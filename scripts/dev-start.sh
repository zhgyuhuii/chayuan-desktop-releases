#!/usr/bin/env bash
# dev-start.sh — 一键启动 chayuan-server 开发环境(macOS / Linux)
#
# 做什么:
#   1. 检测 host 平台 → 用对的 vendor/services/<engine>/<platform>/ 子目录
#   2. 检查 Python (poetry / pip) + chayuan-server 已 editable install
#   3. 检查 vendor binary(llama-server / whisper-server)就位,缺时给指引
#   4. 检查 CHAYUAN_ROOT 数据目录可写,默认 ~/.chayuan-dev
#   5. 顺手起 chayuan-server start -a --single-machine,前台运行
#
# 用法:
#   bash scripts/dev-start.sh                       # 默认前台跑
#   bash scripts/dev-start.sh --bg                  # 后台跑,PID 写到 /tmp/chayuan-dev.pid
#   bash scripts/dev-start.sh --check-only          # 只 preflight,不启
#   bash scripts/dev-start.sh --port 62581          # 自定义 API 端口
#   CHAYUAN_VENDOR_PLATFORM=linux-x64 bash ...      # 强制 vendor 平台
#   CHAYUAN_ROOT=/tmp/dev bash ...                  # 自定义数据目录
set -euo pipefail

# ────────────────────────── 编码:确保终端 UTF-8 ────────────────
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"
export PYTHONIOENCODING="utf-8"

# ────────────────────────── 参数解析 ────────────────────────────
BG=0
CHECK_ONLY=0
PORT="62581"
while [ $# -gt 0 ]; do
    case "$1" in
        --bg) BG=1; shift ;;
        --check-only) CHECK_ONLY=1; shift ;;
        --port) PORT="$2"; shift 2 ;;
        --port=*) PORT="${1#*=}"; shift ;;
        -h|--help)
            grep -E '^#' "$0" | sed 's/^# \?//' | head -25
            exit 0 ;;
        *) echo "[dev-start] 未知参数 $1" >&2; exit 64 ;;
    esac
done

# ────────────────────────── 输出辅助 ────────────────────────────
RED=$(printf '\033[0;31m'); GREEN=$(printf '\033[0;32m')
YELLOW=$(printf '\033[1;33m'); BOLD=$(printf '\033[1m'); RESET=$(printf '\033[0m')
ok()    { echo "${GREEN}✓${RESET} $*"; }
warn()  { echo "${YELLOW}⚠${RESET} $*" >&2; }
err()   { echo "${RED}✗${RESET} $*" >&2; }
step()  { echo "${BOLD}→${RESET} $*"; }

# ────────────────────────── 定位仓库 ────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER="$REPO/chayuan-server"

cd "$REPO"
step "工作目录:$REPO"

# ────────────────────────── 1. host 平台检测 ────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
    Darwin)
        case "$ARCH" in
            arm64|aarch64) PLAT="macos-arm64" ;;
            *)             PLAT="macos-x64" ;;
        esac ;;
    Linux)
        case "$ARCH" in
            aarch64|arm64) PLAT="linux-arm64" ;;
            *)             PLAT="linux-x64" ;;
        esac ;;
    *) err "不支持的 OS:$OS。Windows 请用 scripts/dev-start.ps1"; exit 1 ;;
esac
PLAT="${CHAYUAN_VENDOR_PLATFORM:-$PLAT}"
ok "host:$OS $ARCH → vendor 子目录 = $PLAT"

# ────────────────────────── 2. Python + chayuan 包 ──────────────
step "Step 1/4: 检查 Python 环境"
PYTHON_BIN=""

# 实测:Python 3.13 + chayuan-server 多进程子 worker 100% SIGSEGV(C 扩展不兼容)。
# 强烈优先 3.12.x;3.13 退化为最后兜底并 warn。
pick_python() {
    local cand="$1"
    [ -x "$cand" ] || return 1
    local ver
    ver="$("$cand" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)"
    case "$ver" in
        3.10|3.11|3.12) echo "$ver"; return 0 ;;
        3.13) echo "3.13-bad"; return 0 ;;   # 仍能 import 但跑 chayuan 会 SIGSEGV
        *) return 1 ;;
    esac
}

