#!/usr/bin/env bash
# 为 Playwright e2e 启动独立后端：重建 E2E 数据库 → 迁移 → 灌种子数据 → 启动 uvicorn（同时托管前端 dist）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python
export IP_ENV=test
export IP_DATABASE_URL="${E2E_DATABASE_URL:-postgresql+psycopg://intent:intent@127.0.0.1:5432/intent_e2e}"
export IP_SECRET_KEY="${IP_SECRET_KEY:-e2e-secret-key-0123456789abcdef-xyz}"
export IP_RATE_LIMIT_PER_MINUTE=100000
export IP_FRONTEND_DIST="$ROOT/frontend/dist"
export IP_ADMIN_USERNAME=admin IP_ADMIN_PASSWORD=admin123456
cd "$ROOT/backend"
"$PY" - <<'PY'
import os, sqlalchemy as sa
from sqlalchemy.engine import make_url
u = make_url(os.environ["IP_DATABASE_URL"])
eng = sa.create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")
with eng.connect() as c:
    if not c.scalar(sa.text("select 1 from pg_database where datname=:n"), {"n": u.database}):
        c.execute(sa.text(f'create database "{u.database}"'))
eng2 = sa.create_engine(u, isolation_level="AUTOCOMMIT")
with eng2.connect() as c:
    c.execute(sa.text("drop schema public cascade; create schema public"))
PY
"$PY" -m alembic upgrade head
"$PY" -m app.seed --reset --tokens-out /tmp/e2e_tokens.json
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "${E2E_PORT:-8100}"
