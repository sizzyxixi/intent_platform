import re
from datetime import datetime

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import conflict, not_found, unprocessable
from ..models import Category, Intent, IntentLog, Space, Tool
from . import handling as handling_svc
from . import spaces as space_svc
from . import taxonomy as tax_svc
from .common import INTENT_CODE_RE, STATUSES, Actor, iso, utcnow
from .validation import RefIndex, effective_ask, validate

FIELD_LABEL = {"name": "名称", "category_id": "分类", "description": "意图描述", "examples": "例句", "slots": "槽位",
               "kb_refs": "关联知识", "tools": "关联工具", "handling": "处理方式", "status": "状态"}


def as_dict(it: Intent) -> dict:
    return {"code": it.code, "name": it.name, "category_id": it.category_id, "description": it.description,
            "examples": list(it.examples or []), "slots": list(it.slots or []), "kb_refs": list(it.kb_refs or []),
            "tools": list(it.tools or []),
            "handling": handling_svc.parse_handling(it.handling or handling_svc.DEFAULT_HANDLING),
            "space_id": it.space_id, "status": it.status}


def add_log(it: Intent, by: str, message: str, at: datetime | None = None) -> None:
    it.logs.append(IntentLog(by=by, message=message, at=at or utcnow()))


def get(db: Session, code: str, space_id: str | None = None) -> Intent:
    it = db.get(Intent, code)
    if not it or (space_id and it.space_id != space_id):
        raise not_found(f"意图 {code} 不存在", "INTENT_NOT_FOUND")
    return it


def category_path(it: Intent) -> list[dict]:
    """[{code, name, description}, ...]，从第 1 层到叶子。父节点走 identity map，列表场景先 preload_categories。"""
    out, c = [], it.category
    while c is not None:
        out.append(tax_svc.path_item(c))
        c = c.parent
    return out[::-1]


def preload_categories(db: Session, space_id: str) -> list[Category]:
    """一次查出空间全部分类，放进 identity map；调用方持有返回值直到序列化结束。"""
    return list(db.scalars(select(Category).where(Category.space_id == space_id)))


def path_filter(db: Session, sp: Space, category: str | None = None, l1: str | None = None,
                l2: str | None = None) -> list[int] | None:
    """开放接口的分类筛选：category 为路径（编码或名称，用 / 分隔，可只写前几层）；l1/l2 为 0.5 版参数（兼容）。
    返回该节点及子孙节点的 id；不传返回 None；路径不存在返回 []。"""
    segs = [x for x in re.split(r"\s*/\s*", category or "") if x] if category else [x for x in (l1, l2) if x]
    if not segs:
        return None
    t = tax_svc.Tax(db, sp)
    pid, node = None, None
    for sgm in segs:
        node = t.match_child(pid, sgm)
        if not node:
            return []
        pid = node.id
    return t.descendants(node.id)


def query(db: Session, space_id: str, category_ids=None, status=None, q=None, updated_after=None):
    stmt = select(Intent).where(Intent.space_id == space_id)
    if category_ids is not None:
        stmt = stmt.where(Intent.category_id.in_(list(category_ids)))
    if status:
        stmt = stmt.where(Intent.status == status)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Intent.code.ilike(like), Intent.name.ilike(like), Intent.description.ilike(like),
                              cast(Intent.examples, String).ilike(like)))
    if updated_after:
        stmt = stmt.where(Intent.updated_at > updated_after)
    return stmt


def list_page(db: Session, space_id: str, page: int = 1, page_size: int = 50, **f):
    stmt = query(db, space_id, **f)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = list(db.scalars(stmt.order_by(Intent.position, Intent.code).offset((page - 1) * page_size).limit(page_size)))
    return total, rows


def api_summary(it: Intent) -> dict:
    return {"code": it.code, "name": it.name, "category_path": category_path(it), "description": it.description,
            "status": it.status, "example_count": len(it.examples or []), "updated_at": iso(it.updated_at),
            "space": it.space_id, "handling": handling_svc.public_handling(it.handling)}


