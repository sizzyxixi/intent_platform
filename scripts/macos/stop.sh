#!/bin/bash
ROOT="${IP_ROOT:-$HOME/intent-platform}"
pkill -f "uvicorn app.main:app" || true
"$ROOT/pgsql/bin/pg_ctl" -D "$ROOT/pgdata" -m fast stop || true
