import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import conflict, unprocessable
from ..models import Category, Space
from .common import FINAL_ACTIONS, Actor, iso, utcnow

DEFAULT_CLARIFY = "我想确认一下，您是想要以下哪一项？"
DEFAULT_FINAL_MSG = "抱歉，这个问题我暂时处理不了。"


def candidate_min(threshold: float) -> float:
    return round(threshold / 2 + 1e-9, 2)


def recognition(sp: Space, with_version: bool = False) -> dict:
    d = {
        "threshold": round(sp.threshold, 2),
        "candidate_min": candidate_min(sp.threshold),
        "max_candidates": sp.max_candidates,
        "max_clarify": sp.max_clarify,
        "max_slot_ask": sp.max_slot_ask,
        "tool_retry": sp.tool_retry,
        "final_action": sp.final_action,
        "final_action_label": FINAL_ACTIONS.get(sp.final_action, sp.final_action),
        "clarify_prompt": sp.clarify_prompt,
        "final_message": sp.final_message if sp.final_action == "decline" else None,
    }
    if with_version:
        d["version"] = sp.version
        d["updated_at"] = iso(sp.recognition_updated_at)
        d["updated_by"] = sp.recognition_updated_by
    return d


def default_recognition(space_id: str) -> dict:
    return {"threshold": 0.6, "max_clarify": 2, "max_slot_ask": 2, "tool_retry": 1,
            "final_action": "handoff_human" if space_id == "cs" else "general_assistant",
            "clarify_prompt": DEFAULT_CLARIFY, "final_message": None}


def _check_int(errs, name, v, lo, hi, label):
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
        errs.append({"field": name, "message": f"{label}应为 {lo}～{hi} 的整数"})


def update_recognition(db: Session, sp: Space, payload: dict, actor: Actor) -> bool:
    """只改传入的字段；有改动时 version +1。返回是否有改动。"""
    errs = []
    allowed = {"threshold", "max_clarify", "max_slot_ask", "tool_retry", "final_action",
               "clarify_prompt", "final_message"}
    unknown = [k for k in payload if k not in allowed and k not in ("candidate_min", "max_candidates",
                                                                    "final_action_label", "version")]
    if unknown:
        errs.append({"field": ",".join(unknown), "message": "不支持的字段"})
    if "candidate_min" in payload:
        errs.append({"field": "candidate_min", "message": "candidate_min 由平台按 threshold ÷ 2 计算，不可写"})
    th = payload.get("threshold")
    if th is not None:
        if isinstance(th, bool) or not isinstance(th, (int, float)) or not (0 <= th <= 1):
            errs.append({"field": "threshold", "message": "识别阈值应在 0～1 之间"})
        elif abs(round(th / 0.05) * 0.05 - th) > 1e-6:
            errs.append({"field": "threshold", "message": "识别阈值步长为 0.05"})
    _check_int(errs, "max_clarify", payload.get("max_clarify"), 0, 5, "澄清最多次数")
    _check_int(errs, "max_slot_ask", payload.get("max_slot_ask"), 0, 5, "槽位追问最多次数")
    _check_int(errs, "tool_retry", payload.get("tool_retry"), 0, 3, "工具失败重试次数")
    fa = payload.get("final_action", sp.final_action)
    if fa not in FINAL_ACTIONS:
        errs.append({"field": "final_action", "message": "最终动作应为 handoff_human / general_assistant / decline"})
    if "clarify_prompt" in payload and not str(payload.get("clarify_prompt") or "").strip():
        errs.append({"field": "clarify_prompt", "message": "澄清话术必填"})
    fm = payload.get("final_message", sp.final_message)
    if fa == "decline" and not str(fm or "").strip():
        errs.append({"field": "final_message", "message": "最终动作为「告知无法处理」时必须填写回复话术"})
    if errs:
        raise unprocessable("空间设置校验不通过", details=errs)

    before = recognition(sp)
    if th is not None:
        sp.threshold = round(float(th), 2)
    for k in ("max_clarify", "max_slot_ask", "tool_retry"):
        if payload.get(k) is not None:
            setattr(sp, k, int(payload[k]))
    sp.final_action = fa
    if "clarify_prompt" in payload:
        sp.clarify_prompt = str(payload["clarify_prompt"]).strip()
    if "final_message" in payload:
        sp.final_message = (str(payload["final_message"]).strip() or None) if payload["final_message"] is not None else None
    after = recognition(sp)
    if before == after and not (fa == "decline" and "final_message" in payload and sp.final_message != fm):
        db.rollback()
        return False
    sp.version += 1
    sp.recognition_updated_at = utcnow()
    sp.recognition_updated_by = actor.name
    changed = {k: [before[k], after[k]] for k in after if before.get(k) != after.get(k)}
    audit.record(db, actor, "recognition.update", "space", sp.id, "修改空间设置", sp.id,
                 {"changed": changed, "version": sp.version})
    return True


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
    bot = (data.get("bot") or "").strip()
    prefix = (data.get("code_prefix") or "").strip()
    errs = []
    if not SPACE_ID_RE.match(sid):
        errs.append({"field": "id", "message": "空间 ID 为 2～32 位小写字母/数字/下划线，字母开头"})
    if not name:
        errs.append({"field": "name", "message": "名称必填"})
    if not bot:
        errs.append({"field": "bot", "message": "对应 Bot / 智能体必填"})
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
               final_action="general_assistant", clarify_prompt=DEFAULT_CLARIFY, max_candidates=3,
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
