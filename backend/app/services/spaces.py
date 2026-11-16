import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import conflict, unprocessable
from ..models import Category, Space
from .common import FINAL_ACTIONS, Actor, iso, utcnow
from . import handling as handling_svc

DEFAULT_CLARIFY = "我想确认一下，您是想要以下哪一项？"
DEFAULT_FINAL_MSG = "抱歉，这个问题我暂时处理不了。"


def candidate_min(threshold: float) -> float:
    return round(threshold / 2 + 1e-9, 2)


RECOGNITION_NOTE = (
    "识别阈值、澄清次数与空间兜底已不再按空间维护（此对象仅保留兼容）。"
    "未命中任何意图时，由使用方智能体自行回答（fallback.mode=llm_free）。"
    "若需要转人工或固定话术，请在分类下配置相应处理方式的意图（如「其他」「闲聊」）。"
)


def recognition(sp: Space, with_version: bool = False) -> dict:
    """对外固定返回 llm_free 兜底；库中历史设置不再使用，也不再可写。"""
    fb = handling_svc.public_handling({"mode": "llm_free"}, fallback=True)
    d = {
        "threshold": 0.6,
        "candidate_min": candidate_min(0.6),
        "max_candidates": 3,
        "max_clarify": 2,
        "max_slot_ask": 2,
        "tool_retry": 1,
        "final_action": "general_assistant",
        "final_action_label": FINAL_ACTIONS.get("general_assistant", "general_assistant"),
        "clarify_prompt": DEFAULT_CLARIFY,
        "final_message": None,
        "fallback": fb,
        "deprecated": True,
        "note": RECOGNITION_NOTE,
    }
    if with_version:
        d["version"] = sp.version
        d["updated_at"] = iso(sp.recognition_updated_at)
        d["updated_by"] = sp.recognition_updated_by
    return d


def default_recognition(space_id: str) -> dict:
    fb = handling_svc.default_fallback(space_id)
    fa, fm = handling_svc.legacy_from(fb)
    return {"threshold": 0.6, "max_clarify": 2, "max_slot_ask": 2, "tool_retry": 1,
            "final_action": fa, "clarify_prompt": DEFAULT_CLARIFY, "final_message": fm,
            "fallback": fb}


def _check_int(errs, name, v, lo, hi, label):
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
        errs.append({"field": name, "message": f"{label}应为 {lo}～{hi} 的整数"})


def update_recognition(db: Session, sp: Space, payload: dict, actor: Actor) -> bool:
    """已废弃：忽略写入，不改库、不升版本。"""
    return False


def update_space(db: Session, sp: Space, data: dict, actor: Actor) -> Space:
    """更新空间名称与描述。bot 字段忽略。描述变化会使运行时配置 version +1。"""
    changed = {}
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise unprocessable("名称必填", details=[{"field": "name", "message": "名称必填"}])
        if name != sp.name:
            other = db.scalar(select(Space).where(Space.name == name, Space.id != sp.id))
            if other:
                raise conflict(f"空间名称「{name}」已存在", "SPACE_EXISTS")
            changed["name"] = [sp.name, name]
            sp.name = name
    if "description" in data:
        desc = str(data.get("description") or "").strip()
        if len(desc) > 2000:
            raise unprocessable("描述最多 2000 个字", details=[{"field": "description", "message": "描述最多 2000 个字"}])
        if desc != (sp.description or ""):
            changed["description"] = [sp.description or "", desc]
            sp.description = desc
    if not changed:
        return sp
    if "description" in changed:
        sp.version += 1
    sp.updated_at = utcnow()
    audit.record(db, actor, "space.update", "space", sp.id, f"修改空间「{sp.name}」", sp.id,
                 {"changed": changed, "version": sp.version})
    return sp


def bump_versions(db: Session, space_ids: set[str] | list[str]) -> None:
    for sid in set(space_ids):
        sp = db.get(Space, sid)
        if sp:
            sp.version += 1


def space_dict(sp: Space) -> dict:
    return {"space_id": sp.id, "name": sp.name, "description": sp.description, "bot": sp.bot,
            "code_prefix": sp.code_prefix}


SPACE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def create_space(db: Session, data: dict, actor: Actor) -> Space:
    sid = (data.get("id") or "").strip()
    name = (data.get("name") or "").strip()
    bot = (data.get("bot") or "").strip()  # 可选，已不再使用；传入则忽略展示
    prefix = (data.get("code_prefix") or "").strip()
    errs = []
    if not SPACE_ID_RE.match(sid):
        errs.append({"field": "id", "message": "空间 ID 为 2～32 位小写字母/数字/下划线，字母开头"})
    if not name:
        errs.append({"field": "name", "message": "名称必填"})
    if not re.match(r"^[A-Z]{1,4}$", prefix):
        errs.append({"field": "code_prefix", "message": "编码前缀为 1～4 位大写字母"})
    if errs:
        raise unprocessable("新建空间校验不通过", details=errs)
    if db.get(Space, sid):
        raise conflict(f"空间 ID {sid} 已存在", "SPACE_EXISTS")
    if db.scalar(select(Space).where(Space.name == name)):
        raise conflict(f"空间名称「{name}」已存在", "SPACE_EXISTS")
    if db.scalar(select(Space).where(Space.code_prefix == prefix)):
        raise conflict(f"编码前缀 {prefix} 已被其他空间使用", "PREFIX_EXISTS")
    pos = (db.scalar(select(func.max(Space.position))) or 0) + 1
    rec = default_recognition(sid)
    sp = Space(id=sid, name=name, description=(data.get("description") or "").strip(), bot=bot,
               code_prefix=prefix, version=1, position=pos, threshold=rec["threshold"],
               max_clarify=rec["max_clarify"], max_slot_ask=rec["max_slot_ask"], tool_retry=rec["tool_retry"],
               final_action=rec["final_action"], final_message=rec.get("final_message"),
               fallback=rec["fallback"], clarify_prompt=DEFAULT_CLARIFY, max_candidates=3,
               levels=["大类", "问题桶", "意图"])
    db.add(sp)
    db.flush()
    root = Category(space_id=sid, parent_id=None, depth=1, code="GEN", name="通用", position=0)
    db.add(root)
    db.flush()
    db.add(Category(space_id=sid, parent_id=root.id, depth=2, code=None, name="默认", position=0))
    audit.record(db, actor, "space.create", "space", sid, f"新建空间「{name}」", sid,
                 {"bot": bot, "code_prefix": prefix})
    return sp