# 候选优先级:CHAYUAN_PYTHON env > conda 所有 env(任意名字) > 系统 / pyenv >
# poetry venv > python3(任意)
# `set -e` 在 command -v 找不到时会跳;关掉
set +e
declare -a candidates=()
[ -n "${CHAYUAN_PYTHON:-}" ] && candidates+=("$CHAYUAN_PYTHON")

# 1) 找 conda(PATH 或常见安装路径)
CONDA_EXE=""
for c in conda mamba micromamba; do
    p="$(command -v $c 2>/dev/null)"
    [ -n "$p" ] && CONDA_EXE="$p" && break
done
if [ -z "$CONDA_EXE" ]; then
    for p in \
        "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" \
        "$HOME/miniforge3/bin/conda" "$HOME/mambaforge/bin/conda" \
        /opt/conda/bin/conda /opt/miniconda3/bin/conda /opt/anaconda3/bin/conda \
        /root/miniconda3/bin/conda; do
        [ -x "$p" ] && CONDA_EXE="$p" && break
    done
fi

# 2) 用 conda 自身枚举所有 env(env 名 / 路径 / 别的发行版都不依赖)
if [ -n "$CONDA_EXE" ]; then
    while IFS= read -r envpath; do
        [ -n "$envpath" ] && [ -x "$envpath/bin/python3" ] && candidates+=("$envpath/bin/python3")
    done < <("$CONDA_EXE" info --json 2>/dev/null | \
             python3 -c 'import json,sys;d=json.load(sys.stdin);[print(e) for e in d.get("envs",[])];p=d.get("root_prefix");p and print(p)' 2>/dev/null)
fi

# 3) 通用安装位置
candidates+=(
    "$HOME/miniconda3/envs/py312/bin/python3"
    "$HOME/miniforge3/envs/py312/bin/python3"
    "$HOME/mambaforge/envs/py312/bin/python3"
    "$HOME/anaconda3/envs/py312/bin/python3"
    "/opt/conda/envs/py312/bin/python3"
    "$HOME/.pyenv/versions/3.12.13/bin/python3"
)

# 4) PATH 搜索
for exe in python3.12 python3.11 python3.10 python3; do
    p="$(command -v $exe 2>/dev/null)"
    [ -n "$p" ] && candidates+=("$p")
done

# 5) poetry venv
if command -v poetry >/dev/null 2>&1; then
    POETRY_VENV="$(cd "$SERVER" && poetry env info --path 2>/dev/null)"
    [ -n "$POETRY_VENV" ] && [ -x "$POETRY_VENV/bin/python" ] && candidates+=("$POETRY_VENV/bin/python")
fi

# 去重
declare -a uniq_candidates=()
declare -A seen=()
for c in "${candidates[@]}"; do
    [ -z "$c" ] && continue
    if [ -z "${seen[$c]:-}" ]; then
        seen[$c]=1
        uniq_candidates+=("$c")
    fi
done

declare -a probed=()
for c in "${uniq_candidates[@]}"; do
    [ -x "$c" ] || continue
    pyver=$(pick_python "$c")
    if [ -z "$pyver" ]; then
        probed+=("[$c]  (跑 -c 'import sys' 失败 / 不存在)")
        continue
    fi
    probed+=("[$c]  Python $pyver")
    if [ "$pyver" = "3.13-bad" ]; then
        [ -z "${PYTHON_BIN_FALLBACK:-}" ] && PYTHON_BIN_FALLBACK="$c"
        continue
    fi
    PYTHON_BIN="$c"
    PYTHON_VERSION="$pyver"
    break
done
set -e

if [ -z "$PYTHON_BIN" ] && [ -n "${PYTHON_BIN_FALLBACK:-}" ]; then
    warn "只找到 Python 3.13 ($PYTHON_BIN_FALLBACK);chayuan-server 在 3.13 上"
    warn "  多进程子 worker 会 SIGSEGV(C 扩展不兼容)。装 3.12:"
    echo "    conda create -n py312 python=3.12 -y" >&2
    echo "    然后:CHAYUAN_PYTHON=\$HOME/miniconda3/envs/py312/bin/python3 \\" >&2
    echo "          bash scripts/dev-start.sh" >&2
    PYTHON_BIN="$PYTHON_BIN_FALLBACK"
    PYTHON_VERSION="3.13(危险)"
