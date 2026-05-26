#!/usr/bin/env bash
# Diagnose why a KB in chayuan-desktop can't be searched on macOS / Linux.
#
# Specifically targets the "old KB still shows up but search misses" issue
# typical after the user changed chayuan_root to a new folder: metadata
# may be at the new root while vector_store / content/ is stranded at the
# old root, or vice versa.
#
# Output: ${TMPDIR:-/tmp}/chayuan-kb-diag-<ts>.md (UTF-8, paste-friendly).
#
# Usage:
#   bash scripts/diagnose-kb-vectorize.sh
#   bash scripts/diagnose-kb-vectorize.sh -k my_kb
#   bash scripts/diagnose-kb-vectorize.sh -k bb -q "apikey" -s http://127.0.0.1:62581
set -euo pipefail

KB="bb"
SIDECAR="http://127.0.0.1:62581"
QUERY="deepseek apikey"

while getopts ":k:s:q:h" opt; do
    case "$opt" in
        k) KB="$OPTARG" ;;
        s) SIDECAR="$OPTARG" ;;
        q) QUERY="$OPTARG" ;;
        h) sed -n '2,12p' "$0"; exit 0 ;;
        \?) echo "unknown opt -$OPTARG"; exit 64 ;;
    esac
done

TS=$(date +%Y%m%d-%H%M%S)
OUT="${TMPDIR:-/tmp}/chayuan-kb-diag-${TS}.md"

section() {
    local title="$1"
    {
        printf '\n## %s\n```\n' "$title"
        cat
        printf '\n```\n'
    } >> "$OUT"
}

# header
{
    echo "# KB vectorize diagnostic for KB='${KB}' at ${TS}"
    echo
    echo "- Sidecar : ${SIDECAR}"
    echo "- Query   : ${QUERY}"
    echo "- Host OS : $(uname -s) $(uname -r)"
    echo
} > "$OUT"

# Resolve runtime context via /runtime/diagnose
ROOT=""
ROOT_ERR=""
DIAG_JSON=$(curl -sf --max-time 10 "${SIDECAR}/runtime/diagnose" 2>&1) || ROOT_ERR="curl /runtime/diagnose failed"
if [ -z "$ROOT_ERR" ] && [ -n "$DIAG_JSON" ]; then
    ROOT=$(printf '%s' "$DIAG_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('chayuan_root',''))" 2>/dev/null || true)
fi

# Candidate KB roots (current + all common past locations)
# user said they "changed data folder", so we scan multiple plausibles
KB_DIR_CUR=""
[ -n "$ROOT" ] && KB_DIR_CUR="${ROOT}/data/knowledge_base/${KB}"

CANDIDATE_ROOTS=(
    "${ROOT}"
    "${HOME}/Library/Application Support/chayuan"
    "${HOME}/Library/Application Support/Chayuan"
    "${HOME}/.chayuan"
    "${HOME}/chayuan-data"
)

# Log path scan
LOG_PATH=""
LOG_CANDIDATES=()
if [ -n "$ROOT" ]; then
    LOG_CANDIDATES+=(
        "${ROOT}/logs/server.log"
        "${ROOT}/logs/chayuan.log"
        "${ROOT}/data/logs/server.log"
        "${ROOT}/data/logs/chayuan.log"
    )
fi
LOG_CANDIDATES+=(
    "${HOME}/Library/Application Support/chayuan/logs/server.log"
    "${HOME}/Library/Application Support/chayuan/logs/chayuan.log"
    "${HOME}/Library/Application Support/chayuan/data/logs/server.log"
    "${HOME}/Library/Logs/Chayuan/server.log"
)
for p in "${LOG_CANDIDATES[@]}"; do
    if [ -f "$p" ]; then LOG_PATH="$p"; break; fi
done

# Installed .app introspection
APP_PATHS=(
    "/Applications/Chayuan.app"
    "/Applications/Chayuan.integrated.app"
    "/Applications/Chayuan.lite.app"
    "/Applications/Chayuan.full.app"
)
APP_PATH=""
for p in "${APP_PATHS[@]}"; do
    if [ -d "$p" ]; then APP_PATH="$p"; break; fi
done

