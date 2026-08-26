FROM node:22-alpine AS web-builder
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /web
COPY frontend-vue/package.json frontend-vue/package-lock.json ./
RUN npm config set registry "$NPM_REGISTRY" \
    && npm ci --no-audit --no-fund
COPY frontend-vue/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ARG PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    DATABASE_PATH=/data/height_work_agent.sqlite \
    UPLOAD_DIR=/data/uploads \
    CHROMA_DIR=/data/chroma
WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app --home /app app
COPY pyproject.toml constraints.prod.txt ./
COPY backend/ ./backend/
RUN pip install --index-url "$PYPI_INDEX_URL" --upgrade pip \
    && pip install --index-url "$PYPI_INDEX_URL" --constraint constraints.prod.txt .
COPY data/ ./data/
COPY frontend/ ./frontend/
COPY --from=web-builder /web/dist ./frontend-vue/dist/
RUN mkdir -p /data/uploads /data/chroma /data/backups && chown -R app:app /app /data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)" || exit 1
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
