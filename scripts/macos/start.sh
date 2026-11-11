#!/bin/bash
# macOS 本地运行（无 Homebrew / Docker）：启动本地 PostgreSQL（未运行时）+ 后端（http://127.0.0.1:8000，同时托管前端 dist）
# 目录约定：$ROOT/pgsql（PostgreSQL 二进制）、$ROOT/pgdata（数据目录）、$ROOT/backend、$ROOT/frontend/dist、$ROOT/.venv
set -e
ROOT="${IP_ROOT:-$HOME/intent-platform}"
PG="$ROOT/pgsql/bin"
if ! "$PG/pg_ctl" -D "$ROOT/pgdata" status >/dev/null 2>&1; then
  "$PG/pg_ctl" -D "$ROOT/pgdata" -l "$ROOT/pgdata/server.log" -o "-p 5432 -c listen_addresses=127.0.0.1 -k /tmp" -w -t 60 start
fi
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "8000 端口已有服务在运行（如需重启先执行：pkill -f 'uvicorn app.main:app'）"; exit 0
fi
cd "$ROOT/backend"
set -a; . ./.env.local; set +a
IP_FRONTEND_DIST=../frontend/dist nohup ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$ROOT/server.log" 2>&1 &
for i in $(seq 1 30); do
  if curl --noproxy '*' -s -m 2 -o /dev/null http://127.0.0.1:8000/healthz; then echo "已启动：http://localhost:8000"; exit 0; fi
  sleep 1
done
echo "后端启动超时，查看 $ROOT/server.log"; exit 1
