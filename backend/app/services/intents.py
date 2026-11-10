from datetime import datetime

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import conflict, not_found, unprocessable
from ..models import Intent, IntentLog, Space, Tool
from . import spaces as space_svc
from .common import INTENT_CODE_RE, STATUSES, Actor, iso, utcnow
from .validation import RefIndex, effective_ask, validate

FIELD_LABEL = {"name": "名称", "l2": "L2", "description": "意图描述", "examples": "例句", "slots": "槽位",
               "kb_refs": "关联知识", "tools": "关联工具", "status": "状态"}


def as_dict(it: Intent) -> dict:
    return {"code": it.code, "name": it.name, "l1": it.l1, "l2": it.l2, "description": it.description,
            "examples": list(it.examples or []), "slots": list(it.slots or []), "kb_refs": list(it.kb_refs or []),
            "tools": list(it.tools or []), "space_id": it.space_id, "status": it.status}


def add_log(it: Intent, by: str, message: str, at: datetime | None = None) -> None:
    it.logs.append(IntentLog(by=by, message=message, at=at or utcnow()))


def get(db: Session, code: str, space_id: str | None = None) -> Intent:
    it = db.get(Intent, code)
    if not it or (space_id and it.space_id != space_id):
        raise not_found(f"意图 {code} 不存在", "INTENT_NOT_FOUND")
    return it


def query(db: Session, space_id: str, l1=None, l2=None, status=None, q=None, updated_after=None):
    stmt = select(Intent).where(Intent.space_id == space_id)
    if l1:
        stmt = stmt.where(Intent.l1 == l1)
    if l2:
        stmt = stmt.where(Intent.l2 == l2)
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


def l1_counts(db: Session, space_id: str) -> dict[str, int]:
    rows = db.execute(select(Intent.l1, func.count()).where(Intent.space_id == space_id).group_by(Intent.l1))
    return {a: n for a, n in rows}


def api_summary(it: Intent) -> dict:
    return {"code": it.code, "name": it.name, "l1": it.l1, "l2": it.l2, "description": it.description,
            "status": it.status, "example_count": len(it.examples or []), "updated_at": iso(it.updated_at),
            "space": it.space_id}


def api_detail(it: Intent) -> dict:
    d = api_summary(it)
    d.update({"examples": list(it.examples or []),
              "slots": [{"name": s["name"], "required": bool(s.get("required")), "ask": s.get("ask") or ""}
                        for s in it.slots or []],
              "kb_refs": list(it.kb_refs or []),
              "tools": [{"tool": b["tool"], "param_map": b.get("param_map") or {}} for b in it.tools or []]})
    return d


def admin_detail(it: Intent, idx: RefIndex) -> dict:
    d = api_detail(it)
    defs = idx.slots(it.space_id)
    for s in d["slots"]:
        sd = defs.get(s["name"])
        s["default_ask"] = sd.default_ask if sd else ""
        s["display_name"] = sd.display_name if sd else s["name"]
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
        if not t:
            e.append(f"工具未注册：{n}")
        elif t.maintenance:
            e.append(f"工具 {n} 是维护类平台内置工具，不能在意图中使用")
    if len(set(tnames)) != len(tnames):
        e.append("工具重复关联")
    return e


def create(db: Session, sp: Space, data: dict, actor: Actor) -> Intent:
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    l1, l2 = (data.get("l1") or "").strip(), (data.get("l2") or "").strip()
    errs = []
    if not INTENT_CODE_RE.match(code):
        errs.append("编码格式应为 前缀.大类.意图（大写，如 B.STOCK.AVAIL）")
    elif not code.startswith(f"{sp.code_prefix}.{l1}."):
        errs.append(f"编码应以 {sp.code_prefix}.{l1}. 开头")
    if not name:
        errs.append("名称不能为空")
    m = space_svc.l1_map(db, sp.id)
    if l1 not in m:
        errs.append(f"L1「{l1}」不存在")
    elif l2 not in [b.name for b in m[l1].l2s]:
        errs.append(f"L2「{l2}」不存在")
    if errs:
        raise unprocessable("新建意图校验不通过", problems=errs)
    ex = db.get(Intent, code)
    if ex:
        raise conflict(f"编码 {code} 已被使用" + ("" if ex.space_id == sp.id else "（其他空间）"), "CODE_EXISTS")
    if db.scalar(select(Intent).where(Intent.space_id == sp.id, Intent.name == name)):
        raise conflict(f"本空间已有同名意图「{name}」", "DUPLICATE_NAME")
    pos = (db.scalar(select(func.max(Intent.position)).where(Intent.space_id == sp.id)) or 0) + 1
    it = Intent(code=code, space_id=sp.id, name=name, l1=l1, l2=l2, description="", examples=[], slots=[],
                kb_refs=[], tools=[], status="草稿", owner=actor.name, position=pos, updated_at=utcnow())
    add_log(it, actor.name, "新建意图（草稿）")
    db.add(it)
    audit.record(db, actor, "intent.create", "intent", code, f"新建意图 {code} {name}", sp.id)
    return it


def update(db: Session, it: Intent, data: dict, actor: Actor) -> tuple[Intent, list[str]]:
    """控制台保存（编辑抽屉）。返回 (intent, warnings)。启用时不通过校验抛 422。"""
    if "code" in data and data["code"] != it.code:
        raise unprocessable("编码创建后不可修改")
    if "l1" in data and data["l1"] != it.l1:
        raise unprocessable("L1 不可修改")
    idx = RefIndex(db)
    before = as_dict(it)
    new = dict(before)
    if "name" in data:
        new["name"] = (data.get("name") or "").strip()
    if "l2" in data:
        l1 = space_svc.l1_map(db, it.space_id).get(it.l1)
        if not l1 or data["l2"] not in [b.name for b in l1.l2s]:
            raise unprocessable(f"L2「{data['l2']}」不存在")
        new["l2"] = data["l2"]
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
    for k in ("name", "l2", "description", "examples", "slots", "kb_refs", "tools", "status"):
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
    warnings = []
    if len(new["description"]) < 15:
        warnings.append("意图描述少于 15 个字，启用前需补全")
    return it, warnings


def set_status(db: Session, it: Intent, status: str, actor: Actor) -> Intent:
    if status not in STATUSES:
        raise unprocessable("状态应为 草稿 / 启用 / 停用")
    if status == it.status:
        return it
    if status == "启用":
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