# ─── Section 0: environment ───
{
    echo "chayuan_root        : ${ROOT}"
    echo "chayuan_root status : $([ -n "$ROOT_ERR" ] && echo "FAIL: $ROOT_ERR" || echo "OK")"
    echo "KB dir (expected)   : ${KB_DIR_CUR}"
    echo "log path picked     : ${LOG_PATH}"
    echo "log scanned         :"
    for p in "${LOG_CANDIDATES[@]}"; do echo "  - $p"; done
    echo "installed app       : ${APP_PATH}"
    if [ -n "$APP_PATH" ]; then
        exe="$APP_PATH/Contents/Resources/chayuan-server/chayuan-server"
        if [ -f "$exe" ]; then
            echo "  exe               : $exe"
            echo "  exe mtime         : $(stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S' "$exe" 2>/dev/null || stat -c '%y' "$exe" 2>/dev/null)"
            echo "  exe size MB       : $(($(stat -f '%z' "$exe" 2>/dev/null || stat -c '%s' "$exe" 2>/dev/null) / 1024 / 1024))"
        fi
    fi
    echo "env CHAYUAN_ROOT    : ${CHAYUAN_ROOT:-(unset)}"
} | section "0. environment resolution"

# ─── Section 0.5: SCAN ALL CANDIDATE ROOTS FOR KB DATA ───
# Critical for "I changed chayuan_root and old KBs still show"
{
    echo "Scanning every plausible chayuan_root for a KB named '${KB}':"
    echo
    for r in "${CANDIDATE_ROOTS[@]}"; do
        [ -z "$r" ] && continue
        kb="${r}/data/knowledge_base/${KB}"
        info_db="${r}/data/knowledge_base/info.db"
        if [ -d "$kb" ] || [ -f "$info_db" ]; then
            echo "--- ROOT: $r ---"
            [ -f "$info_db" ] && {
                size=$(stat -f '%z' "$info_db" 2>/dev/null || stat -c '%s' "$info_db" 2>/dev/null)
                mtime=$(stat -f '%Sm' -t '%Y-%m-%d %H:%M:%S' "$info_db" 2>/dev/null || stat -c '%y' "$info_db" 2>/dev/null)
                echo "  info.db   : $info_db  size=$size  mtime=$mtime"
            }
            if [ -d "$kb" ]; then
                echo "  KB dir    : $kb"
                find "$kb" -type f 2>/dev/null | while read -r f; do
                    sz=$(stat -f '%z' "$f" 2>/dev/null || stat -c '%s' "$f" 2>/dev/null)
                    rel="${f#$kb/}"
                    echo "    $rel  ($sz B)"
                done
            else
                echo "  KB dir    : (missing under this root)"
            fi
        fi
    done
} | section "0.5 KB '${KB}' presence across all candidate roots (root-mismatch detector)"

# ─── Section 1: tiktoken hook trace (only meaningful for frozen builds) ───
{
    if [ -z "$LOG_PATH" ]; then
        echo "(no log path resolved)"
    else
        hits=$(grep -F "tiktoken-rthook" "$LOG_PATH" 2>/dev/null | tail -20 || true)
        if [ -z "$hits" ]; then
            echo "(no [tiktoken-rthook] lines in $LOG_PATH)"
            echo "Either: (a) you're in dev mode (hook only runs frozen),"
            echo "or:     (b) installed build is pre-77c80ac (rebuild needed)."
        else
            printf '%s\n' "$hits"
        fi
    fi
} | section "1. tiktoken hook trace"

# ─── Section 2: KB on-disk layout at CURRENT chayuan_root ───
{
    if [ -z "$KB_DIR_CUR" ]; then
        echo "(chayuan_root unresolved)"
    elif [ ! -d "$KB_DIR_CUR" ]; then
        echo "(KB dir does NOT exist at $KB_DIR_CUR)"
        echo
        echo "If you previously created KB '${KB}' but the current chayuan_root"
        echo "doesn't see it, your data is most likely at one of the other roots"
        echo "listed in section 0.5."
    else
        echo "KB dir: $KB_DIR_CUR"
        echo
        find "$KB_DIR_CUR" -type f 2>/dev/null | sort | while read -r f; do
            sz=$(stat -f '%z' "$f" 2>/dev/null || stat -c '%s' "$f" 2>/dev/null)
            rel="${f#$KB_DIR_CUR/}"
            kb=$(awk "BEGIN { printf \"%.1f\", $sz / 1024 }")
            echo "  $rel  ${kb} KB"
        done
        echo
        # Spotlight the .faiss files
        faiss=$(find "$KB_DIR_CUR" -type f -name "*.faiss" 2>/dev/null)
        if [ -z "$faiss" ]; then
            echo "NO .faiss file under KB dir -- vector_store empty / missing."
        else
            echo "FAISS index files:"
            printf '%s\n' "$faiss" | while read -r f; do
                sz=$(stat -f '%z' "$f" 2>/dev/null || stat -c '%s' "$f" 2>/dev/null)
                echo "  $f  $sz B"
            done
        fi
    fi
} | section "2. KB on-disk layout at CURRENT chayuan_root"

