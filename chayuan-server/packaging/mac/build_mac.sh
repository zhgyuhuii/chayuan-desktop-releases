#!/bin/bash
# ============================================================================
# Chayuan macOS 打包脚本
#
# 用法：
#   bash packaging/mac/build_mac.sh [dev|dist]
#
# dev（默认）：
#   - 只打业务源码 + 启动器 + 图标；
#   - 硬编码当前机器的 Python 路径（/opt/anaconda3/bin/python 之类）；
#   - 只能在本机双击运行，用于验证托盘 UX；
#   - 产物：build/dev/Chayuan.app，build/dev/Chayuan-{VERSION}-macos-arm64-personal-dev.dmg
#
# dist（TODO，M3.5 里交付）：
#   - bundle Miniforge 安装器；
#   - 预下载全部 whl；
#   - 首次启动自动安装到 ~/Library/Application Support/Chayuan/python/；
#   - 真正可离线分发的产物。
#
# 退出码：
#   0 成功，非 0 失败。
# ============================================================================

set -euo pipefail

# ---- 参数解析 --------------------------------------------------------------
# 用法：
#   build_mac.sh [dev|dist] [--edition=personal|enterprise]
#
# 位置参数 1 = 模式；可选 --edition 决定产物品牌。两者之间独立组合，常用：
#   build_mac.sh dev                               # 默认 personal dev
#   build_mac.sh dist                              # personal dist
#   build_mac.sh dist --edition=enterprise         # 企业版 dist
MODE="dev"
EDITION="personal"
for arg in "$@"; do
    case "${arg}" in
        dev|dist)                     MODE="${arg}" ;;
        --edition=personal|--edition=enterprise)
                                      EDITION="${arg#--edition=}" ;;
        --edition=*)
            echo "[build_mac] 未知 edition：${arg#--edition=}（仅支持 personal | enterprise）" >&2
            exit 2 ;;
        -h|--help)
            sed -n '1,22p' "${BASH_SOURCE[0]}"
            exit 0 ;;
        *)
            echo "[build_mac] 未知参数：${arg}" >&2
            exit 2 ;;
    esac
done

# ---- 路径定位 --------------------------------------------------------------
# 脚本位于 packaging/mac/build_mac.sh，向上两级找到 repo 根。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${PKG_DIR}/.." && pwd)"

MAC_DIR="${SCRIPT_DIR}"
# 构建输出按 edition 分目录，避免 personal / enterprise 互相覆盖
BUILD_DIR="${PKG_DIR}/build/${MODE}-${EDITION}"
VENDOR_DIR="${PKG_DIR}/vendor"

# 品牌差异：目前 enterprise 仅在 DMG 文件名 + Info.plist 展示名上加后缀，
# 真正的功能差异由 CHAYUAN_EDITION env 触发（launcher.sh 传给 tray）。
if [ "${EDITION}" = "enterprise" ]; then
    APP_NAME="Chayuan Enterprise"
    BUNDLE_ID="cn.bjzlnkj.chayuan.enterprise"
else
    APP_NAME="Chayuan"
    BUNDLE_ID="cn.bjzlnkj.chayuan"
fi
APP_BUNDLE="${BUILD_DIR}/${APP_NAME}.app"

# ---- 版本号从 libs/chayuan-server/pyproject.toml 读取 ---------------------
VERSION="$(grep -m1 '^version' "${REPO_ROOT}/libs/chayuan-server/pyproject.toml" | sed -E 's/version *= *"([^"]+)".*/\1/')"
[ -n "${VERSION}" ] || VERSION="0.0.0"

ARCH="$(uname -m)"   # arm64 / x86_64

DMG_FILE_BASE="Chayuan-${VERSION}-macos-${ARCH}-${EDITION}-${MODE}"
DMG_NAME="${DMG_FILE_BASE}.dmg"
DMG_PATH="${BUILD_DIR}/${DMG_NAME}"

echo "=========================================="
echo "  Chayuan macOS 打包"
echo "  mode      : ${MODE}"
echo "  version   : ${VERSION}"
echo "  arch      : ${ARCH}"
echo "  edition   : ${EDITION}"
echo "  bundle_id : ${BUNDLE_ID}"
echo "  app       : ${APP_BUNDLE}"
echo "  dmg       : ${DMG_PATH}"
echo "=========================================="

# ---- 模式分派 --------------------------------------------------------------
case "${MODE}" in
    dev|dist) ;;
    *)
        echo "[build_mac] 未知模式：${MODE}（仅支持 dev | dist）" >&2
        exit 2 ;;
