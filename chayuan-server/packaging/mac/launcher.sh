#!/bin/bash
# ============================================================================
# Chayuan.app 启动器 —— Contents/MacOS/Chayuan
#
# 本脚本在 .app 双击时由 macOS 执行。它需要做两件事：
#   1. 找到要用的 Python 解释器；
#   2. 设置好 PYTHONPATH，运行 `python -m chayuan.tray.entry`。
#
# 对 Python 的查找顺序（第一个命中即用）：
#   A. $CHAYUAN_PYTHON 显式指定；
#   B. 用户级 Miniforge（dist 首次运行后的 ~/Library/Application Support/
#      Chayuan/python/bin/python3）；
#   C. bundle 内 portable python（预留；当前未使用）；
#   D. 构建机硬编码路径（dev 模式，文件名 .chayuan_dev_python）；
#   E. 系统 PATH 中的 python3 兜底。
#
# dist 模式：如果找不到 B，但 bundle 里有 dist 资源（Miniforge 安装器 +
# offline wheels），就调 first_run.sh 做首次安装。
#
# 任何异常都尽量弹出一个 macOS 原生对话框告诉用户，而不是静默失败。
# ============================================================================

set -u

# --- 路径解析 ---------------------------------------------------------------
SELF="$0"
if [ -L "${SELF}" ]; then
    SELF="$(readlink "${SELF}")"
fi
MACOS_DIR="$(cd "$(dirname "${SELF}")" && pwd)"
CONTENTS_DIR="$(cd "${MACOS_DIR}/.." && pwd)"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"

# chayuan 源码在 Resources/src/chayuan-server/
SRC_ROOT="${RESOURCES_DIR}/src/chayuan-server"

# --- 用户级目录 --------------------------------------------------------------
# 注意：conda / Miniforge 不允许安装到带空格的路径，而 macOS 标准目录
# ~/Library/Application Support/Chayuan 名字里恰好有空格。所以我们用
# ~/.chayuan/ 作为统一根目录，结构如下：
#   ~/.chayuan/python/     Miniforge 安装目录
#   ~/.chayuan/logs/       启动 / first_run / tray / 后端日志
#   ~/.chayuan/data/       业务数据（CHAYUAN_ROOT）
#   ~/.chayuan/.installed  首次安装完成标记
# 用户要"彻底重装"时：rm -rf ~/.chayuan 即可。
APP_SUPPORT="${HOME}/.chayuan"
LOG_DIR="${APP_SUPPORT}/logs"
mkdir -p "${LOG_DIR}" 2>/dev/null || true
LAUNCH_LOG="${LOG_DIR}/launcher.log"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"${LAUNCH_LOG}" 2>/dev/null || true
}

die_gui() {
    local msg="$1"
    log "FATAL: ${msg}"
    /usr/bin/osascript -e "display dialog \"Chayuan 启动失败：\n\n${msg}\n\n详细日志：${LAUNCH_LOG}\" buttons {\"确定\"} default button 1 with title \"Chayuan\" with icon caution" >/dev/null 2>&1 || true
    exit 1
}

log "===== launcher start ====="
log "MACOS_DIR=${MACOS_DIR}"
log "RESOURCES_DIR=${RESOURCES_DIR}"
log "SRC_ROOT=${SRC_ROOT}"

# --- 读取 edition 标记 ------------------------------------------------------
# build_mac.sh 在 .app/Contents/Resources/.chayuan_edition 里写入 personal
# 或 enterprise。两者的运行时差异：
#   - personal  ：走 sqlite + faiss 默认，不强制鉴权；不做特殊 env 注入；
#   - enterprise：设置 CHAYUAN_EDITION=enterprise 与 CHAYUAN_INIT_PROFILE=prod，
#                 首次 init 会走 prod profile（Postgres / Milvus / Redis /
#                 AUTH_REQUIRED=true）。
EDITION="personal"
if [ -f "${RESOURCES_DIR}/.chayuan_edition" ]; then
    EDITION="$(head -n1 "${RESOURCES_DIR}/.chayuan_edition" | tr -d '[:space:]')"
fi
log "edition=${EDITION}"

