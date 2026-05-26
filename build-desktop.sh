#!/usr/bin/env bash
# ====================================================================
# 察元 AI 单机版桌面安装包构建脚本 — macOS / Linux 通用入口。
#
# 与 Windows 端 build-desktop.cmd / build-desktop.ps1 同形,开关命名一致
# (改用 sh 风格连字符):
#
#   ./build-desktop.sh                                  默认 = 双产物:轻量版 + 集成版
#   ./build-desktop.sh --lite-only                      只打"轻量版"(不嵌模型,体积小)
#   ./build-desktop.sh --integrated-only                只打"集成版"(vendor/bundled_models/ 进 Tauri resources)
#   ./build-desktop.sh --skip-model-check               跳过 bundled_models 体检
#   ./build-desktop.sh --skip-server                    显式跳过 sidecar PyInstaller(不算指纹)
#   ./build-desktop.sh --rebuild-server                 强制重建 sidecar(忽略指纹缓存)
#   ./build-desktop.sh --skip-healthcheck               跳过 /healthz 探测
#   ./build-desktop.sh --skip-frontend                  跳过 Tauri bundle
#   ./build-desktop.sh --skip-install                   跳过 pnpm install
#   ./build-desktop.sh --skip-typecheck                 跳过 pnpm typecheck
#   ./build-desktop.sh --bundle-only                    跳过前 4 步,只重做 Tauri bundle
#   ./build-desktop.sh --clean                          构建前先清 dist + bundle
#   ./build-desktop.sh --skip-clean-target              跳过"打包前清 target/"(默认会清=全量冷构建)
#   ./build-desktop.sh --verbose                        透传 PyInstaller / pnpm 完整 stdout
#
# 双产物说明
# ---------
#   轻量版(lite):src-tauri/bundled_models/ 清空,装机包小,首次启动靠
#                 BootstrapBanner 引导用户在线下载或扫盘配置。
#   集成版(integrated):vendor/bundled_models/ 整树拷进 src-tauri/bundled_models/,
#                 被 Tauri bundle.resources 打进安装包(与主 exe 同目录,**不嵌
#                 入 sidecar 内**),用户装好即用,无需联网下载。模型清单见
#                 chayuan-server/vendor/bundled_models/README.md。
#
#   2026-05-15 起两个 flavor 共用同一份 sidecar exe — 模型从 PyInstaller datas
#   剥到 Tauri resources,解决集成版 sidecar ≥ 2 GB 撞 32-bit makensis mmap
#   上限。flavor 差异只剩 build.py --sync-bundled-only 那步;CHAYUAN_LITE_BUILD
#   env 仍设但只控制 Step 1b 同步行为,spec 自身已不读它。
#
#   两个产物各自落到:
#     dist-lite/          ← 轻量版 .dmg/.deb/.AppImage/.msi/.exe
#     dist-integrated/    ← 集成版 .dmg/.deb/.AppImage/.msi/.exe
#
# Sidecar 指纹缓存
# ---------------
#   首次构建后,把 chayuan-server 源码 sha256 落到 ``sidecar.sha256`` 旁边。
#   下次跑 build-desktop.sh 时如果指纹一致,自动跳过 PyInstaller(省 5-10 分钟)。
#   chayuan-server 的 *.py / *.spec / pyproject.toml / poetry.lock 任一改动都会
#   失效,触发重建。手工强制重建用 ``--rebuild-server``,完全跳过用 ``--skip-server``。
#
# 跨架构注意
# ---------
#   x86_64 与 ARM64(aarch64 / arm64)产物**完全不互通**:
#     Intel Mac .dmg     ⇄ Apple Silicon Mac .dmg   不兼容(Rosetta 可勉强,但不推荐)
#     Linux x86_64 .deb  ⇄ Linux ARM64 .deb         不兼容
#     Linux x86_64 .AppImage ⇄ aarch64 .AppImage    不兼容
#
#   本脚本**只为 host 架构出包**;要支持双架构请在两台对应 CPU 的机器
#   (或 CI 双 runner)分别跑一次。脚本会在产物清单里明确标出当前 host
#   架构,提醒分发时不要错配。
#
#   2026-05-19 起 chayuan-server 改用 PyInstaller ``--onedir``:产物不再是
#   ``src-tauri/binaries/chayuan-server-<triple>`` 单文件,而是 build.py 把整个
#   ``dist/chayuan-server/`` 树拷到 ``src-tauri/chayuan-server/``(bootloader
#   ``chayuan-server`` + ``_internal/``),走 Tauri ``bundle.resources`` 而不是
#   ``externalBin``。本脚本据此校验 src-tauri/chayuan-server/chayuan-server。
# ====================================================================

set -euo pipefail

# ── 0. 路径 + 平台识别 ────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$SCRIPT_DIR"

# ── 0a. 版本号自增 + 同步 ──────────────────────────────────────────
# 真源:repo 根 VERSION (X.Y.Z)。
# 每跑一次 build,scripts/bump-version.py 把 patch 段 +1,同步写到
# tauri.conf.json + chayuan-server/pyproject.toml。Tauri/PyInstaller 后续
# 读到的就是新版本。--no-bump 子命令(可加 env CHAYUAN_BUILD_NO_BUMP=1
# 触发)只同步不递增,适合 CI 调试时复刻已有版本。
if [[ "${CHAYUAN_BUILD_NO_BUMP:-0}" == "1" ]]; then
    BUILD_VERSION="$(python3 "$WORKSPACE/scripts/bump-version.py" --no-bump)" || true
else
    BUILD_VERSION="$(python3 "$WORKSPACE/scripts/bump-version.py")" || true