fi
if [ -z "$PYTHON_BIN" ]; then
    err "找不到 Python 3.10/3.11/3.12;chayuan-server 不支持 3.13(C 扩展 SIGSEGV)"
    if [ "${#probed[@]}" -gt 0 ]; then
        echo "    探测过(都不是 3.10/3.11/3.12):" >&2
        for p in "${probed[@]:0:20}"; do echo "      $p" >&2; done
    else
        echo "    一个 Python 候选都没找到(conda + PATH 都空)" >&2
    fi
    echo "" >&2
    echo "  解法:" >&2
    echo "    1) 已装 conda 但 env 名字不是 py312 → 显式指定:" >&2
    echo "       export CHAYUAN_PYTHON=\$(conda info --json | python3 -c \\" >&2
    echo "         \"import json,sys;[print(e+'/bin/python3') for e in json.load(sys.stdin)['envs']]\" | head -1)" >&2
    echo "    2) macOS:  brew install python@3.12;export CHAYUAN_PYTHON=\$(brew --prefix python@3.12)/bin/python3.12" >&2
    echo "    3) Linux:  conda create -n py312 python=3.12 -y" >&2
    echo "              或 apt install -y python3.12 python3.12-venv" >&2
    exit 1
fi

# 验证 import chayuan(pip install -e 或 PYTHONPATH 都可)
export PYTHONPATH="$SERVER/libs/chayuan-server${PYTHONPATH:+:$PYTHONPATH}"
if "$PYTHON_BIN" -c "import chayuan" 2>/dev/null; then
    ok "Python: $PYTHON_BIN (Python $PYTHON_VERSION,可 import chayuan)"
else
    warn "$PYTHON_BIN 但 import chayuan 失败,要先装运行依赖"
    echo "    cd chayuan-server && $PYTHON_BIN -m pip install -e libs/chayuan-server" >&2
    [ $CHECK_ONLY -eq 0 ] && exit 2
fi

# ────────────────────────── 3. vendor binary ────────────────────
step "Step 2/4: 检查 vendor 二进制"
LLAMA_DIR="$SERVER/vendor/services/llama-server/$PLAT"
WHISPER_DIR="$SERVER/vendor/services/whisper-server/$PLAT"

if [ -x "$LLAMA_DIR/llama-server" ]; then
    ok "llama-server:$LLAMA_DIR/llama-server"
else
    warn "llama-server 不在 $LLAMA_DIR/"
    echo "    解法:bash scripts/install-llama-server.sh --target $PLAT" >&2
    [ $CHECK_ONLY -eq 0 ] && [ "$PLAT" != "linux-arm64" ] && exit 2
fi

if [ -x "$WHISPER_DIR/whisper-server" ]; then
    ok "whisper-server:$WHISPER_DIR/whisper-server"
else
    warn "whisper-server 不在 $WHISPER_DIR/(ASR 会 fallback Python faster-whisper)"
    echo "    解法:bash scripts/install-whisper-server.sh --target $PLAT" >&2
fi

# ────────────────────────── 4. CHAYUAN_ROOT ─────────────────────
step "Step 3/4: CHAYUAN_ROOT 数据目录"
CHAYUAN_ROOT="${CHAYUAN_ROOT:-$HOME/.chayuan-dev}"
mkdir -p "$CHAYUAN_ROOT"
if [ ! -w "$CHAYUAN_ROOT" ]; then
    err "CHAYUAN_ROOT=$CHAYUAN_ROOT 不可写"
    exit 1
fi
export CHAYUAN_ROOT
export CHAYUAN_VENDOR_PLATFORM="$PLAT"
ok "CHAYUAN_ROOT=$CHAYUAN_ROOT(env CHAYUAN_VENDOR_PLATFORM=$PLAT)"

