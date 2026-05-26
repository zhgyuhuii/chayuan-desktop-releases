#!/bin/bash
# ============================================================================
# Chayuan Linux 打包脚本（AppImage）
#
# 用法（在 Linux 构建机 x86_64 上，推荐 Ubuntu 20.04+）：
#     bash packaging/linux/build_linux.sh
#
# 前置条件：
#   1. packaging/vendor/cpython-3.11-x86_64-unknown-linux-gnu.tar.gz
#      （pbs 的 Linux glibc 版本 tarball）
#   2. packaging/vendor/wheels/ 里已 pip download 所有 Linux manylinux wheels
#   3. appimagetool 在 PATH 上
#      一次性下载：
#        curl -fL -o /usr/local/bin/appimagetool \
#          https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
#        chmod +x /usr/local/bin/appimagetool
#
# 产物：
#   packaging/build/linux/Chayuan-1.0.0.0-x86_64.AppImage
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${PKG_DIR}/.." && pwd)"

VENDOR_DIR="${PKG_DIR}/vendor"
BUILD_DIR="${PKG_DIR}/build/linux"
APPDIR="${BUILD_DIR}/Chayuan.AppDir"

VERSION="$(grep -m1 '^version' "${REPO_ROOT}/libs/chayuan-server/pyproject.toml" | sed -E 's/version *= *"([^"]+)".*/\1/')"
[ -n "${VERSION}" ] || VERSION="0.0.0.0"

ARCH="x86_64"   # 如需 aarch64 版，脚本分支
OUT="${BUILD_DIR}/Chayuan-${VERSION}-${ARCH}.AppImage"

echo "=========================================="
echo "  Chayuan Linux AppImage 打包"
echo "  version : ${VERSION}"
echo "  arch    : ${ARCH}"
echo "  appdir  : ${APPDIR}"
echo "  out     : ${OUT}"
echo "=========================================="

# ---- 前置资源检查 ---------------------------------------------------------
PY_TARBALL="${VENDOR_DIR}/cpython-3.11-${ARCH}-unknown-linux-gnu.tar.gz"
WHEELS_DIR="${VENDOR_DIR}/wheels"
REQ_SRC="${SCRIPT_DIR}/dist/requirements-runtime.txt"

missing=()
[ -f "${PY_TARBALL}" ] || missing+=("python runtime tarball: ${PY_TARBALL}")
[ -d "${WHEELS_DIR}" ] || missing+=("wheels dir: ${WHEELS_DIR}")
[ -f "${REQ_SRC}" ]    || missing+=("requirements file: ${REQ_SRC}")
if ! command -v appimagetool >/dev/null 2>&1; then
    missing+=("appimagetool not in PATH")
fi
if [ ${#missing[@]} -gt 0 ]; then
    echo "[build_linux] 缺少以下资源："
    for m in "${missing[@]}"; do echo "  - ${m}"; done
    cat <<EOF

补齐示例：

    # 1) python-build-standalone (Linux glibc x86_64, ~30 MB)
    curl -fL -o packaging/vendor/cpython-3.11-x86_64-unknown-linux-gnu.tar.gz \\
      "\$(curl -s https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \\
          | grep browser_download_url | grep '3.11' \\
          | grep 'x86_64-unknown-linux-gnu' | grep 'install_only_stripped' \\
          | head -1 | cut -d'\"' -f4)"

    # 2) 预下载 wheels（必须在 Linux 跑；manylinux 标签要匹配）
    python -m pip download \\
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \\
        --dest packaging/vendor/wheels \\
        --prefer-binary \\
        -r packaging/linux/dist/requirements-runtime.txt

    # 3) appimagetool
    sudo curl -fL -o /usr/local/bin/appimagetool \\
        https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
    sudo chmod +x /usr/local/bin/appimagetool

EOF
    exit 2
fi

# ---- 清理 / 创建 AppDir ----------------------------------------------------
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/src/chayuan-server"
mkdir -p "${APPDIR}/dist/wheels"
mkdir -p "${APPDIR}/usr/share/icons/hicolor/256x256/apps"

# ---- 托盘图标（44×44）与桌面图标（256×256）-------------------------------
IMG_DIR="${REPO_ROOT}/libs/chayuan-server/chayuan/img"
LOGO_PNG="${IMG_DIR}/logo.png"

# 托盘小图——如果 Python + PIL 可用就精准缩放，否则直接拷贝
TRAY_ICON="${IMG_DIR}/tray_icon.png"
if [ ! -f "${TRAY_ICON}" ] && [ -f "${LOGO_PNG}" ]; then
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "
from PIL import Image
im = Image.open('${LOGO_PNG}')
im.thumbnail((44, 44))
im.save('${TRAY_ICON}')
" 2>/dev/null || cp "${LOGO_PNG}" "${TRAY_ICON}"
    else
        cp "${LOGO_PNG}" "${TRAY_ICON}"
    fi
fi

# AppImage 桌面图标 chayuan.png（要求放在 AppDir 根，名字跟 .desktop 里的 Icon 对上）
cp "${LOGO_PNG}" "${APPDIR}/chayuan.png"
cp "${LOGO_PNG}" "${APPDIR}/usr/share/icons/hicolor/256x256/apps/chayuan.png"

# ---- 业务源码 -------------------------------------------------------------
echo "[build_linux] 拷贝业务源码..."
rsync -a \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' \
    --exclude='.DS_Store' --exclude='tests/' --exclude='data/' \
    "${REPO_ROOT}/libs/chayuan-server/chayuan" \
    "${APPDIR}/src/chayuan-server/"
if [ -d "${REPO_ROOT}/libs/chayuan-server/langchain_chayuan" ]; then
    rsync -a --exclude='__pycache__' --exclude='*.pyc' \
        "${REPO_ROOT}/libs/chayuan-server/langchain_chayuan" \
        "${APPDIR}/src/chayuan-server/"
fi

# ---- launcher / first_run / 依赖清单 / 运行时 tarball / wheels ------------
cp "${SCRIPT_DIR}/launcher.sh"                "${APPDIR}/AppRun"
chmod +x "${APPDIR}/AppRun"
cp "${SCRIPT_DIR}/chayuan.desktop"            "${APPDIR}/chayuan.desktop"
cp "${SCRIPT_DIR}/dist/first_run.sh"          "${APPDIR}/dist/first_run.sh"
chmod +x "${APPDIR}/dist/first_run.sh"
cp "${REQ_SRC}"                               "${APPDIR}/dist/requirements-runtime.txt"
cp "${PY_TARBALL}"                            "${APPDIR}/dist/python-runtime.tar.gz"
rsync -a --delete "${WHEELS_DIR}/" "${APPDIR}/dist/wheels/"

# ---- AppImage 打包 --------------------------------------------------------
echo "[build_linux] 生成 AppImage..."
# ARCH=x86_64 是 appimagetool 约定的 env var
ARCH=x86_64 appimagetool "${APPDIR}" "${OUT}"

APP_SIZE="$(du -sh "${OUT}" | awk '{print $1}')"
echo ""
echo "[build_linux] 构建完成 ✅"
echo "  AppImage: ${OUT} (${APP_SIZE})"
echo ""
echo "运行方式（任意 Linux x86_64，不需 root）："
echo "  chmod +x ${OUT}"
echo "  ${OUT}"
echo ""
echo "首次启动日志：~/.chayuan/logs/first_run.log"
