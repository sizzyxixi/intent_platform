import secrets

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session

from .. import audit
from ..deps import SCOPES
from ..errors import conflict, not_found, unprocessable
from ..models import ApiKey, IdempotencyRecord, RateCounter, Space
from ..security import new_api_token, token_hash, token_hint
from .common import Actor, iso, utcnow


def to_dict(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "owner": k.owner, "key_hint": k.token_hint, "spaces": list(k.spaces),
            "scopes": list(k.scopes), "status": k.status, "created_at": iso(k.created_at),
            "last_used_at": iso(k.last_used_at), "revoked_at": iso(k.revoked_at)}


def create(db: Session, data: dict, actor: Actor, key_id: str | None = None, token: str | None = None) -> tuple[ApiKey, str]:
    name = (data.get("name") or "").strip()
    spaces = [s for s in (data.get("spaces") or []) if s]
    scopes = [s for s in (data.get("scopes") or []) if s]
    errs = []
    if not name:
        errs.append({"field": "name", "message": "名称必填"})
    if not spaces:
        errs.append({"field": "spaces", "message": "至少绑定 1 个空间"})
    known = {s.id for s in db.scalars(select(Space))}
    if any(s not in known for s in spaces):
        errs.append({"field": "spaces", "message": "绑定的空间不存在"})
    if not scopes or any(s not in SCOPES for s in scopes):
        errs.append({"field": "scopes", "message": "权限为 " + " / ".join(SCOPES) + "，至少选 1 项"})
    if errs:
        raise unprocessable("API Key 校验不通过", details={"errors": errs})
    kid = key_id or ("ak_" + secrets.token_hex(2))
    while db.get(ApiKey, kid):
        kid = "ak_" + secrets.token_hex(3)
    tok = token or new_api_token()
    k = ApiKey(id=kid, name=name, owner=(data.get("owner") or actor.name).strip(), token_hash=token_hash(tok),
               token_hint=token_hint(kid), spaces=spaces, scopes=[s for s in SCOPES if s in scopes], status="有效")
    db.add(k)
    audit.record(db, actor, "api_key.create", "api_key", kid, f"新建 API Key「{name}」", None,
                 {"spaces": spaces, "scopes": scopes})
    return k, tok


def revoke(db: Session, kid: str, actor: Actor) -> ApiKey:
    k = db.get(ApiKey, kid)
    if not k:
        raise not_found("API Key 不存在", "KEY_NOT_FOUND")
    if k.status != "已吊销":
        k.status = "已吊销"
        k.revoked_at = utcnow()
        audit.record(db, actor, "api_key.revoke", "api_key", kid, f"吊销 API Key「{k.name}」")
    return k


def hard_delete(db: Session, kid: str, actor: Actor) -> dict:
    """彻底删除已吊销的 Key（不留表记录）。审计日志保留，其中的 Key id 作为文本。"""
    k = db.get(ApiKey, kid)
    if not k:
        raise not_found("API Key 不存在", "KEY_NOT_FOUND")
    if k.status != "已吊销":
        raise conflict("请先吊销再删除，有效 Key 不能直接删除", "KEY_NOT_REVOKED")
    snap = to_dict(k)
    db.execute(sa_delete(IdempotencyRecord).where(IdempotencyRecord.api_key_id == kid))
    db.execute(sa_delete(RateCounter).where(RateCounter.api_key_id == kid))
    db.delete(k)
    db.flush()
    audit.record(db, actor, "api_key.delete", "api_key", kid,
                 f"删除 API Key「{snap['name']}」", None,
                 {"name": snap["name"], "key_hint": snap["key_hint"],
                  "spaces": snap["spaces"], "scopes": snap["scopes"]})
    return snap

