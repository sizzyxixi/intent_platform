"""控制台：登录、用户管理（admin）、审计日志（admin）。"""
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ... import audit
from ...config import get_settings
from ...db import get_db
from ...deps import Role, current_user, require, user_actor
from ...errors import ApiError, conflict, not_found, unprocessable
from ...models import AuditLog, User
from ...security import ROLE_LABEL, ROLES, create_session_token, hash_password, verify_password
from ...services.common import Actor, iso, utcnow
from .common import json_body

router = APIRouter(prefix="/api/admin")


def user_dict(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role,
            "role_label": ROLE_LABEL.get(u.role, u.role), "is_active": u.is_active,
            "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at)}


@router.post("/auth/login")
async def login(request: Request, response: Response, db: Session = Depends(get_db)):
    b = await json_body(request)
    u = db.scalar(select(User).where(User.username == (b.get("username") or "").strip()))
    if not u or not u.is_active or not verify_password(b.get("password") or "", u.password_hash):
        raise ApiError(401, "LOGIN_FAILED", "用户名或密码错误")
    u.last_login_at = utcnow()
    tok = create_session_token(u.id, u.token_version)
    s = get_settings()
    response.set_cookie(s.session_cookie, tok, httponly=True, samesite="lax", secure=s.cookie_secure,
                        max_age=s.session_hours * 3600, path="/")
    audit.record(db, Actor("user", str(u.id), u.display_name, ip=request.client.host if request.client else None),
                 "auth.login", "user", str(u.id), "登录")
    db.commit()
    return {"user": user_dict(u), "token": tok}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(get_settings().session_cookie, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(u: User = Depends(current_user)):
    return user_dict(u)


@router.post("/auth/password")
async def change_password(request: Request, u: User = Depends(current_user), db: Session = Depends(get_db)):
    b = await json_body(request)
    if not verify_password(b.get("old_password") or "", u.password_hash):
        raise unprocessable("原密码不正确", "BAD_PASSWORD")
    _check_pw(b.get("new_password") or "")
    u.password_hash = hash_password(b["new_password"])
    u.token_version += 1
    audit.record(db, user_actor(request, u), "user.password", "user", str(u.id), "修改密码")
    db.commit()
    return {"ok": True}


def _check_pw(pw: str):
    if len(pw) < 8:
        raise unprocessable("密码至少 8 位", "WEAK_PASSWORD")


@router.get("/users")
def list_users(actor=Depends(require(Role.ADMIN)), db: Session = Depends(get_db)):
    return [user_dict(u) for u in db.scalars(select(User).order_by(User.id))]


@router.post("/users", status_code=201)
async def create_user(request: Request, actor=Depends(require(Role.ADMIN)), db: Session = Depends(get_db)):
    b = await json_body(request)
    un = (b.get("username") or "").strip()
    if not un or len(un) > 64:
        raise unprocessable("用户名必填")
    if b.get("role") not in ROLES:
        raise unprocessable("角色应为 admin / editor / viewer")
    _check_pw(b.get("password") or "")
    if db.scalar(select(User).where(User.username == un)):
        raise conflict("用户名已存在", "USER_EXISTS")
    u = User(username=un, display_name=(b.get("display_name") or un).strip(), role=b["role"],
             password_hash=hash_password(b["password"]), is_active=True)
    db.add(u)
    db.flush()
    audit.record(db, actor, "user.create", "user", str(u.id), f"新建用户 {un}（{ROLE_LABEL[u.role]}）")
    db.commit()
    return user_dict(u)


@router.patch("/users/{uid}")
async def update_user(uid: int, request: Request, actor=Depends(require(Role.ADMIN)), db: Session = Depends(get_db)):
    b = await json_body(request)
    u = db.get(User, uid)
    if not u:
        raise not_found("用户不存在")
    changes = {}
    if "role" in b:
        if b["role"] not in ROLES:
            raise unprocessable("角色应为 admin / editor / viewer")
        if u.role == "admin" and b["role"] != "admin" and _admins(db) <= 1:
            raise unprocessable("至少保留一个管理员", "LAST_ADMIN")
        changes["role"] = [u.role, b["role"]]
        u.role = b["role"]
        u.token_version += 1
    if "display_name" in b:
        u.display_name = (b["display_name"] or u.username).strip()
    if "is_active" in b:
        if not b["is_active"] and u.role == "admin" and _admins(db) <= 1:
            raise unprocessable("至少保留一个管理员", "LAST_ADMIN")
        changes["is_active"] = [u.is_active, bool(b["is_active"])]
        u.is_active = bool(b["is_active"])
        u.token_version += 1
    if b.get("password"):
        _check_pw(b["password"])
        u.password_hash = hash_password(b["password"])
        u.token_version += 1
        changes["password"] = "reset"
    audit.record(db, actor, "user.update", "user", str(u.id), f"修改用户 {u.username}", None, changes)
    db.commit()
    return user_dict(u)


def _admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True)))


@router.get("/audit")
def list_audit(entity_type: str | None = None, space_id: str | None = None, q: str | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
               actor=Depends(require(Role.ADMIN)), db: Session = Depends(get_db)):
    stmt = select(AuditLog)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if space_id:
        stmt = stmt.where(AuditLog.space_id == space_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditLog.summary.ilike(like), AuditLog.entity_id.ilike(like),
                              AuditLog.actor_name.ilike(like), AuditLog.action.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"total": total, "items": [{"id": r.id, "at": iso(r.at), "actor_type": r.actor_type, "actor": r.actor_name,
                                       "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id,
                                       "space_id": r.space_id, "summary": r.summary, "details": r.details,
                                       "ip": r.ip, "request_id": r.request_id} for r in rows]}
