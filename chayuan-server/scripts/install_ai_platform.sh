#!/usr/bin/env bash
# ============================================================================
# 把 chayuan-server/libs/ 下的 11 个 ai-platform sibling 包以 editable 模式
# 装到当前 Python 环境（Poetry 主 venv 或 conda 都可以）。
#
# 用法：
#   bash scripts/install_ai_platform.sh           # 自动检测 venv
#   bash scripts/install_ai_platform.sh --pip pip3.12
#
# 设计要点：
# * 11 个包 PEP 621（hatchling）打包，不走 Poetry，因此独立 pip install -e；
# * 安装顺序按 chayuan-cli 的依赖图：core → identify → registry → ...；
# * 装完只算"有可用"，不会自动启动；需要再跑 chayuan ai-platform service start。
# ============================================================================

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
LIBS="${ROOT}/libs"

PIP="${PIP:-pip}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --pip) PIP="$2"; shift 2;;
        -h|--help)
            echo "Usage: $0 [--pip /path/to/pip]"; exit 0;;
        *) echo "[install_ai_platform] unknown arg: $1" >&2; exit 1;;
    esac
done

if ! command -v "${PIP}" >/dev/null 2>&1; then
    echo "[install_ai_platform] pip 未找到（${PIP}）；请先激活 venv/conda 环境" >&2
    exit 2
fi

# 依赖顺序（核心层 → 业务层 → CLI）
declare -a PKGS=(
    chayuan-core
    chayuan-identify
    chayuan-registry
    chayuan-discovery
    chayuan-modelmgr
    chayuan-runtime
    chayuan-supervisor
    chayuan-gateway
    chayuan-preflight
    chayuan-packager
    chayuan-cli
)

echo "[install_ai_platform] python: $(command -v ${PIP%pip}python3 2>/dev/null || command -v python3)"
echo "[install_ai_platform] pip   : $(${PIP} --version)"
echo

for pkg in "${PKGS[@]}"; do
    src="${LIBS}/${pkg}"
    if [[ ! -d "${src}" ]]; then
        echo "  ! ${pkg}: 目录不存在 ${src}，跳过"
        continue
    fi
    echo "→ pip install -e ${pkg}"
    "${PIP}" install -e "${src}" --no-deps  # 依赖在最后一次 pip install 时一并解析
done

echo
echo "→ 解析依赖(只装缺失的,不动已有 torch / numpy 等):"
# 关键: --upgrade-strategy only-if-needed 避免 pip 重装已经满足约束的大包,
# 否则 Windows / WSL 上 torch _C.pyd 等 DLL 被锁会触发 [WinError 5] 拒绝访问。
if ! "${PIP}" install -e "${LIBS}/chayuan-cli" --upgrade-strategy only-if-needed; then
    echo
    echo "[install_ai_platform] ✗ chayuan-cli 依赖解析失败" >&2
    echo
    echo "如果错误是 _C.pyd / .so 被占用 / 拒绝访问:" >&2
    echo "  1. 关闭所有 Python 进程(IDE / Jupyter / chayuan server)" >&2
    echo "  2. Windows: 临时关闭 Windows Defender 实时保护;管理员模式重试" >&2
    echo "  3. macOS:   xattr -cr <env> 清掉隔离属性后重试" >&2
    echo "  4. 仍失败: pip uninstall -y torch torchvision torchaudio 后重新执行" >&2
    exit 3
fi

echo
echo "[install_ai_platform] ✓ 11 个包已安装。验证："
echo "    chayuan ai-platform --help"
echo "    chayuan ai-platform service info"
echo "    chayuan ai-platform model ls"
