from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import conflict, not_found, unprocessable
from ..models import SlotDef
from . import intents as intent_svc
from .common import SLOT_NAME_RE, SLOT_TYPES, Actor, split_list


def to_dict(s: SlotDef, used_by: list[str] | None = None) -> dict:
    d = {"name": s.name, "display_name": s.display_name, "type": s.type, "options": list(s.options or []),
         "default_ask": s.default_ask}
    if used_by is not None:
        d["used_by"] = used_by
    return d


def list_slots(db: Session, space_id: str, q: str | None = None) -> list[SlotDef]:
    stmt = select(SlotDef).where(SlotDef.space_id == space_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where((SlotDef.name.ilike(like)) | (SlotDef.display_name.ilike(like)))
    return list(db.scalars(stmt.order_by(SlotDef.position, SlotDef.id)))


def used_by_map(db: Session, space_id: str) -> dict[str, list[str]]:
    from ..models import Intent
    m: dict[str, list[str]] = {}
    for code, slots in db.execute(select(Intent.code, Intent.slots).where(Intent.space_id == space_id)
                                  .order_by(Intent.position)):
        for s in slots or []:
            m.setdefault(s.get("name"), []).append(code)
    return m


def _validate(data: dict, creating: bool) -> dict:
    errs = []
    out = {}
    if creating:
        name = (data.get("name") or "").strip()
        if not SLOT_NAME_RE.match(name) or len(name) > 64:
            errs.append({"field": "name", "message": "英文名为小写英文/数字/下划线，以字母开头"})
        out["name"] = name
    if creating or "display_name" in data:
        dn = (data.get("display_name") or "").strip()
        if not dn:
            errs.append({"field": "display_name", "message": "显示名必填"})
        out["display_name"] = dn
    if creating or "type" in data:
        t = data.get("type") or "文本"
        if t not in SLOT_TYPES:
            errs.append({"field": "type", "message": "类型应为 文本 / 数字 / 日期 / 枚举 / 文件 / 图片"})
        out["type"] = t
    if "options" in data or creating:
        out["options"] = split_list(data.get("options") or [])
    if creating or "default_ask" in data:
        a = (data.get("default_ask") or "").strip()
        if not a:
            errs.append({"field": "default_ask", "message": "默认追问话术必填"})
        out["default_ask"] = a
    if errs:
        raise unprocessable("槽位校验不通过", details={"errors": errs})
    return out


def create(db: Session, space_id: str, data: dict, actor: Actor) -> SlotDef:
    v = _validate(data, True)
    if v["type"] == "枚举" and not v["options"]:
        raise unprocessable("枚举类型必须填写可选值", details={"errors": [{"field": "options", "message": "枚举类型必须填写可选值"}]})
    if v["type"] != "枚举":
        v["options"] = []
    if db.scalar(select(SlotDef).where(SlotDef.space_id == space_id, SlotDef.name == v["name"])):
        raise conflict(f"本空间已有槽位 {v['name']}", "SLOT_EXISTS")
    pos = (db.scalar(select(func.max(SlotDef.position)).where(SlotDef.space_id == space_id)) or 0) + 1
    s = SlotDef(space_id=space_id, position=pos, **v)
    db.add(s)
    audit.record(db, actor, "slot.create", "slot", f"{space_id}:{v['name']}", f"新建槽位 {v['display_name']}（{v['name']}）",
                 space_id, to_dict(s))
    return s


def get(db: Session, space_id: str, name: str) -> SlotDef:
    s = db.scalar(select(SlotDef).where(SlotDef.space_id == space_id, SlotDef.name == name))
    if not s:
        raise not_found(f"本空间没有槽位 {name}", "SLOT_NOT_FOUND")
    return s


def update(db: Session, space_id: str, name: str, data: dict, actor: Actor) -> SlotDef:
    if "name" in data and data["name"] != name:
        raise unprocessable("英文名创建后不可修改")
    s = get(db, space_id, name)
    v = _validate(data, False)
    t = v.get("type", s.type)
    opts = v.get("options", s.options)
    if t == "枚举" and not opts:
        raise unprocessable("枚举类型必须填写可选值", details={"errors": [{"field": "options", "message": "枚举类型必须填写可选值"}]})
    before = to_dict(s)
    for k, val in v.items():
        setattr(s, k, val)
    if t != "枚举":
        s.options = []
    after = to_dict(s)
    if before != after:
        audit.record(db, actor, "slot.update", "slot", f"{space_id}:{name}", f"修改槽位 {name}", space_id,
                     {"before": before, "after": after})
    return s


def delete(db: Session, space_id: str, name: str, actor: Actor) -> None:
    s = get(db, space_id, name)
    users = intent_svc.users_of_slot(db, space_id, name)
    if users:
        raise conflict(f"槽位 {name} 正被 {len(users)} 个意图使用，请先在这些意图中移除", "SLOT_IN_USE",
                       used_by=[u.code for u in users],
                       intents=[{"code": u.code, "name": u.name, "status": u.status} for u in users])
    db.delete(s)
    audit.record(db, actor, "slot.delete", "slot", f"{space_id}:{name}", f"删除槽位 {name}", space_id, to_dict(s))


def ensure_text_slot(db: Session, space_id: str, name: str, actor: Actor) -> SlotDef:
    """导入时遇到本空间没有的槽位：自动新建为文本槽位。"""
    pos = (db.scalar(select(func.max(SlotDef.position)).where(SlotDef.space_id == space_id)) or 0) + 1
    s = SlotDef(space_id=space_id, name=name, display_name=name, type="文本", options=[],
                default_ask=f"请补充{name}", position=pos)
    db.add(s)
    audit.record(db, actor, "slot.auto_create", "slot", f"{space_id}:{name}", f"导入时自动新建文本槽位 {name}", space_id)
    return s
