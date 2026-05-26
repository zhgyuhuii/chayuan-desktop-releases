#!/usr/bin/env bash
# install-vendor.sh — 一站式装齐 chayuan-server/vendor/(Linux / macOS 版)
#
# 对标 scripts/install-vendor.ps1,把打全量包前要塞进 vendor/ 的资源一次装齐:
#   模型 + llama-server binary + whisper-server binary + torch wheels。
#
# ───────────────────── 跨平台能力(重要,先读)─────────────────────
#
#   组件            能否从单台 host 跨平台下全                   说明
#   ────────────    ─────────────────────────────────────────   ──────────────
#   模型            ✅ 平台无关,跑一次全平台通用                GGUF/ONNX
#   llama-server    ✅ linux-x64 / macos-x64 / macos-arm64       curl GitHub zip
#                   ❌ linux-arm64                               upstream 没发 zip
#   whisper-server  ❌ 只能装 HOST 平台                          upstream 不发
#                                                                Linux/Mac binary,
#                                                                只能 brew/docker/
#                                                                源码编译
#   torch wheels    ✅ 4 个平台全可下                            pip download
#                                                                --platform
#
# 结论:本脚本一次能凑齐【模型 + 3 平台 llama-server + 4 平台 torch wheels】。
#       缺口需到对应机器补:
#         - whisper-server 非 host 平台 → 在那台机器跑 install-whisper-server.sh
#         - llama-server linux-arm64    → docker 提取或 ARM Linux 上源码 build
#                                         (见 install-llama-server.sh linux-arm64 分支)
#
# ───────────────────────────── 用法 ────────────────────────────────
#
#   bash scripts/install-vendor.sh [选项]
#
#   --flavor lite|full     模型范围。lite≈1GB 子集;full 全量。默认 lite
#   --components LIST       逗号分隔:models,llama,whisper,torch,all,none
#                           默认 all
#   --source auto|hf|modelscope
#                           模型下载源,透传 install-bundled-models.py。
#                           默认 auto(HF→MS fallback);国内卡 Xet 用 modelscope
#   --targets LIST          llama-server 要下哪些平台,逗号分隔。
#                           默认 linux-x64,linux-arm64,macos-x64,macos-arm64
#   --py-versions LIST      torch wheels 的 Python 版本,逗号分隔(无点)。
#                           默认 312
#   --github-mirror URL     GitHub 镜像前缀。墙内 github.com 不通时用 —
#                           services 二进制只在 GitHub release 发。
#                           例:--github-mirror https://gh-proxy.com/
#                           不传也行:检测到 github.com 不通会自动从内置
#                           清单(gh-proxy.com / ghfast.top)探一个可用的。
#   --dry-run               只打印将执行的命令,不真跑
#   -h|--help               显示本说明
#
# 例:
#   bash scripts/install-vendor.sh                       # lite,全组件
#   bash scripts/install-vendor.sh --flavor full         # full 模型(墙内自动选镜像)
#   bash scripts/install-vendor.sh --components llama,torch   # 只补 binary+wheel
#   bash scripts/install-vendor.sh --source modelscope   # 模型走 MS 镜像
#   bash scripts/install-vendor.sh --github-mirror https://gh-proxy.com/  # 强制指定镜像
set -uo pipefail

# ───────────────────────────── 默认值 ──────────────────────────────
FLAVOR="lite"
COMPONENTS="all"
SOURCE="auto"
TARGETS="linux-x64,linux-arm64,macos-x64,macos-arm64"
PY_VERSIONS="312"
DRY_RUN=0
# 不传 --github-mirror 时沿用调用方已 export 的 GITHUB_MIRROR(可能为空)
GITHUB_MIRROR="${GITHUB_MIRROR:-}"

# ───────────────────────────── 参数解析 ────────────────────────────
while [ $# -gt 0 ]; do
    case "$1" in
        --flavor)       FLAVOR="$2"; shift 2 ;;
        --flavor=*)     FLAVOR="${1#*=}"; shift ;;
        --components)   COMPONENTS="$2"; shift 2 ;;
        --components=*) COMPONENTS="${1#*=}"; shift ;;
        --source)       SOURCE="$2"; shift 2 ;;
        --source=*)     SOURCE="${1#*=}"; shift ;;
        --targets)      TARGETS="$2"; shift 2 ;;
        --targets=*)    TARGETS="${1#*=}"; shift ;;
        --py-versions)   PY_VERSIONS="$2"; shift 2 ;;
        --py-versions=*) PY_VERSIONS="${1#*=}"; shift ;;
        --github-mirror)   GITHUB_MIRROR="$2"; shift 2 ;;
        --github-mirror=*) GITHUB_MIRROR="${1#*=}"; shift ;;
        --dry-run)      DRY_RUN=1; shift ;;
        -h|--help)      grep -E '^#' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "[install-vendor] 未知参数 $1;-h 看用法" >&2; exit 64 ;;
    esac
