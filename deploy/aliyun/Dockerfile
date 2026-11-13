# 阿里云单容器部署：FastAPI 同时提供 /api、/mcp 与前端静态页（frontend_dist 由 aliyun_deploy.sh 预先构建并放进构建上下文）
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
RUN useradd -m -u 10001 app
# 西之月 xclaw-cli（工具中心「测试调用」执行 CLI 工具用）；安装到 /home/app/.xclaw/bin，未登录
USER app
RUN curl -fsSL https://open.westmonth.com/install | sh || echo "xclaw-cli 安装失败，CLI 测试调用不可用"
USER root
COPY backend/ ./
COPY docs/ /app/docs/
COPY frontend_dist/ /app/frontend_dist/
RUN chown -R app /app
USER app
ENV HOME=/home/app PATH=/home/app/.xclaw/bin:$PATH \
    IP_DOCS_DIR=/app/docs IP_FRONTEND_DIST=/app/frontend_dist
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=10 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
