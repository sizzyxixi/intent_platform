"""动态分类层级。

- 每个空间在 spaces.levels 里配置层级名称，最后一个固定是「意图」层；前面 0～5 个是分类层。
- 分类节点存在 categories 邻接表（parent_id + depth，depth 从 1 开始）；意图挂在最末一层分类（叶子）上。
- 层级调整（插入一层 / 删除一层 / 改名）先 preview（在 SAVEPOINT 里真实执行一遍再回滚，得到影响面），
  确认后再执行；执行会 version +1、写审计和受影响意图的操作记录。
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import audit
from ..errors import conflict, not_found, unprocessable
from ..models import Category, Intent, IntentLog, Space
from .common import CATEGORY_CODE_RE, MAX_CATEGORY_LEVELS, Actor, utcnow

UNCATEGORIZED = "未分类"
INTENT_LEVEL_DEFAULT = "意图"


# ---------------- 读 ----------------
class Tax:
    """一个空间的分类树快照（一次查询载入全部节点）。"""

    def __init__(self, db: Session, sp: Space):
        self.db, self.sp = db, sp
        self.levels: list[str] = list(sp.levels or [INTENT_LEVEL_DEFAULT])
        self.n = len(self.levels) - 1  # 分类层数
        self.nodes: dict[int, Category] = {
            c.id: c for c in db.scalars(select(Category).where(Category.space_id == sp.id)
                                        .order_by(Category.depth, Category.position, Category.id))}
        self.children: dict[int | None, list[Category]] = defaultdict(list)
        for c in self.nodes.values():
            self.children[c.parent_id].append(c)
        for v in self.children.values():
            v.sort(key=lambda c: (c.position, c.id))

    def roots(self) -> list[Category]:
        return self.children[None]

    def chain(self, cid: int | None) -> list[Category]:
        out = []
        while cid is not None and cid in self.nodes:
            c = self.nodes[cid]
            out.append(c)
            cid = c.parent_id
        return out[::-1]

    def path(self, cid: int | None) -> list[dict]:
        return [{"code": c.code, "name": c.name} for c in self.chain(cid)]

    def path_str(self, cid: int | None) -> str:
        return " / ".join(c.name for c in self.chain(cid)) or "（无分类）"

    def leaves(self) -> list[Category]:
        out = []

        def walk(pid):
            for c in self.children[pid]:
                if c.depth == self.n:
                    out.append(c)
                else:
                    walk(c.id)
        if self.n:
            walk(None)
        return out

    def leaf_options(self) -> list[dict]:
        return [{"id": c.id, "path": self.path(c.id), "label": self.path_str(c.id)} for c in self.leaves()]

    def descendants(self, cid: int) -> list[int]:
        out, stack = [], [cid]
        while stack:
            x = stack.pop()
            out.append(x)
            stack += [c.id for c in self.children[x]]
        return out

    def root_of(self, cid: int | None) -> Category | None:
        ch = self.chain(cid)
        return ch[0] if ch else None

    def match_child(self, pid: int | None, seg: str) -> Category | None:
        s = str(seg or "").strip()
        if not s:
            return None
        for c in self.children[pid]:
            if (c.code and c.code == s.upper()) or c.name == s:
                return c
        return None

    def resolve(self, segs: list[str]) -> tuple[Category | None, int]:
        """按「编码或名称」逐层匹配。返回 (叶子节点或 None, 成功匹配的层数)。"""
        pid, node = None, None
        for i, s in enumerate(segs):
            node = self.match_child(pid, s)
            if not node:
                return None, i
            pid = node.id
        if node is None or node.depth != self.n or len(segs) != self.n:
            return None, len(segs)
        return node, len(segs)

    def counts(self) -> dict[int, int]:
        """每个节点下（含子孙）的意图数。"""
        direct = dict(self.db.execute(select(Intent.category_id, func.count()).where(
            Intent.space_id == self.sp.id, Intent.category_id.is_not(None)).group_by(Intent.category_id)).all())
        total: dict[int, int] = {}

        def walk(c: Category) -> int:
            n = direct.get(c.id, 0) + sum(walk(x) for x in self.children[c.id])
            total[c.id] = n
            return n
        for r in self.roots():
            walk(r)
        return total

    def tree(self, with_counts: bool = True, with_ids: bool = True) -> list[dict]:
        cnt = self.counts() if with_counts else {}

        def node(c: Category) -> dict:
            d = {"code": c.code, "name": c.name, "depth": c.depth}
            if with_ids:
                d["id"] = c.id
            if with_counts:
                d["count"] = cnt.get(c.id, 0)
            d["children"] = [node(x) for x in self.children[c.id]]
            return d
        return [node(r) for r in self.roots()]


def levels_def(sp: Space) -> dict:
    lv = list(sp.levels or [INTENT_LEVEL_DEFAULT])
    return {"levels": [{"depth": i + 1, "name": n} for i, n in enumerate(lv[:-1])], "intent_level_name": lv[-1]}


def admin_taxonomy(db: Session, sp: Space) -> dict:
    t = Tax(db, sp)
    return {"levels": t.levels, "category_levels": t.n, "tree": t.tree(),
            "unassigned": db.scalar(select(func.count()).select_from(Intent).where(
                Intent.space_id == sp.id, Intent.category_id.is_(None))) or 0}


def public_taxonomy(db: Session, sp: Space) -> dict:
    t = Tax(db, sp)
    return {**levels_def(sp), "categories": t.tree(with_counts=False, with_ids=False)}


def code_rule(t: Tax, cid: int | None) -> str:
    """意图编码必须以此开头。"""
    root = t.root_of(cid)
    return f"{t.sp.code_prefix}.{root.code}." if root is not None and root.code else f"{t.sp.code_prefix}."


def category_input(t: Tax, data: dict) -> tuple[int | None, str | None]:
    """从 category_id / category_path / l1+l2（兼容旧字段）解析叶子节点。返回 (id, 错误)。"""
    if t.n == 0:
        return None, None
    if data.get("category_id") not in (None, ""):
        try:
            cid = int(data["category_id"])
        except (TypeError, ValueError):
            return None, "category_id 无效"
        c = t.nodes.get(cid)
        if not c:
            return None, "分类不存在"
        if c.depth != t.n:
            return None, f"意图只能挂在最末一层分类（{t.levels[t.n - 1]}）下"
        return cid, None
    segs = path_segments(data)
    if not segs:
        return None, "缺少分类路径"
    node, _ = t.resolve(segs)
    if not node:
        return None, f"分类路径「{' / '.join(segs)}」不存在（需要 {t.n} 层：{' / '.join(t.levels[:-1])}）"
    return node.id, None


def path_segments(data: dict) -> list[str]:
    p = data.get("category_path")
    if isinstance(p, str):
        import re
        p = [x for x in re.split(r"\s*[/／>]\s*", p) if x.strip()]
    if isinstance(p, list) and p:
        return [str((x.get("code") or x.get("name")) if isinstance(x, dict) else x).strip() for x in p]
    l1, l2 = str(data.get("l1") or "").strip(), str(data.get("l2") or "").strip()  # 0.5 版字段，兼容
    return [x for x in (l1, l2) if x]


# ---------------- 分类节点增删改 ----------------
def _bump(db: Session, sp: Space) -> None:
    sp.version += 1


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise unprocessable("名称必填", details=[{"field": "name", "message": "名称必填"}])
    if len(name) > 30:
        raise unprocessable("名称最多 30 个字", details=[{"field": "name", "message": "名称最多 30 个字"}])
    return name


def _check_code(db: Session, sp: Space, code, exclude_id: int | None = None) -> str | None:
    code = (code or "").strip().upper() or None
    if code is None:
        return None
    if not CATEGORY_CODE_RE.match(code):
        raise unprocessable("编码为大写字母开头的大写字母 / 数字 / 下划线",
                            details=[{"field": "code", "message": "编码为大写字母开头的大写字母 / 数字 / 下划线"}])
    q = select(Category).where(Category.space_id == sp.id, Category.code == code)
    if exclude_id:
        q = q.where(Category.id != exclude_id)
    if db.scalar(q):
        raise conflict(f"编码 {code} 已存在", "CATEGORY_EXISTS")
    return code


def get_category(db: Session, sp: Space, cid: int) -> Category:
    c = db.get(Category, cid)
    if not c or c.space_id != sp.id:
        raise not_found("分类不存在", "CATEGORY_NOT_FOUND")
    return c


def add_category(db: Session, sp: Space, data: dict, actor: Actor) -> Category:
    t = Tax(db, sp)
    if t.n == 0:
        raise unprocessable("本空间没有分类层，请先在层级设置里添加", "NO_CATEGORY_LEVEL")
    pid = data.get("parent_id")
    parent = get_category(db, sp, int(pid)) if pid not in (None, "") else None
    depth = (parent.depth + 1) if parent else 1
    if depth > t.n:
        raise unprocessable(f"「{parent.name}」已是最末一层分类，下面只能放意图", "CATEGORY_TOO_DEEP")
    name = _check_name(data.get("name"))
    if any(c.name == name for c in t.children[parent.id if parent else None]):
        raise conflict(f"同一层下已有「{name}」", "CATEGORY_EXISTS")
    code = _check_code(db, sp, data.get("code"))
    c = Category(space_id=sp.id, parent_id=parent.id if parent else None, depth=depth, code=code, name=name,
                 position=len(t.children[parent.id if parent else None]))
    db.add(c)
    db.flush()
    _bump(db, sp)
    audit.record(db, actor, "taxonomy.add", "category", str(c.id),
                 f"新增{t.levels[depth - 1]}「{Tax(db, sp).path_str(c.id)}」", sp.id, {"code": code})
    return c


def update_category(db: Session, sp: Space, c: Category, data: dict, actor: Actor) -> Category:
    t = Tax(db, sp)
    before = t.path_str(c.id)
    changed = {}
    if "name" in data:
        name = _check_name(data.get("name"))
        if name != c.name:
            if any(x.name == name and x.id != c.id for x in t.children[c.parent_id]):
                raise conflict(f"同一层下已有「{name}」", "CATEGORY_EXISTS")
            changed["name"] = [c.name, name]
            c.name = name
    if "code" in data:
        code = _check_code(db, sp, data.get("code"), exclude_id=c.id)
        if code != c.code:
            if c.depth == 1 and db.scalar(select(func.count()).select_from(Intent).where(
                    Intent.category_id.in_(t.descendants(c.id)))):
                raise conflict("该分类下已有意图，意图编码依赖一级分类编码，不能修改编码", "CATEGORY_IN_USE")
            changed["code"] = [c.code, code]
            c.code = code
    if not changed:
        return c
    db.flush()
    _bump(db, sp)
    audit.record(db, actor, "taxonomy.update", "category", str(c.id), f"修改分类「{before}」", sp.id,
                 {"changed": changed})
    return c


def delete_category(db: Session, sp: Space, c: Category, actor: Actor) -> None:
    t = Tax(db, sp)
    if t.children[c.id]:
        raise conflict(f"「{c.name}」下还有 {len(t.children[c.id])} 个子分类，不能删除", "CATEGORY_NOT_EMPTY",
                       count=len(t.children[c.id]))
    n = db.scalar(select(func.count()).select_from(Intent).where(Intent.category_id == c.id))
    if n:
        raise conflict(f"「{c.name}」下还有 {n} 个意图，不能删除", "CATEGORY_IN_USE", count=n)
    label = t.path_str(c.id)
    db.delete(c)
    db.flush()
    _bump(db, sp)
    audit.record(db, actor, "taxonomy.delete", "category", str(c.id), f"删除分类「{label}」", sp.id)


# ---------------- 层级调整 ----------------
def _intent_paths(db: Session, sp: Space) -> dict[str, tuple[str, str]]:
    t = Tax(db, sp)
    return {code: (t.path_str(cid), status) for code, cid, status in db.execute(
        select(Intent.code, Intent.category_id, Intent.status).where(Intent.space_id == sp.id))}


def _insert_level(db: Session, sp: Space, index: int, n: int) -> dict:
    """在第 index 层之前插入新层（0 = 最上面，n = 紧挨意图层之上）。每个上级节点下建一个「未分类」接住原有子节点。"""
    created = 0
    parents = [None] if index == 0 else list(db.scalars(
        select(Category.id).where(Category.space_id == sp.id, Category.depth == index)))
    db.execute(update(Category).where(Category.space_id == sp.id, Category.depth > index)
               .values(depth=Category.depth + 1))
    for pid in parents:
        u = Category(space_id=sp.id, parent_id=pid, depth=index + 1, code=None,
                     name=f"__new_{pid}_{utcnow().timestamp()}", position=0)
        db.add(u)
        db.flush()
        q = update(Category).where(Category.space_id == sp.id, Category.id != u.id)
        q = q.where(Category.parent_id.is_(None)) if pid is None else q.where(Category.parent_id == pid)
        db.execute(q.values(parent_id=u.id))
        if index == n:  # 原来的叶子 → 意图跟着下沉到「未分类」
            iq = update(Intent).where(Intent.space_id == sp.id)
            iq = iq.where(Intent.category_id.is_(None)) if pid is None else iq.where(Intent.category_id == pid)
            db.execute(iq.values(category_id=u.id))
        u.name = UNCATEGORIZED
        db.flush()
        created += 1
    return {"created": created, "merged": [], "dropped_codes": []}


def _remove_level(db: Session, sp: Space, depth: int, n: int) -> dict:
    """删除第 depth 层：该层节点的子节点 / 意图并入上级；同一上级下重名的子节点合并。"""
    merged, dropped = [], []
    xs = list(db.scalars(select(Category).where(Category.space_id == sp.id, Category.depth == depth)
                         .order_by(Category.position, Category.id)))
    xids = {x.id for x in xs}
    xinfo = {x.id: (x.parent_id, x.name, x.code) for x in xs}
    for x in xs:  # 先改成临时名、释放编码，避免和并上来的子节点撞唯一约束
        x.name = f"__del_{x.id}"
        x.code = None
    db.flush()
    for x in xs:
        pid, xname, xcode = xinfo[x.id]
        if xcode:
            dropped.append({"code": xcode, "name": xname})
        kids = list(db.scalars(select(Category).where(Category.parent_id == x.id)
                               .order_by(Category.position, Category.id)))
        for k in kids:
            _move_or_merge(db, sp, k, pid, xids, merged)
        if depth == n:
            db.execute(update(Intent).where(Intent.category_id == x.id).values(category_id=pid))
        db.flush()
        db.delete(x)
        db.flush()
    db.execute(update(Category).where(Category.space_id == sp.id, Category.depth > depth)
               .values(depth=Category.depth - 1))
    return {"created": 0, "merged": merged, "dropped_codes": dropped}


def _move_or_merge(db: Session, sp: Space, k: Category, new_pid: int | None, skip: set[int], merged: list) -> None:
    q = select(Category).where(Category.space_id == sp.id, Category.name == k.name, Category.id != k.id)
    q = q.where(Category.parent_id.is_(None)) if new_pid is None else q.where(Category.parent_id == new_pid)
    target = next((c for c in db.scalars(q) if c.id not in skip), None)
    if not target:
        k.parent_id = new_pid
        db.flush()
        return
    merged.append({"name": k.name, "into_id": target.id})
    for g in list(db.scalars(select(Category).where(Category.parent_id == k.id).order_by(Category.position))):
        _move_or_merge(db, sp, g, target.id, skip, merged)
    db.execute(update(Intent).where(Intent.category_id == k.id).values(category_id=target.id))
    code = k.code
    k.code = None
    db.flush()
    db.delete(k)
    db.flush()
    if code and not target.code:
        target.code = code
        db.flush()


def _validate_levels(names) -> list[str]:
    if not isinstance(names, list) or not names:
        raise unprocessable("levels 应为层级名称数组（最后一个是意图层）")
    out = [str(x or "").strip() for x in names]
    errs = []
    if any(not x for x in out):
        errs.append("层级名称不能为空")
    if any(len(x) > 10 for x in out):
        errs.append("层级名称最多 10 个字")
    if len(set(out)) != len(out):
        errs.append("层级名称不能重复")
    if len(out) - 1 > MAX_CATEGORY_LEVELS:
        errs.append(f"分类最多 {MAX_CATEGORY_LEVELS} 层（不含意图层）")
    if errs:
        raise unprocessable("层级设置不合法", problems=errs)
    return out


def _plan(sp: Space, payload: dict) -> tuple[list[str], str, int | None]:
    old = list(sp.levels or [INTENT_LEVEL_DEFAULT])
    ch = payload.get("change") or {}
    op = ch.get("op") or "rename"
    idx = ch.get("index")
    n = len(old) - 1
    if op == "insert":
        if not isinstance(idx, int) or not (0 <= idx <= n):
            raise unprocessable(f"插入位置 index 应为 0～{n}")
        if n + 1 > MAX_CATEGORY_LEVELS:
            raise unprocessable(f"分类最多 {MAX_CATEGORY_LEVELS} 层（不含意图层）", "TOO_MANY_LEVELS")
        default = old[:idx] + [str(ch.get("name") or "新层级").strip()] + old[idx:]
    elif op == "remove":
        if not isinstance(idx, int) or not (0 <= idx < n):
            raise unprocessable("只能删除分类层（意图层不能删除）" if idx == n else f"删除位置 index 应为 0～{n - 1}")
        default = old[:idx] + old[idx + 1:]
    elif op == "rename":
        default = old
    else:
        raise unprocessable("change.op 应为 insert / remove / rename")
    names = _validate_levels(payload.get("levels") or default)
    if len(names) != len(default):
        raise unprocessable(f"levels 长度应为 {len(default)}（{op} 之后）")
    return names, op, idx


def _run(db: Session, sp: Space, op: str, idx: int | None) -> dict:
    n = len(sp.levels or [INTENT_LEVEL_DEFAULT]) - 1
    if op == "insert":
        return _insert_level(db, sp, idx, n)
    if op == "remove":
        return _remove_level(db, sp, idx + 1, n)
    return {"created": 0, "merged": [], "dropped_codes": []}


def _impact(db: Session, sp: Space, names: list[str], op: str, before_nodes: int,
            before_paths: dict, res: dict) -> dict:
    after_paths = _intent_paths(db, sp)
    moved = [{"code": c, "before": before_paths[c][0], "after": after_paths[c][0], "status": before_paths[c][1]}
             for c in before_paths if before_paths[c][0] != after_paths.get(c, ("",))[0]]
    t = Tax(db, sp)
    return {
        "op": op, "levels_before": list(sp.levels or []), "levels_after": names,
        "categories_before": before_nodes, "categories_after": len(t.nodes),
        "created_uncategorized": res["created"],
        "merged": [{"name": m["name"], "into": t.path_str(m["into_id"])} for m in res["merged"]],
        "dropped_codes": res["dropped_codes"],
        "intents_moved": len(moved), "enabled_intents_moved": sum(1 for m in moved if m["status"] == "启用"),
        "samples": moved[:20],
    }


def preview_levels(db: Session, sp: Space, payload: dict) -> dict:
    names, op, idx = _plan(sp, payload)
    before_nodes = len(Tax(db, sp).nodes)
    before_paths = _intent_paths(db, sp)
    nested = db.begin_nested()
    try:
        res = _run(db, sp, op, idx)
        db.flush()
        out = _impact(db, sp, names, op, before_nodes, before_paths, res)
    finally:
        nested.rollback()
    db.expire_all()
    out["needs_confirm"] = op in ("insert", "remove")
    return out


def apply_levels(db: Session, sp: Space, payload: dict, actor: Actor) -> dict:
    names, op, idx = _plan(sp, payload)
    if op in ("insert", "remove") and payload.get("confirm") is not True:
        raise unprocessable("插入或删除层级会移动分类和意图，请先预览影响面并以 confirm=true 确认", "CONFIRM_REQUIRED")
    if op == "rename" and names == list(sp.levels or []):
        return {"changed": False, "version": sp.version, "levels": names}
    before_nodes = len(Tax(db, sp).nodes)
    before_paths = _intent_paths(db, sp)
    res = _run(db, sp, op, idx)
    db.flush()
    imp = _impact(db, sp, names, op, before_nodes, before_paths, res)
    old = list(sp.levels or [])
    sp.levels = names
    sp.version += 1
    after_paths = _intent_paths(db, sp)
    moved = {c: (before_paths[c][0], after_paths[c][0]) for c in before_paths if before_paths[c][0] != after_paths[c][0]}
    for c, (b, a) in moved.items():
        db.add(IntentLog(intent_code=c, by=actor.name, message=f"分类层级调整：{b} → {a}", at=utcnow()))
    label = {"insert": "插入层级", "remove": "删除层级", "rename": "修改层级名称"}[op]
    audit.record(db, actor, "taxonomy.levels", "space", sp.id,
                 f"{label}：{' / '.join(old)} → {' / '.join(names)}", sp.id,
                 {k: imp[k] for k in imp if k != "samples"} | {"version": sp.version})
    return {"changed": True, "version": sp.version, "levels": names, "impact": imp}
