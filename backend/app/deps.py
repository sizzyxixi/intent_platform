"""鉴权依赖：控制台用户（会话 Cookie / Bearer JWT）与开放接口 API Key。"""
import time
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .errors import ApiError, forbidden, not_found, request_id
from .models import ApiKey, Space, User
from .security import decode_session_token, token_hash
from .services.common import Actor, utcnow


# ---------------- 控制台 ----------------
def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(get_settings().session_cookie)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer ") and not auth[7:].startswith("ipk_"):
        token = auth[7:].strip()
    if not token:
        raise ApiError(401, "UNAUTHORIZED", "请先登录")
    p = decode_session_token(token)
    if not p:
        raise ApiError(401, "UNAUTHORIZED", "登录已过期，请重新登录")
    u = db.get(User, int(p["sub"]))
    if not u or not u.is_active or u.token_version != p.get("ver"):
        raise ApiError(401, "UNAUTHORIZED", "登录已失效，请重新登录")
    request.state.user = u
    return u


def user_actor(request: Request, u: User) -> Actor:
    return Actor("user", str(u.id), u.display_name, role=u.role, ip=_client_ip(request),
                 request_id=request_id(request))


class Role:
    """require(Role.EDITOR) => editor 或 admin 可用。"""
    ADMIN = ("admin",)
    EDITOR = ("admin", "editor")
    VIEWER = ("admin", "editor", "viewer")


def require(roles: tuple[str, ...]):
    def dep(request: Request, u: User = Depends(current_user)) -> Actor:
        if u.role not in roles:
            raise forbidden("当前角色无权执行此操作", "ROLE_FORBIDDEN", role=u.role, required=list(roles))
        return user_actor(request, u)
    return dep


def get_space_or_404(db: Session, space_id: str) -> Space:
    sp = db.get(Space, space_id)
    if not sp:
        raise not_found(f"业务空间 {space_id} 不存在", "SPACE_NOT_FOUND")
    return sp


# ---------------- 开放接口 API Key ----------------
SCOPES = ("意图:读", "意图:写", "知识:读", "知识:写", "工具:读")


@dataclass
class KeyCtx:
    key: ApiKey
    actor: Actor


def _rate_limit(db: Session, key_id: str) -> None:
    limit = get_settings().rate_limit_per_minute
    if limit <= 0:
        return
    now = time.time()
    window = int(now // 60)
    cnt = db.execute(text(
        "INSERT INTO rate_counters (api_key_id, \"window\", count) VALUES (:k, :w, 1) "
        "ON CONFLICT (api_key_id, \"window\") DO UPDATE SET count = rate_counters.count + 1 RETURNING count"),
        {"k": key_id, "w": window}).scalar_one()
    if window % 10 == 0:
        db.execute(text("DELETE FROM rate_counters WHERE \"window\" < :w"), {"w": window - 5})
    db.commit()
    if cnt > limit:
        retry = max(1, int(60 - (now % 60)))
        raise ApiError(429, "RATE_LIMITED", f"每个 Key 每分钟最多 {limit} 次请求，请稍后重试",
                       {"retry_after": retry}, headers={"Retry-After": str(retry)})


def api_key(scope: str | None):
    def dep(request: Request, db: Session = Depends(get_db)) -> KeyCtx:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise ApiError(401, "UNAUTHORIZED", "缺少 API Key（Authorization: Bearer ipk_xxx）")
        tok = auth[7:].strip()
        k = db.scalar(select(ApiKey).where(ApiKey.token_hash == token_hash(tok)))
        if not k:
            raise ApiError(401, "UNAUTHORIZED", "API Key 无效")
        if k.status != "有效":
            raise ApiError(401, "KEY_REVOKED", "API Key 已吊销")
        _rate_limit(db, k.id)
        k.last_used_at = utcnow()
        db.commit()
        if scope and scope not in (k.scopes or []):
            raise forbidden(f"该 Key 没有「{scope}」权限", "SCOPE_FORBIDDEN", required=scope)
        actor = Actor("api_key", k.id, k.name, ip=_client_ip(request), request_id=request_id(request))
        request.state.api_key = k
        return KeyCtx(k, actor)
    return dep


def resolve_space(request: Request, db: Session, ctx: KeyCtx, explicit: str | None = None) -> Space:
    sid = explicit or request.headers.get("x-space-id") or request.query_params.get("space")
    bound = list(ctx.key.spaces or [])
    if not sid:
        if not bound:
            raise forbidden("该 Key 未绑定任何业务空间", "SPACE_FORBIDDEN")
        sid = bound[0]
    if sid not in bound:
        raise forbidden(f"该 Key 未绑定空间 {sid}", "SPACE_FORBIDDEN", space=sid, bound_spaces=bound)
    return get_space_or_404(db, sid)


def cleanup_idempotency(db: Session) -> None:
    from datetime import timedelta
    from .models import IdempotencyRecord
    db.execute(delete(IdempotencyRecord).where(
        IdempotencyRecord.created_at < utcnow() - timedelta(hours=get_settings().idempotency_ttl_hours)))