def api_detail(it: Intent) -> dict:
    d = api_summary(it)
    d.update({"examples": list(it.examples or []),
              "slots": [{"name": s["name"], "required": bool(s.get("required")), "ask": s.get("ask") or ""}
                        for s in it.slots or []],
              "kb_refs": list(it.kb_refs or []),
              "tools": [{"tool": b["tool"], "param_map": b.get("param_map") or {}} for b in it.tools or []],
              "handling": handling_svc.public_handling(it.handling)})
    return d


def admin_detail(it: Intent, idx: RefIndex) -> dict:
    d = api_detail(it)
    defs = idx.slots(it.space_id)
    for s in d["slots"]:
        sd = defs.get(s["name"])
        s["default_ask"] = sd.default_ask if sd else ""
        s["display_name"] = sd.display_name if sd else s["name"]
    d["category_id"] = it.category_id
    d["owner"] = it.owner
    d["created_at"] = iso(it.created_at)
    d["logs"] = [{"at": iso(x.at), "by": x.by, "message": x.message}
                 for x in sorted(it.logs, key=lambda x: x.at, reverse=True)]
    d["problems"] = validate(as_dict(it), idx)
    return d


# ---------------- 校验与清洗 ----------------
def _clean_slots(slots) -> list[dict]:
    out = []
    for s in slots or []:
        if not isinstance(s, dict):
            raise unprocessable("槽位格式错误")
        out.append({"name": str(s.get("name") or "").strip(), "required": bool(s.get("required")),
                    "ask": str(s.get("ask") or "").strip()})
    return out


def _clean_refs(refs) -> list[dict]:
    out, seen = [], set()
    for r in refs or []:
        usage = r.get("usage") or "reference"
        if usage not in ("reference", "validate"):
            raise unprocessable("知识用途应为 reference / validate")
        key = (r.get("kb_id"), r.get("doc_id"), r.get("chunk_id"))
        if not all(key):
            raise unprocessable("知识引用需包含 kb_id、doc_id、chunk_id")
        if key in seen:
            continue
        seen.add(key)
        out.append({"kb_id": key[0], "doc_id": key[1], "chunk_id": key[2], "usage": usage})
    return out


def _clean_binds(binds) -> list[dict]:
    out = []
    for b in binds or []:
        pm = {}
        for k, v in (b.get("param_map") or {}).items():
            src = (v or {}).get("source") or "槽位"
            if src not in ("槽位", "常量", "上下文"):
                raise unprocessable("入参取值来源应为 槽位 / 常量 / 上下文")
            pm[k] = {"source": src, "value": str((v or {}).get("value") or "")}
        out.append({"tool": b.get("tool"), "param_map": pm})
    return out


def check_structure(db: Session, it_data: dict, idx: RefIndex) -> list[str]:
    """保存时（任何状态）都必须满足的结构性规则：槽位存在且不重复、工具已注册且非维护类。"""
    e = []
    defs = idx.slots(it_data["space_id"])
    names = [s["name"] for s in it_data["slots"]]
    for n in names:
        if not n:
            e.append("存在未选择的槽位")
        elif n not in defs:
            e.append(f"槽位「{n}」不在本空间槽位列表中")
    if len(set(names)) != len(names):
        e.append("槽位重复")
    tools = idx.tools()
    tnames = [b["tool"] for b in it_data["tools"]]
    for n in tnames:
        t = tools.get(n)
        if not t and n in idx.servers():
            e.append(f"「{n}」是 MCP 服务，不能整体绑定，请下钻选择具体工具")
        elif not t:
            e.append(f"工具未注册：{n}")
        elif t.maintenance:
            e.append(f"工具 {n} 是维护类平台内置工具，不能在意图中使用")
        elif t.type == "skill":
            for b in it_data["tools"]:
                if b["tool"] == n:
                    b["param_map"] = {}  # 技能没有入参映射
    if len(set(tnames)) != len(tnames):
        e.append("工具重复关联")
    msg = handling_svc.check_bindings(it_data.get("handling"), it_data.get("tools"), it_data.get("kb_refs"))
    if msg:
        e.append(msg)
    return e


