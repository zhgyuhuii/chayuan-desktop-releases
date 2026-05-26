#!/usr/bin/env bash
# 本地 LLM runtime 诊断 (Mac/Linux)。装机后跑 / 用户报 bug 贴日志。
set -uo pipefail

SIDECAR_BASE="${SIDECAR_BASE:-http://127.0.0.1:62581}"

ts=$(date +'%Y-%m-%d_%H%M%S')
log_file="/tmp/chayuan-diagnose-${ts}.md"

out=""
W() {
    echo "$1"
    out="${out}${1}"$'\n'
}

W "# Chayuan 本地 Runtime 诊断报告"
W ""
W "- 时间: $(date +'%Y-%m-%d %H:%M:%S')"
W "- 系统: $(uname -srm)"
W "- sidecar base: $SIDECAR_BASE"
W ""

# 1) 探 sidecar 进程
sidecar_pid=$(pgrep -f 'chayuan-server' | head -1 || true)
if [ -z "$sidecar_pid" ]; then
    W "## ✗ sidecar 进程未发现"
    W ""
    W "pgrep -f chayuan-server 没找到进程,说明 chayuan-server 没在跑。"
    W "请先启动 Chayuan 桌面应用,或检查日志:"
    W "  Linux: ~/.local/share/chayuan/logs/sidecar.log"
    W "  Mac:   ~/Library/Logs/chayuan/sidecar.log"
    W ""
    printf '%s' "$out" > "$log_file"
    echo
    echo "日志写到: $log_file"
    exit 2
fi

W "## ✓ sidecar 进程在跑"
W ""
W "- pid: $sidecar_pid"
W ""

# 2) curl /runtime/diagnose
if ! command -v curl >/dev/null 2>&1; then
    W "## ✗ 系统没装 curl,无法继续"
    printf '%s' "$out" > "$log_file"
    echo "日志写到: $log_file"
    exit 2
fi

resp=$(curl -fsS --max-time 15 "$SIDECAR_BASE/runtime/diagnose" 2>&1) || {
    W "## ✗ /runtime/diagnose 调用失败"
    W ""
    W '```'
    W "$resp"
    W '```'
    printf '%s' "$out" > "$log_file"
    echo "日志写到: $log_file"
    exit 2
}

# 解 JSON (依赖 python3 — 比 jq 装机率高)
if command -v python3 >/dev/null 2>&1; then
    parsed=$(python3 -c "
import json, sys
r = json.loads(sys.stdin.read())['data']
print('summary', r['summary']['ok'], r['summary']['warn'], r['summary']['fail'])
print('meta', r.get('chayuan_server_version', '?'), r.get('python_version', '?'), r.get('platform', '?'))
print('root', r.get('chayuan_root', '?'))
for c in r['checks']:
    icon = {'ok': 'OK', 'warn': 'WARN', 'fail': 'FAIL'}[c['severity']]
    detail = c['detail'].replace('|', '\\\\|').replace('\\n', ' ')
    print('row', c['name'], icon, detail)
" <<< "$resp")
else
    W "## ✗ python3 不在 PATH,无法解析 JSON"
    W ""
    W '原始响应:'
    W '```json'
    W "$resp"
    W '```'
    printf '%s' "$out" > "$log_file"
    echo "日志写到: $log_file"
    exit 2
fi

summary_line=$(echo "$parsed" | grep '^summary ')
ok=$(echo "$summary_line" | awk '{print $2}')
warn=$(echo "$summary_line" | awk '{print $3}')
fail=$(echo "$summary_line" | awk '{print $4}')

meta_line=$(echo "$parsed" | grep '^meta ')
sv=$(echo "$meta_line" | awk '{print $2}')
pyv=$(echo "$meta_line" | awk '{print $3}')
plat=$(echo "$meta_line" | awk '{print $4}')

root_line=$(echo "$parsed" | grep '^root ')
root=$(echo "$root_line" | cut -d' ' -f2-)

W "## 结果: $ok ✓ / $warn ⚠ / $fail ✗"
W ""
W "- chayuan-server: $sv (Python $pyv, $plat)"
W "- chayuan_root: $root"
W ""
W "| 检查项 | 状态 | 说明 |"
W "|---|---|---|"
echo "$parsed" | while IFS= read -r line; do
    case "$line" in
        row\ *)
            name=$(echo "$line" | awk '{print $2}')
            icon_raw=$(echo "$line" | awk '{print $3}')
            detail=$(echo "$line" | cut -d' ' -f4-)
            case "$icon_raw" in
                OK)   icon='✓' ;;
                WARN) icon='⚠' ;;
                FAIL) icon='✗' ;;
                *)    icon='?' ;;
            esac
            row="| $name | $icon | $detail |"
            echo "$row"
            out="${out}${row}"$'\n'
            ;;
    esac
done | tee /dev/null

W ""

printf '%s' "$out" > "$log_file"

echo
echo "日志已写到: $log_file"

if [ "$fail" -gt 0 ]; then exit 1; else exit 0; fi
