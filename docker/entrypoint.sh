#!/usr/bin/env bash
# entrypoint:同时跑 nginx (前端 + API 反代) + chayuan-server (Python)
# tini 在 PID 1,本脚本是 PID 2;chayuan 退出 -> 脚本退出 -> tini 收 nginx
set -e

echo "[entrypoint] booting chayuan-server docker image"
echo "[entrypoint] CHAYUAN_ROOT       = ${CHAYUAN_ROOT}"
echo "[entrypoint] CHAYUAN_PROFILE    = ${CHAYUAN_PROFILE}"
echo "[entrypoint] CHAYUAN_VECTOR_STORE = ${CHAYUAN_VECTOR_STORE}"

# 数据目录初始化(volume 第一次挂载是空目录)
mkdir -p "${CHAYUAN_ROOT}" \
         "${CHAYUAN_ROOT}/data" \
         "${CHAYUAN_ROOT}/data/knowledge_base" \
         "${CHAYUAN_ROOT}/data/files" \
         /chayuan/logs \
         /chayuan/app/chayuan-server/vendor/bundled_models

# 用户可以 bind mount 自己的 yaml 配置到 /chayuan/config,这里 symlink 到 CHAYUAN_ROOT
if [ -d /chayuan/config ] && [ "$(ls -A /chayuan/config 2>/dev/null)" ]; then
    echo "[entrypoint] /chayuan/config 有内容,符号链接到 ${CHAYUAN_ROOT}/"
    for f in /chayuan/config/*.yaml; do
        [ -f "$f" ] || continue
        ln -sf "$f" "${CHAYUAN_ROOT}/$(basename "$f")"
    done
fi

# 启 nginx (后台);失败 -> 整个容器退出
echo "[entrypoint] starting nginx ..."
nginx -t
nginx -g 'daemon off;' &
NGINX_PID=$!
echo "[entrypoint] nginx pid=$NGINX_PID listening on :80"

# 信号转发:容器收 SIGTERM/SIGINT,我们也杀 nginx + chayuan
trap 'echo "[entrypoint] shutdown..."; kill -TERM $NGINX_PID 2>/dev/null || true; exit 0' TERM INT

# chayuan-server foreground (PID 2 子进程,日志直出 stdout)
echo "[entrypoint] starting chayuan-server: chayuan $*"
cd /chayuan/app/chayuan-server/libs/chayuan-server
exec python -m chayuan "$@"
