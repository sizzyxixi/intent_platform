"""FastAPI 入口。"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import errors
from .api.admin import auth as admin_auth, intents as admin_intents, resources as admin_resources
from .api.v1 import routes as v1
from .config import get_settings
from .db import SessionLocal
from .mcp import router as mcp_router

log = logging.getLogger("intent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx 的 INFO 日志会带完整 URL（可能含 URL 参数里的凭据）
    try:
        from .services.knowledge import recover_stuck_builds
        with SessionLocal() as db:
            n = recover_stuck_builds(db)
            if n:
                log.warning("recovered %s stuck document builds", n)
    except Exception:  # noqa: BLE001  数据库未就绪时不阻止启动，健康检查会报告
        log.exception("startup recovery skipped")
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="意图管理平台", version="0.17.0", lifespan=lifespan,
                  docs_url="/api/docs" if s.env != "production" else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if s.env != "production" else None)
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()],
                           allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(GZipMiddleware, minimum_size=2048)  # 工具目录（数千个 CLI 命令）等大响应压缩传输
    errors.install(app)

    @app.get("/healthz")
    def healthz():
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
            return {"status": "ok"}
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "db_unavailable"}, status_code=503)

    app.include_router(v1.router)
    app.include_router(admin_auth.router)
    app.include_router(admin_intents.router)
    app.include_router(admin_resources.router)
    app.include_router(mcp_router)

    dist = Path(s.frontend_dist) if s.frontend_dist else None
    if dist and (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = dist / path
            if path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            # index.html 不缓存，避免部署后浏览器仍加载旧版前端
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return app


app = create_app()
