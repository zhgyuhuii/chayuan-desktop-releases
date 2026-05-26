#!/usr/bin/env bash
# ============================================================================
# 察元一体化开发环境一键安装（替代旧的 chayuan-ai-platform/scripts/dev-setup.sh）。
#
# 用法（在 chayuan-server 仓库根，需要 Python 3.10–3.12）：
#     bash scripts/dev_setup_all.sh
#
# 它做四件事：
#   1) 创建 .venv（用 venv，不强制 uv/poetry）；
#   2) 装 chayuan-server 的 poetry 主依赖（--with lint,test）；
#   3) 装 11 个 ai-platform sibling 包（editable）；
#   4) 装 packaging/python312 跨平台打包器（editable）。
#
# 完成后：
#   * `chayuan --help`              主 CLI（含 ai-platform 子命令）
#   * `chayuan ai-platform --help`   AI 平台子命令
#   * `chayuan-pack --help`           打包器
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PY="${PY:-python3}"
"${PY}" --version

# 1) venv
if [[ ! -d ".venv" ]]; then
    echo "[dev-setup] 创建 .venv ..."
    "${PY}" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip

# 2) chayuan-server (Poetry → 直接 pip 走 pyproject.toml 的 dependencies)
echo "[dev-setup] 安装 chayuan-server（核心依赖）..."
if command -v poetry >/dev/null 2>&1; then
    poetry -C libs/chayuan-server install --with lint,test || true
else
    # Poetry 未装：fallback 用 pip 装一个最小可启动的子集（详见 CONTRIBUTING.md）
    pip install -e libs/chayuan-server || true
fi

# 3) 11 个 ai-platform sibling 包
echo "[dev-setup] 安装 11 个 ai-platform sibling 包..."
bash scripts/install_ai_platform.sh --pip pip

# 4) packaging/python312
echo "[dev-setup] 安装 chayuan-pack 跨平台打包器..."
pip install -e packaging/python312

echo
echo "[dev-setup] ✓ 完成。下一步："
echo "  source .venv/bin/activate"
echo "  chayuan init"
echo "  chayuan ai-platform doctor"
echo "  chayuan-pack audit --release lite"
