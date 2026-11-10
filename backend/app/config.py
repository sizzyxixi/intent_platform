"""运行配置：全部来自环境变量（前缀 IP_），见 README「环境变量」。"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IP_", env_file=".env", extra="ignore")

    env: str = "development"  # development / production / test
    database_url: str = "postgresql+psycopg://intent:intent@127.0.0.1:5432/intent"
    secret_key: str = Field(default="dev-insecure-change-me", min_length=8)
    # 控制台会话
    session_cookie: str = "ip_session"
    session_hours: int = 12
    cookie_secure: bool = False
    # 初始管理员（种子脚本 / 首次启动时创建）
    admin_username: str = "admin"
    admin_password: str = "admin123456"
    admin_display_name: str = "管理员"
    # 开放接口
    rate_limit_per_minute: int = 60
    idempotency_ttl_hours: int = 24
    max_batch: int = 100
    max_upload_mb: int = 50
    public_base_url: str = "https://intent.example.com"
    docs_dir: str | None = None
    frontend_dist: str | None = None
    # 知识检索后端（可插拔）：postgres
    search_backend: str = "postgres"
    cors_origins: str = ""  # 逗号分隔；同域部署时留空
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.env == "production" and (s.secret_key.startswith("dev-insecure") or len(s.secret_key) < 32):
        raise RuntimeError("生产环境必须设置 IP_SECRET_KEY（至少 32 个字符的随机串）")
    return s
