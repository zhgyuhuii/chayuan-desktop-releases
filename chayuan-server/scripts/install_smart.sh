#!/usr/bin/env bash
# =============================================================================
# 智能安装(包装 install_ai_platform.sh)— 失败时自动诊断 + 一键修复。
#
# 用法:
#   bash scripts/install_smart.sh               # 失败后只打印诊断
#   bash scripts/install_smart.sh --auto-fix    # 失败后逐条交互式执行修复
#
# 工作流:
#   1) 调用 install_ai_platform.sh,output 同时写到 stdout 和临时日志
#   2) 退出码 == 0 → 完事
#   3) 退出码 != 0 → 把日志喂给 install_diagnose.py 分析
#   4) 诊断器输出已知失败模式 + 修复命令(可选 --auto-fix 自动跑)
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"

PIP="${PIP:-pip}"
AUTO_FIX=0
LOG_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pip) PIP="$2"; shift 2;;
        --auto-fix) AUTO_FIX=1; shift;;
        --log-file) LOG_FILE="$2"; shift 2;;
        -h|--help)
            sed -n '2,15p' "$0"; exit 0;;
        *) echo "[install_smart] 未知参数: $1" >&2; exit 1;;
    esac
done

if [[ -z "${LOG_FILE}" ]]; then
    LOG_FILE="$(mktemp -t chayuan_install_XXXXXX.log)"
fi

echo "========================================================="
echo " chayuan · 智能安装(install_smart.sh)"
echo "========================================================="
echo "  日志文件: ${LOG_FILE}"
echo "  AutoFix:  ${AUTO_FIX}"
echo ""

# 跑底层安装脚本,output tee 到日志
set +e
bash "${HERE}/install_ai_platform.sh" --pip "${PIP}" 2>&1 | tee "${LOG_FILE}"
RC=${PIPESTATUS[0]}
set -e

if [[ "${RC}" -eq 0 ]]; then
    echo
    echo "[install_smart] ✓ 安装成功"
    rm -f "${LOG_FILE}"
    exit 0
fi

echo
echo "[install_smart] ✗ 安装失败(退出码 ${RC}),自动诊断..."
echo

DIAG_PY="${HERE}/install_diagnose.py"
if [[ ! -f "${DIAG_PY}" ]]; then
    echo "[install_smart] 诊断器不存在: ${DIAG_PY}" >&2
    echo "[install_smart] 完整日志: ${LOG_FILE}" >&2
    exit "${RC}"
fi

DIAG_ARGS=("${DIAG_PY}" "${LOG_FILE}")
if [[ "${AUTO_FIX}" -eq 1 ]]; then
    DIAG_ARGS+=("--auto-fix")
fi

python3 "${DIAG_ARGS[@]}" || python "${DIAG_ARGS[@]}"
DIAG_RC=$?

echo
echo "完整日志保留在: ${LOG_FILE}"

if [[ "${AUTO_FIX}" -eq 1 && "${DIAG_RC}" -eq 0 ]]; then
    echo "[install_smart] 修复完成,请重新运行:"
    echo "    bash scripts/install_smart.sh"
fi

exit "${RC}"