if [ "${EDITION}" = "enterprise" ]; then
    export CHAYUAN_EDITION="enterprise"
    # 首次启动时的 init 会读这个 env；已初始化用户不受影响
    export CHAYUAN_INIT_PROFILE="${CHAYUAN_INIT_PROFILE:-prod}"
    # 企业版服务二进制的查找根：RESOURCES_DIR/services/bin（dev-enterprise）
    # 或 first_run 解包后的 ~/.chayuan/services/bin（dist-enterprise）；两者
    # 在 tray.services 里都会自动兜底，这里只需把 RESOURCES_DIR 暴露给子进程。
    export RESOURCES_DIR
fi

# --- dist 首次安装（可选）---------------------------------------------------
# 触发条件：用户级 Python 不存在 且 bundle 里带了 dist 资源（python-build-
# standalone tarball + 离线 wheels + first_run.sh）。
if [ ! -x "${APP_SUPPORT}/python/bin/python3" ] \
   && [ -f "${RESOURCES_DIR}/dist/python-runtime.tar.gz" ] \
   && [ -f "${RESOURCES_DIR}/dist/first_run.sh" ]; then
    log "触发首次安装流程..."
    export RESOURCES_DIR APP_SUPPORT
    export INSTALL_LOG="${LOG_DIR}/first_run.log"
    if ! bash "${RESOURCES_DIR}/dist/first_run.sh"; then
        die_gui "首次安装失败，请查看日志：${INSTALL_LOG}"
    fi
fi

# --- 选择 Python -------------------------------------------------------------
PYTHON_BIN=""

# A. 环境变量显式指定
if [ -n "${CHAYUAN_PYTHON:-}" ] && [ -x "${CHAYUAN_PYTHON}" ]; then
    PYTHON_BIN="${CHAYUAN_PYTHON}"
    log "using CHAYUAN_PYTHON: ${PYTHON_BIN}"
fi

# B. 用户级 python-build-standalone（dist 模式主路径）
if [ -z "${PYTHON_BIN}" ] && [ -x "${APP_SUPPORT}/python/bin/python3" ]; then
    PYTHON_BIN="${APP_SUPPORT}/python/bin/python3"
    log "using user python: ${PYTHON_BIN}"
fi

# C. bundle 内 portable python（预留）
if [ -z "${PYTHON_BIN}" ] && [ -x "${RESOURCES_DIR}/python/bin/python3" ]; then
    PYTHON_BIN="${RESOURCES_DIR}/python/bin/python3"
    log "using bundled portable python: ${PYTHON_BIN}"
fi

# D. dev 模式：硬编码的构建机 python
if [ -z "${PYTHON_BIN}" ] && [ -f "${RESOURCES_DIR}/.chayuan_dev_python" ]; then
    cand="$(head -n1 "${RESOURCES_DIR}/.chayuan_dev_python" | tr -d '[:space:]')"
    if [ -x "${cand}" ]; then
        PYTHON_BIN="${cand}"
        log "using dev-mode hardcoded python: ${PYTHON_BIN}"
    fi
fi

# E. PATH 兜底
if [ -z "${PYTHON_BIN}" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3)"
        log "using PATH python3: ${PYTHON_BIN}"
    fi
fi

if [ -z "${PYTHON_BIN}" ]; then
    die_gui "找不到可用的 Python。如果这是 dist 首次运行失败，请检查 Resources/dist/ 下是否完整。"
fi

# --- 运行 tray --------------------------------------------------------------
export PYTHONPATH="${SRC_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

# 数据目录固定到 ~/.chayuan/data（与 dist 安装路径一体化）。
# CHAYUAN_ROOT_IGNORE_STATE=1 绕过 resolve_chayuan_root 里 state.json 的
# 高优先级逻辑——对 CLI 用户那套"跨 shell 记住上次 init"的启发式在桌面 App
# 场景下会误把旧仓库目录（比如 data_test/）当成当前数据目录，造成
# .app 双击后读到不属于这个安装的配置文件。桌面 App 强制走 env var。
export CHAYUAN_ROOT="${CHAYUAN_ROOT:-${APP_SUPPORT}/data}"
export CHAYUAN_ROOT_IGNORE_STATE=1

log "PYTHONPATH=${PYTHONPATH}"
log "CHAYUAN_ROOT=${CHAYUAN_ROOT}"
log "exec: ${PYTHON_BIN} -m chayuan.tray.entry"

exec "${PYTHON_BIN}" -m chayuan.tray.entry >>"${LAUNCH_LOG}" 2>&1
