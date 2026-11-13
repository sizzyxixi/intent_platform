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
  echo "IP_PUBLIC_BASE_URL=http://$HOST_NAME"
  echo "IP_COOKIE_SECURE=false"
} > "$ENVF"
chmod 600 "$ENVF" "$REMOTE/config/secret_key"

docker build -q -t "$IMG:$REV" -t "$IMG:latest" -f "$REMOTE/src/deploy/aliyun/Dockerfile" "$REMOTE/src"

# 数据库迁移：仅在落后时升级（不导入任何种子数据）
CUR="$(docker run --rm --network "$DB_NET" --env-file "$ENVF" "$IMG:latest" alembic current 2>/dev/null | awk '{print $1}' | tail -1)"
HEAD="$(docker run --rm --network "$DB_NET" --env-file "$ENVF" "$IMG:latest" alembic heads 2>/dev/null | awk '{print $1}' | tail -1)"
echo "alembic current=$CUR head=$HEAD"
if [ -n "$HEAD" ] && [ "$CUR" != "$HEAD" ]; then
  docker run --rm --network "$DB_NET" --env-file "$ENVF" "$IMG:latest" alembic upgrade head
fi

docker rm -f "$APP" >/dev/null 2>&1 || true
docker run -d --name "$APP" --restart unless-stopped --network "$DB_NET" \
  --env-file "$ENVF" -p 127.0.0.1:8091:8000 "$IMG:latest" >/dev/null
docker network connect --alias intent-platform "$PROXY_NET" "$APP"

for i in $(seq 1 40); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$APP")" = healthy ] && break; sleep 3
done
docker inspect -f 'container {{.Name}} {{.State.Health.Status}}' "$APP"

# Caddy：按域名增加站点（不改动原有 :80 站点）。注意单文件 bind mount 需原地写入保持 inode
if ! grep -q "$HOST_NAME" "$CADDYFILE"; then
  cp -a "$CADDYFILE" "$CADDYFILE.bak.$(date +%Y%m%d%H%M%S)"
  NEWCF="$(mktemp)"
  cat "$CADDYFILE" > "$NEWCF"
  cat >> "$NEWCF" <<CADDY_EOF

# intent-platform（意图管理平台）
http://$HOST_NAME {
    reverse_proxy intent-platform:8000
}
CADDY_EOF
  docker cp "$NEWCF" "$CADDY:/tmp/Caddyfile.new"
  docker exec "$CADDY" caddy validate --config /tmp/Caddyfile.new --adapter caddyfile
  cat "$NEWCF" > "$CADDYFILE"   # 原地写入
  rm -f "$NEWCF"
  docker exec "$CADDY" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
fi
docker image prune -f >/dev/null
echo "部署完成：http://$HOST_NAME/"
REMOTE_EOF
