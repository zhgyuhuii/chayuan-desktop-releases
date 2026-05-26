# syntax=docker/dockerfile:1.6
# ====================================================================
# 察元 AI 单机版 — Docker image (前后端一体)
# ====================================================================
# 单 image 同时跑:
#   - chayuan-server (Python FastAPI + chat/embedding/rerank/asr sidecar +
#     config panel)        ── 内部端口 62581 / 62582-5 / 8502
#   - nginx                ── 暴露端口 80,serve 前端 SPA + 反代 API
#
# 浏览器访问:http://<host>/  → 直接进前端
# API 自身仍可访问 http://<host>:62581 (compose 选择是否暴露)
#
# bundled_models 不打进 image,通过 docker volume 挂入。
#
# Build context **必须**是 repo 根 (chayuan-desktop/):
#   docker build -f docker/Dockerfile -t chayuan-server:1.0.0 .
#
# 或推荐 compose:
#   cd docker && docker compose up -d --build
# ====================================================================


# ─────────────────────────────────────────────────────────────────
# Stage 1: build 前端 (Vite -> apps/desktop/dist)
# 前端 build 时设 VITE_API_BASE=""  让 fetch 走相对路径,
# 浏览器同源 (nginx 反代) -> 后端,无 CORS。
# ─────────────────────────────────────────────────────────────────
FROM node:20-bookworm-slim AS web-builder

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=1
RUN corepack enable && corepack prepare pnpm@9.15.4 --activate

WORKDIR /chayuan-client
COPY chayuan-client/pnpm-workspace.yaml chayuan-client/package.json chayuan-client/pnpm-lock.yaml ./

# 拷所有 package.json 让 pnpm install 利用 layer cache
COPY chayuan-client/packages ./packages
COPY chayuan-client/apps    ./apps

# 装依赖(--frozen-lockfile = CI 严格模式)
# Tauri Rust 在 docker 构建里不需要,跳过 native bindings
ENV TAURI_SKIP_DEVSERVER_CHECK=1
RUN pnpm install --frozen-lockfile --prefer-offline || pnpm install

# 前端 build:VITE_API_BASE 空 + VITE_THIN_CLIENT 0,产物用相对路径请求 API
ENV VITE_API_BASE="" \
    VITE_THIN_CLIENT="0"
RUN pnpm -F desktop build:vite


# ─────────────────────────────────────────────────────────────────
# Stage 2: 后端 + nginx 运行时
# ─────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_CACHE_DIR=/tmp/poetry-cache \
    DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# 系统依赖
#   build-essential   : 个别 wheel 落不到二进制要现编
#   libgomp1          : faiss / numpy OpenMP runtime
#   libstdc++6 libgcc-s1 : llama-server / whisper-server sidecar 二进制 link
#   ffmpeg libsndfile1 : ASR 音频解码
#   libmagic1         : python-magic (unstructured 用)
#   nginx-light       : 前端 + API 反代
#   curl git ca-certs : 运行时下模型 / KB ingest / healthcheck
#   tini              : PID 1 信号转发,docker stop 干净退出 nginx + chayuan
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 libstdc++6 libgcc-s1 \
        ffmpeg libsndfile1 \
        libmagic1 \
        nginx-light \
        curl git ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* /var/log/nginx/*

RUN pip install --no-cache-dir poetry==2.2.0

WORKDIR /chayuan/app

# ── 1. 依赖层 ─────────────────────────────────────────────────────
COPY chayuan-server/libs/chayuan-server/pyproject.toml ./chayuan-server/libs/chayuan-server/
COPY chayuan-server/libs/chayuan-server/poetry.lock* ./chayuan-server/libs/chayuan-server/

WORKDIR /chayuan/app/chayuan-server/libs/chayuan-server
RUN poetry install --only main --no-root --no-cache && rm -rf /tmp/poetry-cache

# ── 2. 后端源码 ───────────────────────────────────────────────────
WORKDIR /chayuan/app
COPY chayuan-server/libs/chayuan-server/chayuan ./chayuan-server/libs/chayuan-server/chayuan
COPY chayuan-server/libs/chayuan-server/langchain_chayuan ./chayuan-server/libs/chayuan-server/langchain_chayuan

WORKDIR /chayuan/app/chayuan-server/libs/chayuan-server
RUN poetry install --only main --no-cache && rm -rf /tmp/poetry-cache

# ── 3. Sidecar 二进制 (.dockerignore 已排除非 linux 平台) ──────────
WORKDIR /chayuan/app
COPY chayuan-server/vendor/services ./chayuan-server/vendor/services
RUN chmod +x \
        /chayuan/app/chayuan-server/vendor/services/llama-server/linux-x64/llama-server \
        /chayuan/app/chayuan-server/vendor/services/whisper-server/linux-x64/whisper-server \
    || true

# ── 4. 拷前端 dist (来自 stage 1) ──────────────────────────────────
COPY --from=web-builder /chayuan-client/apps/desktop/dist /chayuan/web

# ── 5. nginx 配置 + entrypoint ────────────────────────────────────
COPY docker/nginx.conf      /etc/nginx/nginx.conf
COPY docker/entrypoint.sh   /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# ── 6. volume 占位 ────────────────────────────────────────────────
RUN mkdir -p /chayuan/app/chayuan-server/vendor/bundled_models \
             /chayuan/data \
             /chayuan/logs

# 运行时环境
ENV CHAYUAN_ROOT=/chayuan/data \
    CHAYUAN_PROFILE=single-machine \
    CHAYUAN_AUTH=anonymous \
    CHAYUAN_REDIS=disabled \
    CHAYUAN_QUEUE=inproc \
    CHAYUAN_VECTOR_STORE=sqlite-vec \
    CHAYUAN_AUTO_INSTALL_DEPS=false \
    CHAYUAN_ROOT_IGNORE_STATE=1 \
    CHAYUAN_VENDOR_PLATFORM=linux-x64 \
    PYTHONPATH=/chayuan/app/chayuan-server/libs/chayuan-server

EXPOSE 80 62581 8502

# nginx 反代后,健康检查打 nginx 80 即等价后端 ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -fsS http://127.0.0.1/healthz >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["start", "-a", "--single-machine"]