def create(db: Session, sp: Space, data: dict, actor: Actor) -> Intent:
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    t = tax_svc.Tax(db, sp)
    cid, cerr = tax_svc.category_input(t, data)
    errs = []
    if cerr:
        errs.append(cerr)
    rule = tax_svc.code_rule(t, cid)
    if not INTENT_CODE_RE.match(code):
        errs.append(f"编码格式应为 {rule}XXX（大写字母 / 数字 / 下划线，用点分隔，如 B.PRE.STOCK）")
    elif not cerr and not code.startswith(rule):
        errs.append(f"编码应以 {rule} 开头")
    if not name:
        errs.append("名称不能为空")
    if errs:
        raise unprocessable("新建意图校验不通过", problems=errs)
    ex = db.get(Intent, code)
    if ex:
        raise conflict(f"编码 {code} 已被使用" + ("" if ex.space_id == sp.id else "（其他空间）"), "CODE_EXISTS")
    if db.scalar(select(Intent).where(Intent.space_id == sp.id, Intent.name == name)):
        raise conflict(f"本空间已有同名意图「{name}」", "DUPLICATE_NAME")
    pos = (db.scalar(select(func.max(Intent.position)).where(Intent.space_id == sp.id)) or 0) + 1
    it = Intent(code=code, space_id=sp.id, name=name, category_id=cid, description="", examples=[], slots=[],
                kb_refs=[], tools=[], handling=dict(handling_svc.DEFAULT_HANDLING),
                status="草稿", owner=actor.name, position=pos, updated_at=utcnow())
    add_log(it, actor.name, "新建意图（草稿）")
    db.add(it)
    audit.record(db, actor, "intent.create", "intent", code, f"新建意图 {code} {name}", sp.id)
    return it


def update(db: Session, it: Intent, data: dict, actor: Actor) -> tuple[Intent, list[str]]:
    """控制台保存（编辑抽屉）。返回 (intent, warnings)。启用时不通过校验抛 422。"""
    if "code" in data and data["code"] != it.code:
        raise unprocessable("编码创建后不可修改")
    idx = RefIndex(db)
    before = as_dict(it)
    new = dict(before)
    if "name" in data:
        new["name"] = (data.get("name") or "").strip()
    if any(k in data for k in ("category_id", "category_path")):
        t = tax_svc.Tax(db, db.get(Space, it.space_id))
        cid, cerr = tax_svc.category_input(t, data)
        if cerr:
            raise unprocessable(cerr, problems=[cerr])
        if cid != it.category_id:
            rule = tax_svc.code_rule(t, cid)
            if not it.code.startswith(rule):
                m = f"编码 {it.code} 与目标分类不匹配（应以 {rule} 开头），只能在同一个一级分类内移动"
                raise unprocessable(m, problems=[m])
        new["category_id"] = cid
    if "description" in data:
        new["description"] = (data.get("description") or "").strip()
    if "examples" in data:
        seen, ex = set(), []
        for x in data.get("examples") or []:
            x = str(x).strip()
            if x and x not in seen:
                seen.add(x)
                ex.append(x)
        new["examples"] = ex
    if "slots" in data:
        new["slots"] = _clean_slots(data["slots"])
    if "kb_refs" in data:
        new["kb_refs"] = _clean_refs(data["kb_refs"])
    if "tools" in data:
        new["tools"] = _clean_binds(data["tools"])
    if "handling" in data:
        new["handling"] = handling_svc.parse_handling(data["handling"])
    status = data.get("status", it.status)
    if status not in STATUSES:
        raise unprocessable("状态应为 草稿 / 启用 / 停用")
    new["status"] = status
    if not new["name"]:
        raise unprocessable("名称不能为空", problems=["名称不能为空"])
    st_err = check_structure(db, new, idx)
    if st_err:
        raise unprocessable("保存失败", problems=st_err)
    if new["name"] != before["name"] and db.scalar(select(Intent).where(
            Intent.space_id == it.space_id, Intent.name == new["name"], Intent.code != it.code)):
        raise unprocessable("本空间已有同名意图", details={"problems": [f"本空间已有同名意图「{new['name']}」"]})
    problems = validate(new, idx)
    if status == "启用" and problems:
        raise unprocessable("启用前必填校验未通过", "ENABLE_CHECK_FAILED", problems=problems)
    changed = [k for k in FIELD_LABEL if new.get(k) != before.get(k)]
    if not changed:
        return it, []
    for k in ("name", "category_id", "description", "examples", "slots", "kb_refs", "tools", "handling", "status"):
        setattr(it, k, new[k])
    it.updated_at = utcnow()
    msg = "修改了 " + " / ".join(FIELD_LABEL[k] for k in changed if k != "status")
    if "status" in changed:
        msg = (msg + "；" if len(changed) > 1 else "") + f"状态 {before['status']} → {status}"
    add_log(it, actor.name, msg.strip())
    if before["status"] == "启用" or status == "启用":
        space_svc.bump_versions(db, {it.space_id})
    audit.record(db, actor, "intent.update", "intent", it.code, msg, it.space_id,
                 {"changed": changed, "status": [before["status"], status]})
    return it, []