done

case "$FLAVOR" in lite|full) ;; *)
    echo "[install-vendor] --flavor 只能是 lite / full" >&2; exit 64 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(dirname "$HERE")"
SERVER_DIR="$WORKSPACE/chayuan-server"

# 解析 python
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    echo "[install-vendor] 找不到 python3 / python,无法下载模型 / torch wheels" >&2
    exit 1
fi

# host 平台检测(whisper-server 只能装 host)
detect_host_target() {
    case "$(uname -s)" in
        Linux)  case "$(uname -m)" in aarch64|arm64) echo "linux-arm64";; *) echo "linux-x64";; esac ;;
        Darwin) case "$(uname -m)" in arm64|aarch64) echo "macos-arm64";; *) echo "macos-x64";; esac ;;
        *) echo "" ;;
    esac
}
HOST_TARGET="$(detect_host_target)"

# 组件开关
want() { case ",$COMPONENTS," in *",all,"*) return 0;; *",$1,"*) return 0;; *) return 1;; esac; }
if [ "$COMPONENTS" = "none" ]; then
    echo "[install-vendor] --components none,什么都不做"; exit 0
fi

# 探一个 URL 是否可达(curl HEAD,2xx/3xx/4xx 都算"网络通了")
probe_url() {
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "${2:-8}" -I "$1" 2>/dev/null)" || return 1
    case "$code" in 2*|3*|4*) return 0 ;; *) return 1 ;; esac
}

# GitHub 镜像:services 要从 GitHub release 下二进制,github.com 常被墙。
# 优先级:--github-mirror / $GITHUB_MIRROR(显式)> github.com 直连
#         > 自动探内置镜像(gh-proxy.com / ghfast.top,2026-05 实测可用;
#           老的 ghproxy.com 已废弃,不要再用)。
if want services && [ -z "$GITHUB_MIRROR" ]; then
    if probe_url "https://github.com/" 6; then
        : # github.com 直连可达,不用镜像
    else
        echo "[install-vendor] github.com 直连不可达,自动探测 GitHub 镜像..."
        for cand in "https://gh-proxy.com/" "https://ghfast.top/"; do
            if probe_url "$cand" 8; then
                GITHUB_MIRROR="$cand"
                echo "[install-vendor] 自动选用 GitHub 镜像:$cand"
                break
            fi
        done
        if [ -z "$GITHUB_MIRROR" ]; then
            echo "[install-vendor] 内置镜像 gh-proxy.com / ghfast.top 也不可达 — services 阶段可能失败" >&2
        fi
    fi
fi
# GitHub 镜像 export 给 install-llama-server.sh / install-whisper-server.sh,
# 它们读 $GITHUB_MIRROR 把下载 / git clone URL 套上镜像前缀。
if [ -n "$GITHUB_MIRROR" ]; then
    export GITHUB_MIRROR
    echo "[install-vendor] GitHub 镜像:$GITHUB_MIRROR (services 下载走镜像)"
fi

# ───────────────────────────── 工具函数 ────────────────────────────
declare -a RESULTS=()
record() { RESULTS+=("$1|$2"); }

step() { echo; echo "════════ $* ════════"; }

run() {
    echo "  + $*"
    if [ "$DRY_RUN" = "1" ]; then return 0; fi
    "$@"
}

# ───────────────────────────── 1. 模型 ─────────────────────────────
if want models; then
    step "1/4 模型 → vendor/bundled_models/"
    MODEL_ARGS=()
    [ "$FLAVOR" = "lite" ] && MODEL_ARGS+=(--lite)
    MODEL_ARGS+=(--source "$SOURCE")
    if run "$PY" "$HERE/install-bundled-models.py" "${MODEL_ARGS[@]}"; then
        record "models($FLAVOR)" OK
    else
        record "models($FLAVOR)" FAIL
    fi
fi

