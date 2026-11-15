# SafeShell Pro 单端口镜像：前端（React）在构建期编译进镜像，后端（FastAPI）单端口托管
# 前端静态资源与 API 同端口（8772），无需额外反代。生产部署：docker compose up --build

# ---------- 阶段 1：构建前端 ----------
FROM node:22-slim AS frontend
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- 阶段 2：运行后端 ----------
FROM python:3.13.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    APP_PORT=8772

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY schema.sql ./schema.sql
COPY --from=frontend /web/dist ./web/dist

# data/ 与 logs/ 为运行时产物（已被 .gitignore 忽略），干净 clone 不存在，须在此创建
RUN mkdir -p /app/data /app/logs

VOLUME ["/app/data", "/app/logs"]
EXPOSE 8772

# 端口可由 APP_PORT 覆盖（默认 8772）；前端静态资源由 FastAPI 同端口托管
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${APP_PORT:-8772}"]