esac

# ---- dist 预置资源检查 ----------------------------------------------------
if [ "${MODE}" = "dist" ]; then
    DIST_SRC="${MAC_DIR}/dist"
    PY_TARBALL_SRC="${VENDOR_DIR}/cpython-3.11-aarch64-apple-darwin.tar.gz"
    WHEELS_SRC="${VENDOR_DIR}/wheels"
    REQ_SRC="${DIST_SRC}/requirements-runtime.txt"

    missing=()
    [ -f "${PY_TARBALL_SRC}" ]     || missing+=("Python runtime tarball：${PY_TARBALL_SRC}")
    [ -d "${WHEELS_SRC}" ]         || missing+=("离线 wheels 目录：${WHEELS_SRC}")
    [ -f "${DIST_SRC}/first_run.sh" ] || missing+=("首次运行脚本：${DIST_SRC}/first_run.sh")
    [ -f "${REQ_SRC}" ]            || missing+=("依赖清单：${REQ_SRC}")
    if [ -d "${WHEELS_SRC}" ]; then
        whl_count=$(find "${WHEELS_SRC}" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' -o -name '*.zip' \) | wc -l | tr -d ' ')
        [ "${whl_count}" -gt 0 ]   || missing+=("wheels 目录为空（先运行预下载：参考 packaging/README.md）")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        echo "[build_mac] dist 模式缺少以下资源：" >&2
        for m in "${missing[@]}"; do echo "  - ${m}" >&2; done
        cat <<'EOF' >&2

补齐方式示例（Apple Silicon）：

    # 1) python-build-standalone（Astral 出的自包含 Python 3.11，~20MB）
    curl -fL -o packaging/vendor/cpython-3.11-aarch64-apple-darwin.tar.gz \
      "$(curl -s https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \
          | grep browser_download_url \
          | grep '3.11' | grep 'aarch64-apple' | grep 'install_only_stripped' \
          | head -1 | cut -d'"' -f4)"

    # 2) 预下载所有 arm64 wheels（~370MB，2-5 分钟）
    /opt/anaconda3/envs/chat/bin/pip download \
      --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
      --dest packaging/vendor/wheels \
      --prefer-binary \
      -r packaging/mac/dist/requirements-runtime.txt
EOF
        exit 2
    fi
    echo "[build_mac] dist 资源 OK（wheels=${whl_count}）"
fi

# ---- 选择 dev 模式用的 Python（必须能 import chayuan / rumps） -----------
pick_dev_python() {
    # 允许显式覆盖
    if [ -n "${CHAYUAN_DEV_PYTHON:-}" ] && [ -x "${CHAYUAN_DEV_PYTHON}" ]; then
        echo "${CHAYUAN_DEV_PYTHON}"
        return 0
    fi

    # 优先找 conda envs 里装了业务依赖的 python（避免 anaconda base 缺依赖）
    local candidates=()
    local env_dir
    for env_dir in /opt/anaconda3/envs/* /opt/miniconda3/envs/* "${HOME}/anaconda3/envs/"* "${HOME}/miniconda3/envs/"* "${HOME}/miniforge3/envs/"*; do
        [ -d "${env_dir}" ] || continue
        [ -x "${env_dir}/bin/python" ] || continue
        candidates+=("${env_dir}/bin/python")
    done
    # 再兜底 anaconda base / PATH
    candidates+=(
        "/opt/anaconda3/bin/python3"
        "/opt/anaconda3/bin/python"
        "$(command -v python3 2>/dev/null || true)"
        "$(command -v python 2>/dev/null || true)"
    )
    local cand
    for cand in "${candidates[@]}"; do
        [ -z "${cand}" ] && continue
        [ -x "${cand}" ] || continue
        # 要求三件套：rumps + chayuan（走 PYTHONPATH 可见）+ streamlit
        if "${cand}" -c "
import sys, importlib
sys.path.insert(0, '${REPO_ROOT}/libs/chayuan-server')
for m in ('rumps', 'chayuan', 'streamlit', 'fastapi', 'nicegui'):
    importlib.import_module(m)
" >/dev/null 2>&1; then
            echo "${cand}"
            return 0
        fi
    done
    return 1
}

# dev 模式才需要在本机找 Python；dist 模式启动后用用户级 Miniforge，构建时不依赖
if [ "${MODE}" = "dev" ]; then
    if ! DEV_PYTHON="$(pick_dev_python)"; then
        cat <<EOF >&2
[build_mac] 在本机找不到满足条件的 Python（需要 rumps + chayuan + streamlit + fastapi + nicegui）。
建议先：
    /opt/anaconda3/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple 'rumps>=0.4,<0.5'
或显式指定：
    CHAYUAN_DEV_PYTHON=/path/to/python bash packaging/mac/build_mac.sh dev
EOF
        exit 3
    fi
    echo "[build_mac] dev python: ${DEV_PYTHON}"
fi

# ---- 清空并重建构建目录 ---------------------------------------------------
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"
mkdir -p "${APP_BUNDLE}/Contents/MacOS"
mkdir -p "${APP_BUNDLE}/Contents/Resources/src"

# ---- 托盘图标：确保 chayuan/img/tray_icon.png 存在（44×44）---------------
# rumps / pystray 的 NSStatusItem 都不会自动缩放大图，logo.png（1024×1023）
# 直接塞进去会导致菜单栏不显示。build 前确保有专用小图。
IMG_DIR="${REPO_ROOT}/libs/chayuan-server/chayuan/img"
TRAY_ICON="${IMG_DIR}/tray_icon.png"
if [ ! -f "${TRAY_ICON}" ] && [ -f "${IMG_DIR}/logo.png" ]; then
    echo "[build_mac] 生成托盘小图 tray_icon.png（44×44）..."
    /usr/bin/sips -Z 44 "${IMG_DIR}/logo.png" --out "${TRAY_ICON}" >/dev/null
fi

# ---- 拷贝 chayuan-server 源码 --------------------------------------------
echo "[build_mac] 拷贝业务源码..."
# 只拷 libs/chayuan-server/chayuan/ 和必要的元数据；排除 __pycache__ / *.pyc / 测试。
# 注意：不能整个排 data/——其中 data/knowledge_base/samples/ 是包自带的示例
# 知识库，init 时会 shutil.copytree 到 CHAYUAN_ROOT。只排 data 里的运行时
# 输出（log / temp / media）。
SRC_DST="${APP_BUNDLE}/Contents/Resources/src/chayuan-server"
mkdir -p "${SRC_DST}"
/usr/bin/rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    --exclude='tests/' \
    --exclude='chayuan/data/logs/' \
    --exclude='chayuan/data/temp/' \
    --exclude='chayuan/data/media/' \
    "${REPO_ROOT}/libs/chayuan-server/chayuan" \
    "${SRC_DST}/"
/usr/bin/rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    "${REPO_ROOT}/libs/chayuan-server/langchain_chayuan" \
    "${SRC_DST}/" 2>/dev/null || true

# ---- 写 Info.plist（替换版本号 / 名称 / BundleID 占位符）-----------------
echo "[build_mac] 写入 Info.plist..."
sed -e "s/__CHAYUAN_VERSION__/${VERSION}/g" \
    -e "s#__CHAYUAN_BUNDLE_ID__#${BUNDLE_ID}#g" \
    -e "s#__CHAYUAN_APP_NAME__#${APP_NAME}#g" \
    "${MAC_DIR}/Info.plist" \
    > "${APP_BUNDLE}/Contents/Info.plist"

# ---- Edition 标记（供 launcher 读） ------------------------------------
# 启动时 launcher 读这个文件，把 CHAYUAN_EDITION 写进 env，再导出
# CHAYUAN_INIT_PROFILE（enterprise → prod）给首次 init 流程用。这样
# 同一套 launcher.sh 可以驱动 personal / enterprise 两种产物。
echo "${EDITION}" > "${APP_BUNDLE}/Contents/Resources/.chayuan_edition"

# ---- 启动器脚本 ----------------------------------------------------------
echo "[build_mac] 写入启动器..."
# 注意：.app/Contents/MacOS/<name> 必须和 Info.plist 里的 CFBundleExecutable
# 对应。Info.plist 目前固定 "Chayuan"；如果 EDITION=enterprise，APP_NAME 含
# 空格，CFBundleExecutable 不能有空格。所以可执行文件仍叫 "Chayuan"。
cp "${MAC_DIR}/launcher.sh" "${APP_BUNDLE}/Contents/MacOS/Chayuan"
chmod +x "${APP_BUNDLE}/Contents/MacOS/Chayuan"

if [ "${MODE}" = "dev" ]; then
    # dev 模式：把选定的 Python 路径写进 Resources/.chayuan_dev_python
    echo "${CHAYUAN_DEV_PYTHON:-${DEV_PYTHON}}" > "${APP_BUNDLE}/Contents/Resources/.chayuan_dev_python"
else
    # dist 模式：打包 python-build-standalone tarball + 离线 wheels + 首次运行脚本
    echo "[build_mac] dist: 注入 Python runtime tarball / 离线 wheels / 首次运行脚本..."
    DIST_DST="${APP_BUNDLE}/Contents/Resources/dist"
    mkdir -p "${DIST_DST}"

    # 注意：tarball 在 bundle 里改名成 python-runtime.tar.gz，first_run.sh 以此为锚。
    cp "${VENDOR_DIR}/cpython-3.11-aarch64-apple-darwin.tar.gz" "${DIST_DST}/python-runtime.tar.gz"
    cp "${MAC_DIR}/dist/requirements-runtime.txt"              "${DIST_DST}/requirements-runtime.txt"
    cp "${MAC_DIR}/dist/first_run.sh"                          "${DIST_DST}/first_run.sh"
    chmod +x "${DIST_DST}/first_run.sh"

    # wheels 用 rsync 以便未来增量重跑（目录很大，避免 cp 整体重拷）
    /usr/bin/rsync -a --delete "${VENDOR_DIR}/wheels/" "${DIST_DST}/wheels/"

    py_size="$(du -sh "${DIST_DST}/python-runtime.tar.gz" | awk '{print $1}')"
    wheel_total="$(du -sh "${DIST_DST}/wheels" | awk '{print $1}')"
    echo "[build_mac] dist python runtime: ${py_size}"
    echo "[build_mac] dist wheels 大小：${wheel_total}"
fi

# ---- 企业版 embedded 服务：Postgres + Redis 二进制 -----------------------
# 二进制来源：conda-forge osx-arm64，在构建机上 `conda create --prefix
# /tmp/chayuan-services-arm64 -c conda-forge postgresql=17 redis-server`
# 打包，最后 tar -czf 到 vendor/services/chayuan-services-macos-arm64.tar.gz。
# 产物 ~42 MB 压缩 / 135 MB 解压，relocatable（测试过移动 prefix 仍可跑）。
if [ "${EDITION}" = "enterprise" ]; then
    SERVICES_SRC="${VENDOR_DIR}/services/chayuan-services-macos-${ARCH}.tar.gz"
    if [ ! -f "${SERVICES_SRC}" ]; then
        cat <<EOF >&2
[build_mac] 企业版缺少 embedded 服务 tarball：${SERVICES_SRC}

补齐方式（构建机上一次性）：

    /opt/anaconda3/bin/conda create -y \\
      --override-channels \\
      -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/ \\
      --prefix /tmp/chayuan-services-${ARCH} \\
      postgresql=17 redis-server

    cd /tmp && tar -czf chayuan-services-macos-${ARCH}.tar.gz -C chayuan-services-${ARCH} .
    mv /tmp/chayuan-services-macos-${ARCH}.tar.gz packaging/vendor/services/
EOF
        exit 4
    fi

    SERVICES_DST_DIR="${APP_BUNDLE}/Contents/Resources/services"
    if [ "${MODE}" = "dev" ]; then
        # dev 模式：直接解压到 Resources/services/，便于热跑（不走 first_run）
        echo "[build_mac] enterprise dev: 解压 embedded services 到 Resources/services/..."
        mkdir -p "${SERVICES_DST_DIR}"
        /usr/bin/tar -xzf "${SERVICES_SRC}" -C "${SERVICES_DST_DIR}"
        svc_size="$(du -sh "${SERVICES_DST_DIR}" | awk '{print $1}')"
        echo "[build_mac] enterprise dev services size: ${svc_size}"
    else
        # dist 模式：把 tarball 放到 Resources/dist/，由 first_run 解压到
        # ~/.chayuan/services/——避免每次启动都要从 read-only .app bundle
        # 跑二进制（code signing 后会禁写 bundle 内文件，走用户目录最稳）。
        echo "[build_mac] enterprise dist: 注入 services tarball 到 Resources/dist/..."
        cp "${SERVICES_SRC}" "${DIST_DST}/services-runtime.tar.gz"
        svc_size="$(du -sh "${DIST_DST}/services-runtime.tar.gz" | awk '{print $1}')"
        echo "[build_mac] enterprise dist services tarball: ${svc_size}"
    fi
fi

# ---- 图标 .icns -----------------------------------------------------------
echo "[build_mac] 生成 .icns..."
ICON_DST="${APP_BUNDLE}/Contents/Resources/AppIcon.icns"
ICON_SRC=""
for cand in \
    "${REPO_ROOT}/libs/chayuan-server/chayuan/img/logo.png" \
    "${REPO_ROOT}/libs/chayuan-server/chayuan/img/chatchat_icon_blue_square_v2.png"; do
    if [ -f "${cand}" ]; then
        ICON_SRC="${cand}"
        break
    fi
done
if [ -n "${ICON_SRC}" ]; then
    bash "${MAC_DIR}/make_icns.sh" "${ICON_SRC}" "${ICON_DST}"
else
    echo "[build_mac] warn: 找不到图标源，跳过 .icns 生成（Finder 中将显示通用图标）"
fi

# ---- 注册 app（让 Finder 刷新图标）---------------------------------------
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "${APP_BUNDLE}" 2>/dev/null || true

# ---- 生成 DMG ------------------------------------------------------------
echo "[build_mac] 生成 DMG..."
STAGING="${BUILD_DIR}/dmg_staging"
rm -rf "${STAGING}"
mkdir -p "${STAGING}"
cp -R "${APP_BUNDLE}" "${STAGING}/"
ln -s /Applications "${STAGING}/Applications"

# hdiutil 原生可用，无需 brew
/usr/bin/hdiutil create \
    -volname "${APP_NAME} ${VERSION}" \
    -srcfolder "${STAGING}" \
    -ov \
    -format UDZO \
    -fs HFS+ \
    "${DMG_PATH}"

rm -rf "${STAGING}"

# ---- 汇总 ----------------------------------------------------------------
APP_SIZE="$(du -sh "${APP_BUNDLE}" | awk '{print $1}')"
DMG_SIZE="$(du -sh "${DMG_PATH}" | awk '{print $1}')"

cat <<EOF

[build_mac] 构建完成 ✅
  mode      : ${MODE}
  .app      : ${APP_BUNDLE} (${APP_SIZE})
  .dmg      : ${DMG_PATH} (${DMG_SIZE})

运行方式：
  1. 直接启动 .app（本机）：
       open "${APP_BUNDLE}"
  2. 安装 dmg（双击 → 拖到 Applications，再从 Launchpad 启动）：
       open "${DMG_PATH}"
EOF

if [ "${MODE}" = "dev" ]; then
cat <<EOF

注意（dev 模式限制）：
  * 仅本机可用，依赖硬编码的 Python：${DEV_PYTHON}
  * 未签名：首次双击会被 Gatekeeper 拦，请「右键 → 打开 → 确认」。
  * LSUIElement=1：只存在于菜单栏，不会出现在 Dock 和 Cmd+Tab 里。
EOF
else
cat <<EOF

注意（dist 模式）：
  * 首次启动会执行 first_run.sh：安装 Miniforge + 离线 wheels 到
      ~/.chayuan/python/ （约 1–3 分钟；conda 忌讳带空格路径，所以
      不走 ~/Library/Application Support/）；
  * 此后每次启动都直接用该 Python，不再联网；
  * 未签名：首次双击仍会被 Gatekeeper 拦，请「右键 → 打开 → 确认」；
  * 离线验证：拔网后再跑一次，确认仍能正常起来；
  * 彻底重装：rm -rf ~/.chayuan。
EOF
fi

cat <<'EOF'

启动日志位置：
  ~/.chayuan/logs/launcher.log
  ~/.chayuan/logs/tray.log
  ~/.chayuan/logs/first_run.log （仅 dist）
EOF