# ─── Section 3: list_files via API ───
{
    curl -sf --max-time 15 "${SIDECAR}/knowledge_base/list_files?knowledge_base_name=${KB}" 2>&1 |
        python3 -m json.tool 2>/dev/null ||
        echo "(list_files request failed)"
} | section "3. /knowledge_base/list_files"

# ─── Section 4: recreate_vector_store ───
{
    curl -sf --max-time 600 -X POST \
        -H "Content-Type: application/json" \
        -d "{\"knowledge_base_name\":\"${KB}\",\"allow_empty_kb\":false}" \
        "${SIDECAR}/knowledge_base/recreate_vector_store" 2>&1 |
        python3 -m json.tool 2>/dev/null ||
        echo "(recreate_vector_store request failed; raw output above)"
} | section "4. /knowledge_base/recreate_vector_store (real exception surfaces here)"

# ─── Section 5: search_docs ───
{
    curl -sf --max-time 30 -X POST \
        -H "Content-Type: application/json" \
        -d "{\"query\":\"${QUERY}\",\"knowledge_base_name\":\"${KB}\",\"top_k\":5,\"score_threshold\":2.0}" \
        "${SIDECAR}/knowledge_base/search_docs" 2>&1 |
        python3 -m json.tool 2>/dev/null ||
        echo "(search_docs request failed)"
} | section "5. /knowledge_base/search_docs (query='${QUERY}', check retrieval_path)"

# ─── Section 6: server.log tail 300 ───
{
    if [ -n "$LOG_PATH" ] && [ -f "$LOG_PATH" ]; then
        tail -300 "$LOG_PATH"
    else
        echo "(no log path)"
    fi
} | section "6. server.log tail 300"

# ─── Section 7: filtered log entries ───
{
    if [ -n "$LOG_PATH" ] && [ -f "$LOG_PATH" ]; then
        grep -E "tiktoken|cl100k|vector_store|embedding|embed_documents|upload_docs|recreate_vector|search_docs|kb_doc_api|splitter|atomic_rebuild|FileNotFound|Traceback|ERROR|Exception|unstructured|english-words" "$LOG_PATH" 2>/dev/null |
            tail -80
    else
        echo "(no log path)"
    fi
} | section "7. server.log filtered (KB / embedding / exception keywords, last 80)"

# ─── Section 8: embedding sidecar /v1/models ───
{
    curl -sf --max-time 5 "http://127.0.0.1:62583/v1/models" 2>&1 |
        python3 -m json.tool 2>/dev/null ||
        echo "(embedding sidecar @ 62583 unreachable)"
} | section "8. embedding sidecar /v1/models @ 62583"

# ─── Section 9: running process tree ───
{
    if command -v ps >/dev/null; then
        ps -axww -o pid,ppid,command 2>/dev/null |
            grep -E "(chayuan|llama-server|whisper-server|rapidocr|paddleocr)" |
            grep -v grep |
            head -40
    fi
} | section "9. running process tree (mac/linux ps)"

# ─── Section 10: model-platforms snapshot (the bb 'Connection error' suspect) ───
{
    echo "--- /v1/models (chayuan-server side, merged platforms) ---"
    curl -sf --max-time 10 "${SIDECAR}/v1/models" 2>&1 |
        python3 -m json.tool 2>/dev/null ||
        echo "(/v1/models request failed)"
    echo
    echo "--- /llm_model/list_running_models ---"
    curl -sf --max-time 10 "${SIDECAR}/llm_model/list_running_models" 2>&1 |
        python3 -m json.tool 2>/dev/null ||
        echo "(list_running_models request failed)"
} | section "10. model platforms exposed by chayuan-server (for 'embedding Connection error' RCA)"

echo
echo "OK -- KB vectorize diagnostic written to:"
echo "  $OUT"
echo
echo "View / paste:"
echo "  open '$OUT'         # macOS"
echo "  cat '$OUT'"
echo
echo "Paste the entire file contents back to the developer."
