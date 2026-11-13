#!/usr/bin/env bash
# 一键（重新）部署到阿里云 ECS。在仓库根目录、能 ssh 到服务器的机器上执行：
#   bash deploy/aliyun/aliyun_deploy.sh            # 部署当前 HEAD
#   SERVER=root@47.86.15.93 bash deploy/aliyun/aliyun_deploy.sh
# 不会改动数据库数据，只在数据库版本落后时执行 alembic upgrade head。
set -euo pipefail
SERVER="${SERVER:-root@47.86.15.93}"
HOST_NAME="${HOST_NAME:-intent.47.86.15.93.sslip.io}"   # 对外访问域名（Caddy 按域名分流）
REMOTE=/opt/intent-platform
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "==> 构建前端"
(cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)

echo "==> 打包代码 $(git rev-parse --short HEAD)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
git archive --format=tar HEAD backend docs deploy/aliyun > "$TMP/src.tar"
mkdir -p "$TMP/x/frontend_dist" && cp -r frontend/dist/. "$TMP/x/frontend_dist/"
tar -C "$TMP/x" -rf "$TMP/src.tar" frontend_dist
gzip "$TMP/src.tar"
scp -q "$TMP/src.tar.gz" "$SERVER:/tmp/intent-platform-src.tar.gz"

echo "==> 服务器端构建与重启"
ssh "$SERVER" REV="$(git rev-parse --short HEAD)" HOST_NAME="$HOST_NAME" 'bash -s' <<'REMOTE_EOF'
set -euo pipefail
REMOTE=/opt/intent-platform
APP=intent-platform-app
IMG=intent-platform-app
DB_NET=moonclaw-db_default       # Postgres 容器所在网络（别名 postgres）
PROXY_NET=agentscope-ecs_default # Caddy 所在网络
CADDYFILE=/opt/moonclaw/config/Caddyfile
CADDY=agentscope-ecs-proxy-1

rm -rf "$REMOTE/src.new" && mkdir -p "$REMOTE/src.new"
tar -xzf /tmp/intent-platform-src.tar.gz -C "$REMOTE/src.new"
rm -rf "$REMOTE/src.prev"; [ -d "$REMOTE/src" ] && mv "$REMOTE/src" "$REMOTE/src.prev"
mv "$REMOTE/src.new" "$REMOTE/src"

# 运行配置（仅 root 可读）：由 config/database.env 派生，数据库主机改为容器网络内的 postgres
ENVF="$REMOTE/config/app.env"
umask 077
if [ ! -f "$REMOTE/config/secret_key" ]; then
  head -c 48 /dev/urandom | base64 | tr -d '\n/+=' > "$REMOTE/config/secret_key"
fi
DBURL="$(grep '^IP_DATABASE_URL=' "$REMOTE/config/database.env" | head -1 | cut -d= -f2- | sed 's#@127\.0\.0\.1:5432/#@postgres:5432/#')"
{
  echo "IP_ENV=production"
  echo "IP_DATABASE_URL=$DBURL"
  echo "IP_SECRET_KEY=$(cat "$REMOTE/config/secret_key")"
  echo "IP_PUBLIC_BASE_URL=http://47.86.15.93/intent-platform"
  echo "IP_COOKIE_SECURE=false"
} > "$ENVF"
chmod 600 "$ENVF" "$REMOTE/config/secret_key"

docker build -q -t "$IMG:$REV" -t "$IMG:latest" -f "$REMOTE/src/deploy/aliyun/Dockerfile" "$REMOTE/src"

# 数据库迁移：仅在落后时升级（不导入任何种子数据）
CUR="$(docker run --rm --network "$DB_NET" --env-file "$ENVF" "$IMG:latest" alembic current 2>/dev/null | awk '{print $1}' | tail -1)"
HEAD="$(docker run --rm --network "$DB_NET" --env-file "$ENVF" "$IMG:latest" alembic heads 2>/dev/null | awk '{print $1}' | tail -1)"
echo "alembic current=$CUR head=$HEAD"
if [ -n "$HEAD" ] && [ "$CUR" != "$HEAD" ]; then
  set +e
  docker run --rm --network "$DB_NET" --env-file "$ENVF" "$IMG:latest" alembic upgrade head
  up=$?
  set -e
  if [ "$up" -ne 0 ]; then
    echo "业务账号无权变更表结构，改用 postgres 超级用户执行迁移（密码不落盘、不打印）"
    MIGENV="$(mktemp)"
    chmod 600 "$MIGENV"
    docker exec moonclaw-db-postgres-1 printenv POSTGRES_PASSWORD | python3 -c '
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote
pw = sys.stdin.read().rstrip("\n")
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
out = []
for line in src.read_text().splitlines():
    if line.startswith("IP_DATABASE_URL="):
        u = urlsplit(line.split("=", 1)[1])
        netloc = "postgres:%s@%s:%s" % (quote(pw, safe=""), u.hostname, u.port or 5432)
        out.append("IP_DATABASE_URL=" + urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment)))
    else:
        out.append(line)
dst.write_text("\n".join(out) + "\n")
' "$ENVF" "$MIGENV"
    docker run --rm --network "$DB_NET" --env-file "$MIGENV" "$IMG:latest" alembic upgrade head
    rm -f "$MIGENV"
  fi
fi

docker rm -f "$APP" >/dev/null 2>&1 || true
docker run -d --name "$APP" --restart unless-stopped --network "$DB_NET" \
  --env-file "$ENVF" -p 127.0.0.1:8091:8000 "$IMG:latest" >/dev/null
docker network connect --alias intent-platform "$PROXY_NET" "$APP"

for i in $(seq 1 40); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$APP")" = healthy ] && break; sleep 3
done
docker inspect -f 'container {{.Name}} {{.State.Health.Status}}' "$APP"

# Caddy：按域名增加站点；并幂等地确保 :80 有 /intent-platform/*（供无法解析 sslip.io 的环境按 IP 访问）。
# 已存在的配置不改写。注意单文件 bind mount 需原地写入保持 inode。
caddy_changed=0
if ! grep -q "$HOST_NAME" "$CADDYFILE"; then
  caddy_changed=1
  cp -a "$CADDYFILE" "$CADDYFILE.bak.$(date +%Y%m%d%H%M%S)"
  NEWCF="$(mktemp)"
  cat "$CADDYFILE" > "$NEWCF"
  cat >> "$NEWCF" <<CADDY_EOF

# intent-platform（意图管理平台）
http://$HOST_NAME {
    reverse_proxy intent-platform:8000
}
CADDY_EOF
  cat "$NEWCF" > "$CADDYFILE"
  rm -f "$NEWCF"
fi
# 0=已存在，1=已插入，其它=失败
set +e
python3 "$REMOTE/src/deploy/aliyun/ensure_caddy_intent_path.py" "$CADDYFILE"
ens=$?
set -e
if [ "$ens" -gt 1 ]; then
  exit "$ens"
fi
if [ "$ens" -eq 1 ]; then
  caddy_changed=1
fi
if [ "$caddy_changed" -eq 1 ]; then
  NEWCF="$(mktemp)"
  cat "$CADDYFILE" > "$NEWCF"
  docker cp "$NEWCF" "$CADDY:/tmp/Caddyfile.new"
  docker exec "$CADDY" caddy validate --config /tmp/Caddyfile.new --adapter caddyfile
  cat "$NEWCF" > "$CADDYFILE"
  rm -f "$NEWCF"
  docker exec "$CADDY" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
fi
docker image prune -f >/dev/null
echo "部署完成：控制台 http://$HOST_NAME/  API http://47.86.15.93/intent-platform/"
REMOTE_EOF
