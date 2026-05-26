#!/bin/bash
# ============================================================================
# Chayuan Linux 启动器
#
# 本脚本是 AppImage 的 AppRun（entry point），也可以当作 .deb / .rpm 安装
# 后的 /usr/bin/chayuan 使用。它负责：
#   1. 定位 bundle 内的 Python 或触发首次安装；
#   2. 设置 PYTHONPATH / CHAYUAN_ROOT；
#   3. 起 tray 进程（pystray 会自动走 AppIndicator 或 XEmbed）。
#
# 对托盘的提醒：GNOME 3.26+ 默认禁用 legacy tray，需要用户装
# `gnome-shell-extension-appindicator`；KDE / XFCE / Cinnamon 默认支持。
# 启动时会在 launcher.log 里记录 DE 信息，排障时定位用。
# ============================================================================

set -u

# --- 解析自身路径 -----------------------------------------------------------
SELF="$(readlink -f "$0" 2>/dev/null || echo "$0")"
APPDIR="$(cd "$(dirname "${SELF}")" && pwd)"

# AppImage 运行时 APPDIR 由 AppRun 外部设置；如果脚本被直接当 AppRun 用，
# 那 APPDIR 就是脚本所在目录（AppDir 根）。两种模式都兼容。
if [ -n "${APPIMAGE:-}" ] && [ -n "${APPDIR:-}" ]; then
    # 走 AppImage 路径：使用 AppImage runtime 传入的 APPDIR
    :
fi

SRC_ROOT="${APPDIR}/src/chayuan-server"
DIST_DIR="${APPDIR}/dist"

# --- 用户目录 ---------------------------------------------------------------
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
    # zenity / kdialog / notify-send 任一可用都试
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Chayuan" --text="启动失败：\n\n${msg}\n\n日志：${LAUNCH_LOG}" 2>/dev/null &
    elif command -v kdialog >/dev/null 2>&1; then
        kdialog --error "Chayuan 启动失败：${msg}" 2>/dev/null &
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send -u critical "Chayuan" "启动失败：${msg}" 2>/dev/null &
    else
        echo "Chayuan 启动失败：${msg}" >&2
    fi
    exit 1
}

log "===== launcher start ====="
log "APPDIR=${APPDIR}"
log "SRC_ROOT=${SRC_ROOT}"
log "DE=${XDG_CURRENT_DESKTOP:-unknown} Session=${XDG_SESSION_TYPE:-unknown}"

# --- 首次安装触发 ----------------------------------------------------------
if [ ! -x "${APP_SUPPORT}/python/bin/python3" ] \
   && [ -f "${DIST_DIR}/python-runtime.tar.gz" ] \
   && [ -f "${DIST_DIR}/first_run.sh" ]; then
    log "触发首次安装流程..."
    export APPDIR APP_SUPPORT
    export INSTALL_LOG="${LOG_DIR}/first_run.log"
    # Linux first_run 读的是 RESOURCES_DIR 变量（跟 mac 对齐），这里映射过去
    export RESOURCES_DIR="${APPDIR}"
    if ! bash "${DIST_DIR}/first_run.sh"; then
        die_gui "首次安装失败，日志：${INSTALL_LOG}"
    fi
fi

# --- 选择 Python -----------------------------------------------------------
PYTHON_BIN=""

if [ -n "${CHAYUAN_PYTHON:-}" ] && [ -x "${CHAYUAN_PYTHON}" ]; then
    PYTHON_BIN="${CHAYUAN_PYTHON}"
    log "using CHAYUAN_PYTHON: ${PYTHON_BIN}"
fi

if [ -z "${PYTHON_BIN}" ] && [ -x "${APP_SUPPORT}/python/bin/python3" ]; then
    PYTHON_BIN="${APP_SUPPORT}/python/bin/python3"
    log "using user python: ${PYTHON_BIN}"
fi

if [ -z "${PYTHON_BIN}" ] && [ -x "${APPDIR}/python/bin/python3" ]; then
    PYTHON_BIN="${APPDIR}/python/bin/python3"
    log "using bundled python (appimage portable): ${PYTHON_BIN}"
fi

if [ -z "${PYTHON_BIN}" ] && command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
    log "using PATH python3: ${PYTHON_BIN}"
fi

if [ -z "${PYTHON_BIN}" ]; then
    die_gui '找不到可用的 Python；请确认 dist 资源完整或 PATH 上有 python3 >= 3.10'
fi

# --- 运行 tray -------------------------------------------------------------
export PYTHONPATH="${SRC_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export CHAYUAN_ROOT="${CHAYUAN_ROOT:-${APP_SUPPORT}/data}"

log "PYTHONPATH=${PYTHONPATH}"
log "CHAYUAN_ROOT=${CHAYUAN_ROOT}"
log "exec: ${PYTHON_BIN} -m chayuan.tray.entry"

exec "${PYTHON_BIN}" -m chayuan.tray.entry >>"${LAUNCH_LOG}" 2>&1
