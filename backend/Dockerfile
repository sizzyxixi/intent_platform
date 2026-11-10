FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY docs/ /app/docs/
ENV IP_DOCS_DIR=/app/docs
RUN useradd -r -u 10001 app && chown -R app /app
USER app
EXPOSE 8000
# 生产：多 worker；迁移与种子由 compose 的 migrate 服务一次性执行
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