fi
echo "[build-desktop] BUILD_VERSION=$BUILD_VERSION"

SERVER_DIR="$WORKSPACE/chayuan-server"
SERVER_PKG_DIR="$SERVER_DIR/libs/chayuan-server"
CLIENT_DIR="$WORKSPACE/chayuan-client"
DESKTOP_DIR="$CLIENT_DIR/apps/desktop"
# 2026-05-19 chayuan-server 切 PyInstaller --onedir:产物不再是
# src-tauri/binaries/chayuan-server-<triple> 单文件,而是 build.py 把整个
# dist/chayuan-server/ 树拷到 src-tauri/chayuan-server/(走 Tauri ``resources``,
# 见 tauri.conf.json bundle.resources)。这里跟 build.py::DESKTOP_CHAYUAN_SERVER_DIR
# 及 build-desktop.ps1::$ChayuanServerClientDir 对齐。
CHAYUAN_SERVER_DIR="$DESKTOP_DIR/src-tauri/chayuan-server"
TAURI_BUNDLE="$DESKTOP_DIR/src-tauri/target/release/bundle"

UNAME_S="$(uname -s)"
UNAME_M="$(uname -m)"

# Rust target triple,与 packaging/pyinstaller/build.py::rust_target_triple() 对齐。
case "$UNAME_S" in
    Darwin)
        OS_NAME=macos
        case "$UNAME_M" in
            arm64|aarch64) ARCH=aarch64 ; TRIPLE=aarch64-apple-darwin ;;
            x86_64|amd64)  ARCH=x86_64  ; TRIPLE=x86_64-apple-darwin  ;;
            *) echo "[FAIL] unsupported macOS arch: $UNAME_M" >&2 ; exit 2 ;;
        esac
        ;;
    Linux)
        OS_NAME=linux
        case "$UNAME_M" in
            aarch64|arm64) ARCH=aarch64 ; TRIPLE=aarch64-unknown-linux-gnu ;;
            x86_64|amd64)  ARCH=x86_64  ; TRIPLE=x86_64-unknown-linux-gnu  ;;
            *) echo "[FAIL] unsupported Linux arch: $UNAME_M" >&2 ; exit 2 ;;
        esac
        ;;
    *)
        echo "[FAIL] 本脚本仅支持 macOS / Linux;Windows 请用 build-desktop.cmd" >&2
        exit 2
        ;;
esac

# --onedir 产物里的 bootloader 可执行文件。Linux/macOS 无 .exe 后缀,且不带
# rust target triple —— build.py copy_to_tauri_resources() 整树拷过来时它就叫
# ``chayuan-server``(对应 Windows 的 chayuan-server.exe)。
SIDECAR_NAME="chayuan-server"
SIDECAR_PATH="$CHAYUAN_SERVER_DIR/$SIDECAR_NAME"

# ── 1. 参数解析 ──────────────────────────────────────────────────
SKIP_SERVER=0
SKIP_HEALTHCHECK=0
SKIP_FRONTEND=0
SKIP_INSTALL=0
SKIP_TYPECHECK=0
BUNDLE_ONLY=0
CLEAN=0
SKIP_CLEAN_TARGET=0   # 默认每次打包前完全清 Rust target/;传 --skip-clean-target 关闭
VERBOSE=0
REBUILD_SERVER=0
SKIP_SERVER_AUTO=0  # 内部状态:Step 1 是被指纹自动跳过,还是 --skip-server 显式跳过
SKIP_MODEL_CHECK=0
LITE_ONLY=0
INTEGRATED_ONLY=0

usage() {
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-server)      SKIP_SERVER=1 ;;
        --rebuild-server)   REBUILD_SERVER=1 ;;
        --skip-healthcheck) SKIP_HEALTHCHECK=1 ;;
        --skip-frontend)    SKIP_FRONTEND=1 ;;
        --skip-install)     SKIP_INSTALL=1 ;;
        --skip-typecheck)   SKIP_TYPECHECK=1 ;;
        --bundle-only)      BUNDLE_ONLY=1 ;;
        --clean)            CLEAN=1 ;;
        --skip-clean-target) SKIP_CLEAN_TARGET=1 ;;
        --verbose|-v)       VERBOSE=1 ;;
        --skip-model-check) SKIP_MODEL_CHECK=1 ;;
        --lite-only)        LITE_ONLY=1 ;;
        --integrated-only)  INTEGRATED_ONLY=1 ;;
        -h|--help)          usage ;;
        *)
            echo "[FAIL] 未知参数: $1" >&2
            echo "可用参数:--lite-only / --integrated-only / --skip-model-check / --skip-server / --rebuild-server / --skip-healthcheck / --skip-frontend / --skip-install / --skip-typecheck / --bundle-only / --clean / --skip-clean-target / --verbose" >&2
            exit 2 ;;
    esac
    shift
done

# 互斥检查
if [[ "$LITE_ONLY" == "1" ]] && [[ "$INTEGRATED_ONLY" == "1" ]]; then
    echo "[FAIL] --lite-only 与 --integrated-only 互斥,二选一(不带 = 都打)" >&2
    exit 2
fi

# 决定本次要打哪些 flavor
FLAVORS=()
if [[ "$LITE_ONLY" == "1" ]]; then
    FLAVORS=(lite)
elif [[ "$INTEGRATED_ONLY" == "1" ]]; then
    FLAVORS=(integrated)
else
    FLAVORS=(lite integrated)
fi

if [[ "$BUNDLE_ONLY" == "1" ]]; then
    SKIP_SERVER=1
    SKIP_HEALTHCHECK=1
    SKIP_INSTALL=1
    SKIP_TYPECHECK=1
