import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

ROLES = ("admin", "editor", "viewer")
ROLE_LABEL = {"admin": "管理员", "editor": "编辑", "viewer": "只读"}


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def create_session_token(user_id: int, token_version: int) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "ver": token_version, "iat": now,
               "exp": now + timedelta(hours=s.session_hours), "typ": "session"}
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_session_token(token: str) -> dict | None:
    try:
        p = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return p if p.get("typ") == "session" else None


# ---- API Key：ipk_<随机串>，只存 sha256 ----
def new_api_token() -> str:
    return "ipk_" + secrets.token_urlsafe(30).replace("-", "x").replace("_", "y")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_hint(key_id: str) -> str:
    return f"ipk_{key_id}_••••••••"


# ---- 工具密钥加密托管 ----
def _fernet() -> Fernet:
    k = hashlib.sha256(("tool-secret:" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(k))


def encrypt_secret(v: str) -> str:
    return _fernet().encrypt(v.encode()).decode()


def decrypt_secret(v: str) -> str | None:
    try:
        return _fernet().decrypt(v.encode()).decode()
    except InvalidToken:
        return None