def preview_bind(db: Session, it: Intent, bind: dict) -> dict:
    """校验绑定（不写入）。返回清洗后的 {tool, param_map}。"""
    incoming = _clean_binds([bind])
    if not incoming or not incoming[0].get("tool"):
        raise unprocessable("请指定要绑定的工具")
    name = incoming[0]["tool"]
    data = as_dict(it)
    data["tools"] = [b for b in (data.get("tools") or []) if b.get("tool") != name] + incoming
    st = check_structure(db, data, RefIndex(db))
    if st:
        raise unprocessable("绑定校验不通过", problems=st)
    return incoming[0]


def bind_tool(db: Session, it: Intent, bind: dict, actor: Actor) -> tuple[Intent, list[str]]:
    """把一个已注册工具绑到意图上（已绑定则更新 param_map）。"""
    cleaned = preview_bind(db, it, bind)
    tools = [b for b in (it.tools or []) if b.get("tool") != cleaned["tool"]]
    tools.append(cleaned)
    return update(db, it, {"tools": tools}, actor)


def unbind_tool(db: Session, it: Intent, name: str, actor: Actor) -> Intent:
    """从意图上解绑工具；未绑定则原样返回。"""
    name = (name or "").strip()
    cur = list(it.tools or [])
    nxt = [b for b in cur if b.get("tool") != name]
    if len(nxt) == len(cur):
        return it
    it, _ = update(db, it, {"tools": nxt}, actor)
    return it


def preview_kb_ref(db: Session, it: Intent, ref: dict) -> dict:
    """校验一条知识关联（不写入）。"""
    incoming = _clean_refs([ref])
    if not incoming:
        raise unprocessable("请指定要关联的知识（kb_id、doc_id、chunk_id，整篇用 chunk_id=\"*\"）")
    r = incoming[0]
    if not RefIndex(db).ref_ok(r):
        raise unprocessable(
            f"知识引用无效：{r['kb_id']}/{r['doc_id']}/{r['chunk_id']}，"
            "请确认知识库、文档和切片存在（整篇用 chunk_id=\"*\"）")
    return r


def bind_knowledge(db: Session, it: Intent, ref: dict, actor: Actor) -> tuple[Intent, list[str]]:
    cleaned = preview_kb_ref(db, it, ref)
    refs = [r for r in (it.kb_refs or [])
            if not (r.get("kb_id") == cleaned["kb_id"] and r.get("doc_id") == cleaned["doc_id"]
                    and r.get("chunk_id") == cleaned["chunk_id"])]
    refs.append(cleaned)
    return update(db, it, {"kb_refs": refs}, actor)


def unbind_knowledge(db: Session, it: Intent, ref: dict, actor: Actor) -> Intent:
    kb_id, doc_id, chunk_id = (ref or {}).get("kb_id"), (ref or {}).get("doc_id"), (ref or {}).get("chunk_id")
    if not (kb_id and doc_id and chunk_id):
        raise unprocessable("解绑知识需要 kb_id、doc_id、chunk_id")
    cur = list(it.kb_refs or [])
    nxt = [r for r in cur if not (r.get("kb_id") == kb_id and r.get("doc_id") == doc_id
                                  and r.get("chunk_id") == chunk_id)]
    if len(nxt) == len(cur):
        return it
    it, _ = update(db, it, {"kb_refs": nxt}, actor)
    return it