fi

# ── 2. 颜色 + 日志助手 ────────────────────────────────────────────
if [[ -t 1 ]] && [[ "${NO_COLOR:-}" == "" ]]; then
    C_RESET=$'\033[0m' ; C_BOLD=$'\033[1m'
    C_GREEN=$'\033[32m' ; C_YELLOW=$'\033[33m' ; C_RED=$'\033[31m' ; C_CYAN=$'\033[36m'
else
    C_RESET= ; C_BOLD= ; C_GREEN= ; C_YELLOW= ; C_RED= ; C_CYAN=
fi

step() { echo "${C_BOLD}${C_CYAN}» $*${C_RESET}"; }
ok()   { echo "${C_GREEN}[OK]${C_RESET} $*"; }
skip() { echo "${C_YELLOW}[SKIP]${C_RESET} $*"; }
fail() { echo "${C_RED}[FAIL]${C_RESET} $*" >&2; }
note() { echo "    ${C_YELLOW}↪${C_RESET} $*"; }

invoke_step() {
    local name="$1" ; shift
    local workdir="$1" ; shift
    step "▸ $name"
    if [[ "$VERBOSE" == "1" ]]; then
        ( cd "$workdir" && "$@" )
    else
        local logfile
        logfile="$(mktemp -t chayuan-build-XXXXXX.log)"
        if ( cd "$workdir" && "$@" ) >"$logfile" 2>&1 ; then
            ok "$name"
            rm -f "$logfile"
        else
            local rc=$?
            fail "$name 失败 (rc=$rc),最后 50 行:"
            tail -n 50 "$logfile" | sed 's/^/    /'
            note "完整日志: $logfile"
            exit $rc
        fi
    fi
}

# ── 3. 工作区 + 工具链探测 ────────────────────────────────────────
step '工作区'
note "OS:        $OS_NAME"
note "Arch:      $ARCH ($TRIPLE)"
note "Workspace: $WORKSPACE"
note "Sidecar:   $SIDECAR_NAME"
[[ -d "$SERVER_DIR" ]] || { fail "找不到 chayuan-server 目录" ; exit 2; }
[[ -d "$CLIENT_DIR" ]] || { fail "找不到 chayuan-client 目录" ; exit 2; }

require_cmd() {
    local cmd="$1" hint="$2"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        fail "缺工具 '$cmd'。$hint"
        exit 127
    fi
    note "$cmd -> $(command -v "$cmd")"
}

step '工具链检查'
if [[ "$SKIP_SERVER" != "1" ]]; then
    require_cmd python3 "请装 Python 3.10+ (https://www.python.org/downloads/)"
    if ! command -v poetry >/dev/null 2>&1; then
        note "未检测到 poetry,后端打包将走 ${C_BOLD}python3 -m pip / pyinstaller${C_RESET}"
    else
        note "poetry -> $(command -v poetry)"
    fi
    # PyInstaller 在 Linux 上需要 objdump(系统包 binutils)分析动态库依赖,
    # 缺了会在打包深处报 "On Linux, objdump is required"。系统包要 root 装、
    # 不像 Rust 能装进 ~/.cargo,这里只检测 + 给出明确安装命令,不自动 sudo
    # (避免脚本挂在密码提示上 / 跨发行版乱猜)。
    if ! command -v objdump >/dev/null 2>&1; then
        fail "缺 'objdump'(系统包 binutils)—— PyInstaller 在 Linux 打包必需。"
        if   command -v apt-get >/dev/null 2>&1; then note "请装:${C_BOLD}sudo apt-get install -y binutils${C_RESET}"
        elif command -v dnf     >/dev/null 2>&1; then note "请装:${C_BOLD}sudo dnf install -y binutils${C_RESET}"
        elif command -v yum     >/dev/null 2>&1; then note "请装:${C_BOLD}sudo yum install -y binutils${C_RESET}"
        elif command -v pacman  >/dev/null 2>&1; then note "请装:${C_BOLD}sudo pacman -S --noconfirm binutils${C_RESET}"
        elif command -v zypper  >/dev/null 2>&1; then note "请装:${C_BOLD}sudo zypper install -y binutils${C_RESET}"
        else note "用你发行版的包管理器安装 binutils"
        fi
        exit 127
    fi
    note "objdump -> $(command -v objdump)"