# ───────────────────────────── 2. llama-server ─────────────────────
if want llama; then
    step "2/4 llama-server → vendor/services/llama-server/<platform>/"
    IFS=',' read -ra LLAMA_TARGETS <<< "$TARGETS"
    for t in "${LLAMA_TARGETS[@]}"; do
        t="$(echo "$t" | xargs)"  # trim
        [ -z "$t" ] && continue
        echo "  --- llama-server target=$t ---"
        if run bash "$HERE/install-llama-server.sh" --target "$t"; then
            record "llama:$t" OK
        else
            # linux-arm64 upstream 没 zip,失败是预期内 — 标 SKIP 不算 FAIL
            if [ "$t" = "linux-arm64" ]; then
                record "llama:$t" "SKIP(upstream无zip,需docker/源码)"
            else
                record "llama:$t" FAIL
            fi
        fi
    done
fi

# ───────────────────────────── 3. whisper-server ───────────────────
if want whisper; then
    step "3/4 whisper-server → vendor/services/whisper-server/<platform>/"
    if [ -n "$HOST_TARGET" ]; then
        echo "  whisper-server 只能装本机平台(upstream 不发 Linux/Mac binary)"
        echo "  --- whisper-server host=$HOST_TARGET ---"
        if run bash "$HERE/install-whisper-server.sh"; then
            record "whisper:$HOST_TARGET(host)" OK
        else
            record "whisper:$HOST_TARGET(host)" FAIL
        fi
    else
        record "whisper" "SKIP(非Linux/Mac host)"
    fi
    # 提示其它平台
    IFS=',' read -ra WANT_TARGETS <<< "$TARGETS"
    for t in "${WANT_TARGETS[@]}"; do
        t="$(echo "$t" | xargs)"
        case "$t" in linux-*|macos-*) ;; *) continue ;; esac
        if [ "$t" != "$HOST_TARGET" ]; then
            record "whisper:$t" "TODO(到该机器跑 install-whisper-server.sh)"
        fi
    done
fi

# ───────────────────────────── 4. torch wheels ─────────────────────
if want torch; then
    step "4/4 torch wheels → vendor/torch_wheels/<platform_py_var>/"
    PREFLIGHT="$SERVER_DIR/packaging/preflight_torch.py"
    if [ ! -f "$PREFLIGHT" ]; then
        record "torch" "FAIL(找不到 $PREFLIGHT)"
    else
        # pip download --platform 可跨平台下:Linux/Mac 4 个 tag 全要
        TORCH_PLATS="manylinux2014_x86_64,manylinux2014_aarch64,macosx_10_9_x86_64,macosx_11_0_arm64"
        if run "$PY" "$PREFLIGHT" \
                --platforms "$TORCH_PLATS" \
                --variants cpu \
                --py-versions "$PY_VERSIONS" \
                --allow-missing; then
            record "torch(4平台)" OK
        else
            record "torch(4平台)" FAIL
        fi
    fi
fi

# ───────────────────────────── 总结 ────────────────────────────────
step "总结"
ANY_FAIL=0
for r in "${RESULTS[@]}"; do
    name="${r%%|*}"
    status="${r#*|}"
    case "$status" in
        OK)        printf '  ✓ %-32s %s\n' "$name" "$status" ;;
        FAIL*)     printf '  ✗ %-32s %s\n' "$name" "$status"; ANY_FAIL=1 ;;
        *)         printf '  • %-32s %s\n' "$name" "$status" ;;
    esac
done

echo
echo "缺口补法:"
echo "  whisper-server 非 host 平台 → 到对应机器跑 bash scripts/install-whisper-server.sh"
echo "  llama-server   linux-arm64  → docker 提取或 ARM Linux 源码 build"
echo "                                (bash scripts/install-llama-server.sh --target linux-arm64 会打印指引)"

if [ "$ANY_FAIL" = "1" ]; then
    echo
    echo "[install-vendor] 有阶段失败:"
    echo "  models 失败  → 换 --source modelscope;或外网机器跑 install-bundled-models.py 拷过来"
    echo "  llama 失败   → GitHub release 限速/网络;外网机器跑 install-llama-server.sh 拷 vendor/services/ 过来"
    echo "  torch 失败   → 看 preflight_torch.py stdout;pip 源 / 网络问题"
    exit 1
fi

echo
echo "[install-vendor] vendor/ 资源就绪。打包:bash build-desktop.sh --integrated-only"
exit 0