def delete(db: Session, it: Intent, actor: Actor) -> dict:
    """硬删除已停用意图（意图日志随 FK CASCADE 删除）。草稿和启用不可删。不升空间版本。"""
    if it.status != "停用":
        raise conflict("只能删除已停用的意图，请先停用后再删除", "INTENT_NOT_DISABLED")
    snap = {"code": it.code, "name": it.name, "status": it.status, "space_id": it.space_id}
    audit.record(db, actor, "intent.delete", "intent", it.code,
                 f"删除意图 {it.code} {it.name}", it.space_id,
                 {"name": it.name, "status": it.status})
    db.delete(it)
    db.flush()
    return snap


def set_status(db: Session, it: Intent, status: str, actor: Actor) -> Intent:
    if status not in STATUSES:
        raise unprocessable("状态应为 草稿 / 启用 / 停用")
    if status == it.status:
        return it
    if status == "启用":
        if it.category_id:
            c = db.get(Category, it.category_id)
            if c is not None and not c.enabled:
                raise unprocessable(f"所属分类「{c.code or c.name}」已停用，不能启用该意图")
        problems = validate(as_dict(it), RefIndex(db))
        if problems:
            raise unprocessable("启用前必填校验未通过", "ENABLE_CHECK_FAILED", problems=problems)
    old = it.status
    it.status = status
    it.updated_at = utcnow()
    add_log(it, actor.name, f"状态 {old} → {status}")
    if old == "启用" or status == "启用":
        space_svc.bump_versions(db, {it.space_id})
    audit.record(db, actor, "intent.status", "intent", it.code, f"状态 {old} → {status}", it.space_id)
    return it


def recheck_enabled(db: Session, intents: list[Intent], by: str, reason: str) -> list[str]:
    """知识 / 工具变化后：已启用意图若不再通过校验，自动转为草稿并记录。返回被降级的编码。"""
    idx = RefIndex(db)
    downgraded = []
    for it in intents:
        if it.status != "启用":
            continue
        if validate(as_dict(it), idx):
            it.status = "草稿"
            it.updated_at = utcnow()
            add_log(it, by, f"{reason}，校验未通过已转为草稿")
            downgraded.append(it.code)
    return downgraded


def users_of_slot(db: Session, space_id: str, name: str) -> list[Intent]:
    return list(db.scalars(select(Intent).where(Intent.space_id == space_id,
                                                Intent.slots.contains([{"name": name}])).order_by(Intent.position)))


def users_of_tool(db: Session, name: str) -> list[Intent]:
    return list(db.scalars(select(Intent).where(Intent.tools.contains([{"tool": name}]))
                           .order_by(Intent.space_id, Intent.position)))


def tool_usage(db: Session) -> dict[str, list[Intent]]:
    """一次查询得到 {工具名: [使用它的意图]}（工具多时避免逐个查询）。"""
    out: dict[str, list[Intent]] = {}
    for it in db.scalars(select(Intent).where(func.jsonb_array_length(Intent.tools) > 0)
                         .order_by(Intent.space_id, Intent.position)):
        for n in dict.fromkeys(b.get("tool") for b in it.tools or []):
            out.setdefault(n, []).append(it)
    return out


def users_of_kb(db: Session, kb_id: str, doc_id: str | None = None) -> list[Intent]:
    pat = {"kb_id": kb_id}
    if doc_id:
        pat["doc_id"] = doc_id
    return list(db.scalars(select(Intent).where(Intent.kb_refs.contains([pat])).order_by(Intent.space_id, Intent.position)))


def effective_slots(it: Intent, idx: RefIndex) -> list[dict]:
    defs = idx.slots(it.space_id)
    return [{"name": s["name"], "required": bool(s.get("required")), "ask": effective_ask(s, defs),
             "ask_overridden": bool((s.get("ask") or "").strip())} for s in it.slots or []]


def tool_maint(db: Session, name: str) -> bool:
    t = db.get(Tool, name)
    return bool(t and t.maintenance)