fi
if [[ "$SKIP_FRONTEND" != "1" ]]; then
    require_cmd pnpm "请装 pnpm (corepack enable;npm i -g pnpm)"
    require_cmd cargo "请装 Rust (https://rustup.rs)"
    # Tauri 在 Linux 上编译需要 C 编译器(cc/gcc,Rust 链接用)+ 一堆 GTK/WebKit
    # 开发库。这些是系统包、要 root 装(不像 Rust 能装进 ~/.cargo),只检测 +
    # 给确切安装命令,不自动 sudo。一次把缺的全列出来,免得一个个撞。
    if [[ "$OS_NAME" == "linux" ]]; then
        missing_sys=()
        command -v cc         >/dev/null 2>&1 || missing_sys+=("C 编译器(cc/gcc)")
        command -v pkg-config >/dev/null 2>&1 || missing_sys+=("pkg-config")
        if command -v pkg-config >/dev/null 2>&1; then
            pkg-config --exists webkit2gtk-4.1 2>/dev/null || missing_sys+=("libwebkit2gtk-4.1-dev")
            pkg-config --exists gtk+-3.0       2>/dev/null || missing_sys+=("libgtk-3-dev")
            # libspa-sys / pipewire crate(截图 / 屏幕捕获依赖)编译需要 pipewire 开发包
            pkg-config --exists libpipewire-0.3 2>/dev/null || missing_sys+=("libpipewire-0.3-dev")
        fi
        # libspa-sys 用 bindgen 生成 FFI 绑定,bindgen 运行期要加载 libclang.so(LLVM)
        ldconfig -p 2>/dev/null | grep -qi libclang || missing_sys+=("libclang-dev")
        if [[ ${#missing_sys[@]} -gt 0 ]]; then
            fail "Tauri Linux 编译缺系统依赖:${missing_sys[*]}"
            if command -v apt-get >/dev/null 2>&1; then
                note "Debian/Ubuntu 一条装齐:"
                note "  sudo apt-get install -y build-essential pkg-config libssl-dev libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev libxdo-dev libpipewire-0.3-dev libclang-dev"
            elif command -v dnf >/dev/null 2>&1; then
                note "Fedora 一条装齐:"
                note "  sudo dnf install -y @development-tools pkgconf-pkg-config openssl-devel webkit2gtk4.1-devel gtk3-devel libappindicator-gtk3-devel librsvg2-devel libxdo-devel pipewire-devel clang-devel"
            elif command -v pacman >/dev/null 2>&1; then
                note "Arch 一条装齐:"
                note "  sudo pacman -S --needed base-devel pkgconf openssl webkit2gtk-4.1 gtk3 libayatana-appindicator librsvg libxdo libpipewire clang"
            else
                note "用你发行版的包管理器装:C 编译器(gcc) + pkg-config + webkit2gtk-4.1 + gtk3 + appindicator + librsvg2 + libxdo + pipewire + libclang 的开发包"
            fi
            exit 127
        fi
        note "cc / pkg-config / webkit2gtk-4.1 / gtk3 / pipewire / libclang 就绪"
    fi
fi

# ── 4. 可选清理 ──────────────────────────────────────────────────
if [[ "$CLEAN" == "1" ]]; then
    step '清理'
    rm -rf "$SERVER_DIR/dist" 2>/dev/null && note "清 chayuan-server/dist" || true
    rm -rf "$TAURI_BUNDLE" 2>/dev/null && note "清 src-tauri/target/release/bundle" || true
    rm -rf "$WORKSPACE/dist-lite" "$WORKSPACE/dist-integrated" 2>/dev/null \
        && note "清 dist-lite/ + dist-integrated/" || true
fi

# ── 4b. bundled_models 体检(集成版需要,轻量版仅打印) ─────────────
MODEL_CHECK_OK=0      # 0 = 未跑 / 失败,1 = 至少 1 个 cap 有模型
if [[ "$SKIP_MODEL_CHECK" == "1" ]]; then
    step '模型体检 [skipped]'
else
    step 'bundled_models 体检 (本次打包会嵌入哪些模型)'
    check_script="$WORKSPACE/scripts/check-bundled-models.py"
    if [[ -f "$check_script" ]]; then
        if python3 "$check_script"; then
            MODEL_CHECK_OK=1
        else
            note "bundled_models 全空 — 集成版将退化为轻量版(都不嵌模型)"
        fi
    else
        note "$check_script 不存在,跳过"
    fi
fi

# 决策:整合版需要模型;空目录又显式 --integrated-only 时给警告但仍允许
for flav in "${FLAVORS[@]}"; do
    if [[ "$flav" == "integrated" ]] && [[ "$SKIP_MODEL_CHECK" != "1" ]] && [[ "$MODEL_CHECK_OK" != "1" ]]; then
        if [[ "$INTEGRATED_ONLY" == "1" ]]; then
            echo "${C_YELLOW}[WARN]${C_RESET} 集成版构建但 bundled_models 全空,产物效果上等同轻量版" >&2
        fi
    fi
done

ALL_ARTIFACTS=()    # 每个 flavor 产物:"flavor|src|dest|size_mb"

# 2026-05-15:sidecar 不再嵌 bundled_models(它们改走 Tauri resources),
# 所以两个 flavor 用同一份 sidecar 就够,**不再强制 rebuild**。flavor 切换
# 只需要重做 src-tauri/bundled_models/ 同步 + Tauri bundle,从而省 5-10 分钟。

# 把 Step 1 ~ Step 3 + 单 flavor 总结包成 do_build_flavor;主循环按 $FLAVORS 调一次或两次。
do_build_flavor() {
    local FLAVOR="$1"
    if [[ "$FLAVOR" == "lite" ]]; then
        export CHAYUAN_LITE_BUILD=1
        FLAVOR_LABEL="轻量版(lite)"
    else
        unset CHAYUAN_LITE_BUILD || true
        FLAVOR_LABEL="集成版(integrated)"
    fi

    echo ""
    step "═══ [$FLAVOR_LABEL] 开始 ═══"

# ── 5. Step 1: chayuan-server PyInstaller ─────────────────────────
#
# 指纹缓存:把 chayuan/*.py + packaging/pyinstaller/* + pyproject.toml + poetry.lock
# 合算 sha256,落在 chayuan-server/dist/ 下。下次构建时如果指纹没变,自动
# 跳过 PyInstaller(可省 5-10 分钟)。
#
# 注:stamp 不能放进 src-tauri/chayuan-server/ —— build.py 的
# copy_to_tauri_resources() 每次 PyInstaller 后会 rmtree 整个目录再 copytree,
# 放里面会被清掉(且会污染 Tauri ``resources`` glob)。放 chayuan-server/dist/
# 旁边,跟 build-desktop.ps1 的 .build-fingerprint-<triple>.json 同思路。
#
# - 强制重建:加 --rebuild-server 或删掉 stamp 文件。
# - 显式跳过:加 --skip-server(不算指纹,直接复用)。
fingerprint_file="$SERVER_DIR/dist/.sidecar-fingerprint-${TRIPLE}.sha256"
SERVER_FINGERPRINT=""
if [[ "$SKIP_SERVER" != "1" ]]; then
    SERVER_FINGERPRINT=$(
        cd "$SERVER_DIR" && {
            find libs/chayuan-server/chayuan packaging/pyinstaller \
                 -type f \( -name '*.py' -o -name '*.spec' -o -name '*.toml' \) \
                 -print0 2>/dev/null
            find . -maxdepth 1 -type f \( -name 'pyproject.toml' -o -name 'poetry.lock' \) \
                 -print0 2>/dev/null
        } | LC_ALL=C sort -z | xargs -0 shasum -a 256 2>/dev/null \
          | shasum -a 256 | awk '{print $1}'
    )

    if [[ "$REBUILD_SERVER" != "1" ]] \
       && [[ -n "$SERVER_FINGERPRINT" ]] \
       && [[ -f "$SIDECAR_PATH" ]] \
       && [[ -f "$fingerprint_file" ]] \
       && [[ "$SERVER_FINGERPRINT" == "$(cat "$fingerprint_file" 2>/dev/null)" ]]; then
        SKIP_SERVER=1
        SKIP_SERVER_AUTO=1
    fi
fi

if [[ "$SKIP_SERVER" == "1" ]]; then
    step 'Step 1 [skipped]'
    if [[ ! -f "$SIDECAR_PATH" ]]; then
        fail "Step 1 跳过了但 sidecar 不存在: $SIDECAR_PATH"
        note "去掉 --skip-server / 加 --rebuild-server 重跑,或先复制其它机器上同 triple 的 sidecar 进来。"
        exit 1
    fi
    # --onedir:报整个 chayuan-server/ 目录大小(含 _internal/),单 bootloader 没参考价值。
    sz=$(du -sm "$CHAYUAN_SERVER_DIR" | awk '{print $1}')
    if [[ "$SKIP_SERVER_AUTO" == "1" ]]; then
        ok "源码指纹未变,自动复用 sidecar: $SIDECAR_PATH (onedir 总计 ${sz} MB)"
        note "强制重建:--rebuild-server"
    else
        ok "复用现有 sidecar: $SIDECAR_PATH (onedir 总计 ${sz} MB)"
    fi
else
    step 'Step 1: chayuan-server PyInstaller'
    # 杀残留(上次 healthz 探测没干净时常见)
    pkill -f 'chayuan-server' 2>/dev/null && note "已清理残留 chayuan-server 进程" || true

    # 优先 poetry,无 poetry 用裸 python(假定 deps 已用 pip 装好)
    # 把 host triple 显式传给 build.py,确保 sync_services 按对的平台选 vendor 子目录
    if command -v poetry >/dev/null 2>&1; then
        # ── 装依赖(关键)──────────────────────────────────────────
        # PyInstaller 打的是「当前 venv 里装了什么」。真正的 Python 包(含
        # click / rich / tiktoken 等全部第三方依赖)在 libs/chayuan-server/;
        # 根目录 chayuan-server/pyproject.toml 是 monorepo 元数据
        # (package-mode=false,deps 只有 python),在那里跑 poetry install
        # 等于啥都没装,PyInstaller Analysis 会找不到 click → 打进去缺包 →
        # sidecar 运行时 ModuleNotFoundError。所以全部 poetry 调用都在
        # $SERVER_PKG_DIR(libs/chayuan-server/)跑,跟 build-desktop.ps1
        # 的 $ServerPkgDir 对齐。
        #
        # poetry.lock 跟 pyproject.toml 不同步时 Poetry 1.5+ 拒绝 install,
        # 先 lock 再 install。不带 --no-update:Poetry 2.x 已删该 flag,
        # 1.x 多点版本解析时间无伤。
        invoke_step 'poetry lock' "$SERVER_PKG_DIR" \
            poetry lock --no-interaction
        invoke_step 'poetry install (--only main)' "$SERVER_PKG_DIR" \
            poetry install --only main --no-interaction
        # 删过时 pathlib backport(若存在):这个废弃的 stdlib pathlib backport
        # 跟 PyInstaller 6+ 不兼容,会让打包报错。pip uninstall 找不到包时
        # 退出码非 0,用 `|| true` 兜住(没装过是正常情况)。
        invoke_step 'remove stale pathlib backport' "$SERVER_PKG_DIR" \
            bash -c 'poetry run pip uninstall -y pathlib || true'
        # PyInstaller 本身由 build.py 的 _ensure_pyinstaller() 兜底自动安装,
        # 这里不重复装,避免与 build.py 冲突。
        #
        # build.py 路径相对 $SERVER_PKG_DIR:../../packaging/pyinstaller/build.py。
        # 必须在 $SERVER_PKG_DIR 跑,poetry run 才命中刚装好依赖的那个 env。
        invoke_step 'pyinstaller (poetry)' "$SERVER_PKG_DIR" \
            poetry run python ../../packaging/pyinstaller/build.py --target "$TRIPLE"
    else
        # 裸 python3 分支:没有 poetry 就没法自动装 chayuan-server 依赖。
        # PyInstaller 打的是当前解释器 venv 里的包,缺 click/rich/tiktoken
        # 等会导致 sidecar 运行时 ModuleNotFoundError。这里先校验依赖是否
        # 就绪,缺了就 fail-fast 给出明确安装命令,不静默打出缺包的产物。
        if ! python3 -c "import PyInstaller" 2>/dev/null; then
            fail "python3 找不到 PyInstaller。请先 'pip install pyinstaller>=6.0' 或装 poetry。"
            exit 127
        fi
        if ! python3 -c "import click" 2>/dev/null; then
            fail "python3 找不到 chayuan-server 依赖(如 'click')。"
            note "无 poetry 时本脚本不会自动装依赖。请先在当前 python3 环境装好 chayuan-server:"
            note "  python3 -m pip install -e \"$SERVER_PKG_DIR\""
            note "或安装 poetry 让脚本自动装依赖:pip install 'poetry>=1.8'"
            exit 127
        fi
        invoke_step 'pyinstaller (python3)' "$SERVER_DIR" \
            python3 packaging/pyinstaller/build.py --target "$TRIPLE"
    fi

    if [[ ! -f "$SIDECAR_PATH" ]]; then
        fail "PyInstaller 跑完了但 sidecar 没出现在: $SIDECAR_PATH"
        note "build.py(--onedir)应把整个 dist/chayuan-server/ 拷到 src-tauri/chayuan-server/;"
        note "看看 $SERVER_DIR/dist/ 里实际生成了啥,以及 build.py::copy_to_tauri_resources。"
        exit 1
    fi
    chmod +x "$SIDECAR_PATH"
    # --onedir 下 $SIDECAR_PATH 只是 bootloader(几 MB),整个 bundle 在同目录
    # _internal/ 子目录;报整个 chayuan-server/ 目录大小才有意义。
    sz=$(du -sm "$CHAYUAN_SERVER_DIR" | awk '{print $1}')
    ok "Sidecar 已就位: $SIDECAR_PATH (onedir 总计 ${sz} MB)"

    # 把这次构建用的源码指纹落盘 —— 下次同样指纹会直接命中自动跳过。
    if [[ -n "$SERVER_FINGERPRINT" ]]; then
        mkdir -p "$(dirname "$fingerprint_file")"
        echo "$SERVER_FINGERPRINT" > "$fingerprint_file"
        note "源码指纹已记录:$fingerprint_file"
    fi
fi

# ── 5b. 同步 bundled_models 到 Tauri resources ───────────────────
# build.py 的 sync_bundled_models() 会清空 src-tauri/bundled_models/,然后
# 按 CHAYUAN_LITE_BUILD env 决定是清空(轻量版)还是拷整树(集成版)。
# sidecar 自身不嵌模型,所以 Step 1 不需要 rebuild;只这一步随 flavor 切。
step "Step 1b: 同步 src-tauri/bundled_models/ ($FLAVOR)"
if command -v poetry >/dev/null 2>&1; then
    invoke_step "sync bundled (poetry)" "$SERVER_DIR" \
        poetry run python packaging/pyinstaller/build.py --sync-bundled-only --target "$TRIPLE"
else
    invoke_step "sync bundled (python3)" "$SERVER_DIR" \
        python3 packaging/pyinstaller/build.py --sync-bundled-only --target "$TRIPLE"
fi

# ── 5c. Step 1c: 同步诊断脚本到 src-tauri/tools/ ──────────────────
# 把 scripts/diagnose.sh 拷进 src-tauri/tools/,Tauri 据 tauri.conf.json 的
# resources "tools/**/*" 把它打进 installer,已装用户原地可跑诊断,无需
# clone 整个仓库。对应 build-desktop.ps1 的 Step 1c(Windows 拷 .ps1)——
# 2f574b0 只给 ps1 加了这步、漏了 sh,导致 Linux/macOS 打包必挂。
#
# 关键:"tools/**/*" 是所有平台共用的 glob,Tauri 在它匹配 0 文件时会硬
# 报错("glob pattern tools/**/* ... didn't match any files")直接 fail。
# 故无论 diagnose.sh 在不在,都先 touch 一个 .placeholder 兜底,保证 glob
# 永不为空 —— 与 bundled_models/ services/ 的 .placeholder 做法一致。
step "Step 1c: 同步诊断脚本到 src-tauri/tools/"
tools_dst="$DESKTOP_DIR/src-tauri/tools"
mkdir -p "$tools_dst"
touch "$tools_dst/.placeholder"
diag_src="$WORKSPACE/scripts/diagnose.sh"
if [[ -f "$diag_src" ]]; then
    cp -f "$diag_src" "$tools_dst/diagnose.sh"
    chmod +x "$tools_dst/diagnose.sh"
    note "  copied $diag_src -> $tools_dst/diagnose.sh"
else
    note "  scripts/diagnose.sh 不存在,跳过(installer 里只留 .placeholder)"
fi

# ── 6. Step 2: /healthz 探测 ─────────────────────────────────────
if [[ "$SKIP_HEALTHCHECK" == "1" ]]; then
    step 'Step 2 [skipped]'
else
    step 'Step 2: sidecar /healthz 探测'
    tmp_data="$(mktemp -d -t chayuan-health-XXXXXX)"
    export CHAYUAN_ROOT="$tmp_data"
    # chayuan 优先读 state.json.last_init_root(开发机上一次 chayuan init 写的),
    # 不设这个 env 时 $CHAYUAN_ROOT 会被无视。详见 chayuan/paths.py:120 注释。
    export CHAYUAN_ROOT_IGNORE_STATE=1
    note "临时数据目录(测完即删):$tmp_data"

    stdout_log="$(mktemp -t chayuan-health-stdout-XXXXXX.log)"
    stderr_log="$(mktemp -t chayuan-health-stderr-XXXXXX.log)"

    "$SIDECAR_PATH" start -a --single-machine \
        > "$stdout_log" 2> "$stderr_log" &
    sidecar_pid=$!
    note "PID=$sidecar_pid;最长等 360s 直到 /healthz 200(onefile 解压 + bootstrap 慢;首次启动 matplotlib 还要扫系统字体目录建缓存)"

    deadline=$(( $(date +%s) + 360 ))
    ready=0
    while [[ $(date +%s) -lt $deadline ]]; do
        if ! kill -0 "$sidecar_pid" 2>/dev/null; then
            break  # 子进程已退出,跳出等待
        fi
        if curl -fsS --max-time 2 'http://127.0.0.1:62581/healthz' >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 0.8
    done

    # 杀整棵进程树
    if kill -0 "$sidecar_pid" 2>/dev/null; then
        kill -TERM "$sidecar_pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$sidecar_pid" 2>/dev/null || true
    fi
    pkill -f 'chayuan-server' 2>/dev/null || true
    rm -rf "$tmp_data" 2>/dev/null || true

    if [[ "$ready" == "1" ]]; then
        ok 'sidecar 360s 内 /healthz 返 200'
        rm -f "$stdout_log" "$stderr_log"
    else
        fail 'sidecar 启动失败或 /healthz 不响应。stderr 尾部:'
        tail -n 40 "$stderr_log" | sed 's/^/    /'
        note "stdout 日志: $stdout_log"
        note "stderr 日志: $stderr_log"
        exit 1
    fi
fi

# ── 7. Step 3: chayuan-client Tauri bundle ────────────────────────
if [[ "$SKIP_FRONTEND" == "1" ]]; then
    step 'Step 3 [skipped]'
else
    step 'Step 3: chayuan-client Tauri bundle'

    if [[ "$SKIP_INSTALL" == "1" ]]; then
        skip 'Step 3a [pnpm install] (--skip-install)'
    else
        invoke_step 'pnpm install' "$CLIENT_DIR" pnpm install --frozen-lockfile
    fi

    if [[ "$SKIP_TYPECHECK" == "1" ]]; then
        skip 'Step 3b [pnpm typecheck] (--skip-typecheck)'
    else
        invoke_step 'pnpm typecheck' "$CLIENT_DIR" pnpm typecheck
    fi

    if [[ "$BUNDLE_ONLY" == "1" ]]; then
        # cargo / vite 走增量缓存,只重做 Tauri bundle(makensis / dmg / deb / appimage 等)
        invoke_step 'tauri build (bundle-only)' "$DESKTOP_DIR" \
            pnpm exec tauri build
    else
        # 打包前完全清理 Rust target/ —— 每次打包都是干净的全量冷构建。
        # 代价:Cargo 增量编译缓存被清,本次 tauri build 会全量重编依赖
        # (Tauri 应用约 +10~30 分钟)。要快速增量打包:用 --bundle-only
        # (复用缓存)或 --skip-clean-target 跳过本步。
        if [[ "$SKIP_CLEAN_TARGET" != "1" ]]; then
            rust_target="$DESKTOP_DIR/src-tauri/target"
            if [[ -d "$rust_target" ]]; then
                step '打包前清理 target/(全量冷构建)'
                if rm -rf "$rust_target" 2>/dev/null && [[ ! -d "$rust_target" ]]; then
                    note "已删除 $rust_target"
                else
                    # 个别文件被占用 rm 删不净 —— 退回 cargo clean 兜底
                    ( cd "$DESKTOP_DIR/src-tauri" && cargo clean 2>/dev/null ) \
                        && note 'cargo clean 兜底完成' \
                        || note 'target/ 清理可能不彻底,关掉占用该目录的进程后重试'
                fi
            fi
        fi
        invoke_step 'pnpm build:desktop' "$CLIENT_DIR" pnpm build:desktop
    fi

    # 列出产物 — Tauri 默认输出文件名已经带架构后缀。
    # ⚠ mac 的 .app 是目录(<bundle>/macos/Chayuan.app/Contents/...),
    # 拿掉 dmg target 后 mac 只剩 .app —— 这里要把目录也算 artifact。
    # 顺手 nsis sub 也加进来:Windows 装机用的 nsis installer 落在
    # bundle/nsis/ 不在原列表里,装机分发时会漏。
    artifacts=()
    if [[ -d "$TAURI_BUNDLE" ]]; then
        for sub in dmg macos deb appimage rpm nsis msi; do
            for f in "$TAURI_BUNDLE/$sub"/* ; do
                # 普通文件:任何后缀都收 (.dmg/.deb/.AppImage/.rpm/.exe/.msi)
                # 目录形态:只收 *.app(mac bundle)—— deb 解包临时目录之类
                # 不能误当 artifact
                if [[ -f "$f" ]] || [[ -d "$f" && "$f" == *.app ]]; then
                    artifacts+=("$f")
                fi
            done
        done
    fi
    if [[ "${#artifacts[@]}" == "0" ]]; then
        fail "Tauri bundle 没产出文件;看 $TAURI_BUNDLE"
        exit 1
    fi
    ok 'Tauri bundle 完成:'
    flavor_out="$WORKSPACE/dist-$FLAVOR"
    rm -rf "$flavor_out"
    mkdir -p "$flavor_out"
    for a in "${artifacts[@]}"; do
        sz=$(du -m "$a" | awk '{print $1}')
        base="$(basename "$a")"
        # 给文件名加 flavor 后缀,避免双 flavor 同名互相覆盖
        stem="${base%.*}" ; ext="${base##*.}"
        if [[ "$stem" == "$base" ]]; then  # 无扩展(罕见)
            dest_name="${base}.${FLAVOR}"
        else
            dest_name="${stem}.${FLAVOR}.${ext}"
        fi
        dest="$flavor_out/$dest_name"
        # ⚠ .app 是目录 cp -f 拷不动,要 -R 递归;-a 顺带保留权限和软链。
        # 普通文件用 cp -af 也工作(单文件时 -R 不生效但无害)。
        cp -af "$a" "$dest"
        ALL_ARTIFACTS+=("$FLAVOR|$a|$dest|$sz")
        echo "    $a → $dest (${sz} MB)"
    done

    # mac:从 .app 再产 .dmg —— Tauri 内置 bundle_dmg.sh 在新 macOS 容易挂
    # (Privacy/Automation 权限、hdiutil 版本兼容),改用 scripts/make-mac-dmg.sh
    # 走 hdiutil + Applications 软链的最小路径。生成放在 flavor_out 里,跟 .app
    # 并排。失败只警告不阻塞(.app 仍是有效产物)。
    #
    # 命名对齐 Tauri 默认产物风格:`Chayuan_<version>_<arch>.<flavor>.dmg`,
    # 跟 windows NSIS `Chayuan_0.1.0_x64-setup.<flavor>.exe` 同 schema。
    if [[ "$(uname -s)" = "Darwin" ]]; then
        make_dmg="$WORKSPACE/scripts/make-mac-dmg.sh"
        if [[ -x "$make_dmg" ]]; then
            # 从 tauri.conf.json 拿版本号(真源,跟 Tauri 自己生成 DMG 名同一字段)
            tauri_conf="$CLIENT_DIR/apps/desktop/src-tauri/tauri.conf.json"
            app_version="$(python3 -c "import json,sys; print(json.load(open('$tauri_conf'))['version'])" 2>/dev/null || echo "")"
            # arch:Tauri mac 用 aarch64 / x86_64(对齐 rust target_arch),
            # 而不是 uname -m 的 arm64
            arch_raw="$(uname -m)"
            case "$arch_raw" in
                arm64|aarch64) app_arch="aarch64" ;;
                x86_64|amd64)  app_arch="x86_64"  ;;
                *)             app_arch="$arch_raw" ;;
            esac
            for src_app in "$flavor_out"/*.app; do
                [[ -d "$src_app" ]] || continue
                app_base="$(basename "$src_app")"   # 例:Chayuan.lite.app
                app_stem="${app_base%.app}"          # 例:Chayuan.lite
                # 拆 flavor 后缀(Chayuan.lite → app_name=Chayuan, flavor_seg=lite)
                if [[ "$app_stem" == *.* ]]; then
                    app_name="${app_stem%.*}"
                    flavor_seg=".${app_stem##*.}"
                else
                    app_name="$app_stem"
                    flavor_seg=""
                fi
                # 拼回 Tauri 风格:Chayuan_0.1.0_aarch64.lite.dmg
                if [[ -n "$app_version" ]]; then
                    dmg_name="${app_name}_${app_version}_${app_arch}${flavor_seg}.dmg"
                else
                    # 版本读取失败兜底:不带版本,但仍带 arch
                    dmg_name="${app_name}_${app_arch}${flavor_seg}.dmg"
                fi
                dmg_dest="$flavor_out/$dmg_name"
                step "生成 DMG: $dmg_name"
                if bash "$make_dmg" "$src_app" "$dmg_dest" "Chayuan"; then
                    dmg_sz=$(du -m "$dmg_dest" | awk '{print $1}')
                    ALL_ARTIFACTS+=("$FLAVOR|$dmg_dest|$dmg_dest|$dmg_sz")
                    ok "DMG 完成 → $dmg_dest (${dmg_sz} MB)"
                else
                    note "DMG 生成失败,跳过(.app 仍可用)"
                fi
            done
        else
            note "scripts/make-mac-dmg.sh 不存在或不可执行,跳过 DMG"
        fi
    fi
fi

}   # end do_build_flavor

# ── 主循环:按 $FLAVORS 跑一次或两次 ─────────────────────────────
for flav in "${FLAVORS[@]}"; do
    do_build_flavor "$flav"
done

# ── 8. 总结 ──────────────────────────────────────────────────────
step '完成'
note "host: $OS_NAME / $ARCH ($TRIPLE)"
note "本次构建 flavor:${FLAVORS[*]}"
if [[ "$SKIP_FRONTEND" != "1" ]]; then
    for flav in "${FLAVORS[@]}"; do
        note "$flav 产物目录:$WORKSPACE/dist-$flav/"
    done
fi
if [[ "${#ALL_ARTIFACTS[@]}" -gt 0 ]]; then
    echo ""
    echo "${C_BOLD}本次产物清单${C_RESET}"
    printf "  %-12s %-50s %s\n" "flavor" "file" "size"
    for line in "${ALL_ARTIFACTS[@]}"; do
        IFS='|' read -r f _src dest sz <<< "$line"
        printf "  %-12s %-50s %s MB\n" "$f" "$(basename "$dest")" "$sz"
    done
fi
echo ""
echo "${C_BOLD}${C_YELLOW}⚠ 本次产物只能跑在 ${ARCH} 架构机器上${C_RESET}"
echo "  其它架构请在对应 CPU 机器上重跑本脚本(或 CI 双 runner)。"
if [[ "${#FLAVORS[@]}" -eq 2 ]]; then
    echo ""
    echo "  ${C_BOLD}轻量版${C_RESET}:不嵌模型,装机包小,需联网下载或扫盘配置"
    echo "  ${C_BOLD}集成版${C_RESET}:vendor/bundled_models/ 进 Tauri resources,装机即用(模型清单见上方"
    echo "                  '模型体检'输出,或 chayuan-server/vendor/bundled_models/README.md)"
fi
echo ""
echo "${C_GREEN}[OK] 全部步骤通过${C_RESET}"