# 首次需 chayuan init -q,创建基础 yaml
if [ ! -f "$CHAYUAN_ROOT/basic_settings.yaml" ]; then
    step "首次启动:跑 chayuan init -q 初始化数据目录"
    if command -v poetry >/dev/null 2>&1; then
        (cd "$SERVER" && poetry run python -m chayuan init -q --profile local)
    else
        (cd "$SERVER" && "$PYTHON_BIN" -m chayuan init -q --profile local)
    fi
    ok "基础 yaml 已生成 → $CHAYUAN_ROOT/"
fi

if [ $CHECK_ONLY -eq 1 ]; then
    ok "preflight 全部通过(--check-only)"
    exit 0
fi

# 从 chayuan_root/basic_settings.yaml 读真实 API 端口(优先 --port 显式)
# basic_settings.yaml 的结构:API_SERVER.port / 或 VENDOR_PREFERRED_PORTS.api
if [ "$PORT" = "62581" ] && [ -f "$CHAYUAN_ROOT/basic_settings.yaml" ]; then
    YAML_PORT="$("$PYTHON_BIN" -c "
import yaml
try:
    with open('$CHAYUAN_ROOT/basic_settings.yaml') as f: d = yaml.safe_load(f) or {}
    p = ((d.get('API_SERVER') or {}).get('public_port')
         or (d.get('API_SERVER') or {}).get('port')
         or (d.get('VENDOR_PREFERRED_PORTS') or {}).get('api'))
    if isinstance(p, int): print(p)
except Exception: pass
" 2>/dev/null)"
    if [ -n "$YAML_PORT" ] && [ "$YAML_PORT" != "62581" ]; then
        PORT="$YAML_PORT"
        ok "从 basic_settings.yaml 读到 API 端口:$PORT"
    fi
fi

# ────────────────────────── 5. 启 chayuan-server ────────────────
step "Step 4/4: 启 chayuan-server (port=$PORT)"
LOG_FILE="${TMPDIR:-/tmp}/chayuan-dev.log"
PID_FILE="${TMPDIR:-/tmp}/chayuan-dev.pid"

# 清理已有进程
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    warn "已有 dev sidecar pid=$(cat "$PID_FILE"),先杀掉"
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    sleep 1
fi

if command -v poetry >/dev/null 2>&1 && (cd "$SERVER" && poetry env info --path >/dev/null 2>&1); then
    CMD=(poetry run python -m chayuan start -a --single-machine)
else
    CMD=("$PYTHON_BIN" -m chayuan start -a --single-machine)
fi

cd "$SERVER"
if [ $BG -eq 1 ]; then
    nohup "${CMD[@]}" > "$LOG_FILE" 2>&1 &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$PID_FILE"
    ok "spawned pid=$SERVER_PID,日志:$LOG_FILE"

    # health probe:60s 内每 2s curl 一次 /healthz,任一成功就退;期间检测进程还活着
    step "等 health(最多 60s):curl http://127.0.0.1:$PORT/healthz"
    deadline=$((SECONDS + 60))
    healthy=0
    while [ $SECONDS -lt $deadline ]; do
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            err "chayuan-server 已退出(pid=$SERVER_PID),启动失败"
            echo "    最后 20 行日志:" >&2
            tail -20 "$LOG_FILE" | sed 's/^/      /' >&2
            rm -f "$PID_FILE"
            exit 3
        fi
        if curl -sf --max-time 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
            healthy=1; break
        fi
        sleep 2
    done

    if [ $healthy -eq 1 ]; then
        ok "ready ✓ (pid=$SERVER_PID listening on :$PORT)"
        echo "    tail -f $LOG_FILE         # 看日志"
        echo "    curl http://127.0.0.1:$PORT/healthz   # 再探活"
        echo "    kill \$(cat $PID_FILE)     # 关掉"
    else
        err "60s 内 /healthz 没返 200,server 可能卡在启动中"
        echo "    pid=$SERVER_PID 还活着,看日志:tail -f $LOG_FILE" >&2
        echo "    要强制关:kill $SERVER_PID" >&2
        exit 4
    fi
else
    ok "前台启动(Ctrl+C 退出)"
    exec "${CMD[@]}"
fi
