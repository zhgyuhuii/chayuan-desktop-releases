#!/usr/bin/env bash
# E2E smoke test for the frozen chayuan-desktop KB pipeline.
#
# Why this exists
# ---------------
# Frozen / installed builds have repeatedly broken KB retrieval in ways that
# dev mode (poetry run chayuan start) never sees:
#   - PyInstaller namespace-package issue with tiktoken_ext (cl100k_base unknown)
#   - PyInstaller data-file collection missing unstructured/nlp/english-words.txt
#   - mp child runtime hook ordering (tiktoken patch skipped for API server)
#   - MODEL_PLATFORMS not picking up local-runtime sidecar registration
#   - atomic_rebuild shadow path FileNotFound during recreate_vector_store
#
# Each of these ships silently: server starts, sidecar listens, list_files
# returns docs_count>0, but search_docs returns 0 vector hits and chat acts
# as if KB isn't attached.
#
# This script catches all of them in <2 minutes. Run it before every release
# (manually for now; wire into CI later).
#
# Flow
# ----
#   1. Auto-detect installed chayuan-server binary (mac .app / Linux / Win)
#   2. Spawn it as a subprocess pointing at a fresh isolated CHAYUAN_ROOT
#   3. Wait for /runtime/diagnose and embedding sidecar (62583) to be up
#   4. POST /knowledge_base/create_knowledge_base
#   5. Upload a .txt file with a unique secret token + "deepseek apikey" content
#   6. Verify SQLite docs_count > 0 (chunking ok)
#   7. Verify on-disk vector_store/<embed>/index.faiss > 100 bytes (embed wrote)
#   8. POST /knowledge_base/search_docs with the unique secret
#   9. Assert: at least 1 hit AND retrieval_path includes "vector" or "hybrid"
#  10. Cleanup: kill server, optionally keep the test root
#
# Exit codes
# ----------
#   0 = PASS
#   1 = FAIL (with server log tail printed)
#   2 = couldn't even start (no installed binary / port collision)
#
# Usage
# -----
#   bash scripts/e2e-frozen-kb-smoke.sh
#   bash scripts/e2e-frozen-kb-smoke.sh --exe /path/to/chayuan-server
#   bash scripts/e2e-frozen-kb-smoke.sh --root /tmp/my-test-root --keep-server
#
# Note: NOT `set -e` -- we want every step to run and report individually.
set -uo pipefail

KEEP_SERVER=0
EXPLICIT_EXE=""
TEST_ROOT="${TMPDIR:-/tmp}/chayuan-e2e-smoke-$(date +%s)"
PORT=62581
EMBED_PORT=62583

while [ $# -gt 0 ]; do
    case "$1" in
        --keep-server) KEEP_SERVER=1; shift ;;
        --exe)         EXPLICIT_EXE="$2"; shift 2 ;;
        --root)        TEST_ROOT="$2"; shift 2 ;;
        --port)        PORT="$2"; shift 2 ;;
        -h|--help)     sed -n '2,55p' "$0"; exit 0 ;;
        *)             echo "[smoke] unknown arg: $1" >&2; exit 64 ;;
    esac
done

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yel()   { printf '\033[33m%s\033[0m\n' "$*"; }

# ─── 1. Detect installed chayuan-server binary ─────────────────────────────
detect_exe() {
    if [ -n "$EXPLICIT_EXE" ]; then
        [ -f "$EXPLICIT_EXE" ] && echo "$EXPLICIT_EXE" || return 1
        return 0
    fi
    case "$(uname -s)" in
        Darwin)
            for p in /Applications/Chayuan.app \
                     /Applications/Chayuan.integrated.app \
                     /Applications/Chayuan.full.app \
                     /Applications/Chayuan.lite.app; do
                [ -f "$p/Contents/Resources/chayuan-server/chayuan-server" ] && {
                    echo "$p/Contents/Resources/chayuan-server/chayuan-server"
                    return 0
                }
            done ;;
        Linux)
            for p in /opt/Chayuan /opt/chayuan /usr/lib/chayuan-desktop; do
                [ -f "$p/chayuan-server/chayuan-server" ] && {
                    echo "$p/chayuan-server/chayuan-server"
                    return 0
                }
            done ;;
        MINGW*|MSYS*|CYGWIN*)
            for p in "/c/Program Files/Chayuan" \
                     "${LOCALAPPDATA:-}/Programs/Chayuan"; do
                [ -f "$p/chayuan-server/chayuan-server.exe" ] && {
                    echo "$p/chayuan-server/chayuan-server.exe"
                    return 0
                }
            done ;;
    esac
    return 1
}

EXE=$(detect_exe)
if [ -z "$EXE" ]; then
    red "[FAIL] No installed chayuan-server binary found. Install the desktop"
    red "        app first, or pass --exe <path>."
    exit 2
fi

mkdir -p "$TEST_ROOT"
LOG_FILE="$TEST_ROOT/smoke.log"
KB="e2e_smoke_$$_$(date +%s)"
SECRET="DEEPSEEK_E2E_SK_$(printf '%s' "$KB" | shasum 2>/dev/null | head -c 16 || echo deadbeef0badc0de)"
TEST_FILE="$TEST_ROOT/api_key.txt"

cat > "$TEST_FILE" <<EOF
Chayuan E2E frozen-KB smoke test fixture.

The DeepSeek apikey value used by the verification suite is:
sk-${SECRET}

That secret above should be retrievable by querying the KB after upload.
If you can't find it via /knowledge_base/search_docs with retrieval_path
including "vector" or "hybrid", the frozen build's KB pipeline is broken.

For reproducibility this token is stable per smoke-test invocation.
EOF

echo "════════════════════════════════════════════════"
echo "[smoke] exe        : $EXE"
echo "[smoke] CHAYUAN_ROOT: $TEST_ROOT (isolated)"
echo "[smoke] test KB    : $KB"
echo "[smoke] secret tok : sk-$SECRET"
echo "[smoke] log file   : $LOG_FILE"
echo "════════════════════════════════════════════════"

# ─── 2. Spawn chayuan-server with isolated root ────────────────────────────
export CHAYUAN_ROOT="$TEST_ROOT"
"$EXE" start -a --single-machine > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "[smoke] spawned chayuan-server PID=$SERVER_PID"

cleanup() {
    rc=$?
    if [ "$KEEP_SERVER" = "1" ]; then
        yel "[smoke] --keep-server set, leaving PID=$SERVER_PID running"
        yel "        cleanup yourself: kill $SERVER_PID; rm -rf '$TEST_ROOT'"
        return
    fi
    if kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "[smoke] killing server PID=$SERVER_PID"
        kill "$SERVER_PID" 2>/dev/null || true
        sleep 2
        kill -9 "$SERVER_PID" 2>/dev/null || true
    fi
    # Always print log tail on failure so users have evidence
    if [ "$rc" -ne 0 ] && [ -f "$LOG_FILE" ]; then
        echo ""
        red "─── server.log tail 50 (for diagnosis) ───────────"
        tail -50 "$LOG_FILE"
    fi
}
trap cleanup EXIT

FAIL=0

# ─── 3. Wait for chayuan-server API ────────────────────────────────────────
echo "[smoke] waiting for http://127.0.0.1:$PORT/runtime/diagnose ..."
api_up=0
for i in $(seq 1 90); do
    if curl -sf --max-time 2 "http://127.0.0.1:$PORT/runtime/diagnose" >/dev/null 2>&1; then
        api_up=1
        echo "[smoke] API up after ${i}s"
        break
    fi
    sleep 1
done
[ "$api_up" = "1" ] || { red "[FAIL] API never came up"; exit 1; }

# ─── 4. Wait for embedding sidecar ─────────────────────────────────────────
echo "[smoke] waiting for embedding sidecar @ 127.0.0.1:$EMBED_PORT ..."
embed_up=0
for i in $(seq 1 120); do
    if curl -sf --max-time 2 "http://127.0.0.1:$EMBED_PORT/v1/models" >/dev/null 2>&1; then
        embed_up=1
        echo "[smoke] embedding sidecar up after ${i}s"
        break
    fi
    sleep 1
done
if [ "$embed_up" != "1" ]; then
    red "[FAIL] embedding sidecar @ $EMBED_PORT never became reachable"
    FAIL=1
fi

# ─── 5. Create test KB ─────────────────────────────────────────────────────
echo "[smoke] POST /knowledge_base/create_knowledge_base name=$KB"
CREATE=$(curl -s --max-time 30 -X POST \
    -H "Content-Type: application/json" \
    -d "{\"knowledge_base_name\":\"$KB\",\"vector_store_type\":\"faiss\",\"embed_model\":\"bge-m3\"}" \
    "http://127.0.0.1:$PORT/knowledge_base/create_knowledge_base" 2>&1)
echo "  → $CREATE" | head -3
if ! echo "$CREATE" | grep -qE '"code":\s*200'; then
    red "[FAIL] KB creation"
    FAIL=1
fi

# ─── 6. Upload the test file ───────────────────────────────────────────────
echo "[smoke] POST /knowledge_base/upload_docs file=$TEST_FILE"
UPLOAD=$(curl -s --max-time 180 -X POST \
    -F "knowledge_base_name=$KB" \
    -F "files=@$TEST_FILE" \
    -F "override=true" \
    -F "to_vector_store=true" \
    "http://127.0.0.1:$PORT/knowledge_base/upload_docs" 2>&1)
echo "  → $UPLOAD" | head -3

# Give the indexer a beat to finish async write
sleep 5

# ─── 7. SQLite-side check: docs_count > 0 ──────────────────────────────────
echo "[smoke] checking list_files docs_count ..."
LIST=$(curl -s --max-time 15 "http://127.0.0.1:$PORT/knowledge_base/list_files?knowledge_base_name=$KB")
DOCS=$(printf '%s' "$LIST" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); fs=d.get('data',[]); print(sum(int(f.get('docs_count') or 0) for f in fs))" 2>/dev/null || echo 0)
echo "  → total docs_count = $DOCS"
if [ "$DOCS" = "0" ]; then
    red "[FAIL] docs_count == 0 (chunking failed; check unstructured / text_splitter / tiktoken)"
    FAIL=1
fi

# ─── 8. On-disk check: vector_store has a non-trivial index.faiss ──────────
KB_DIR="$TEST_ROOT/data/knowledge_base/$KB"
FAISS_FILE=$(find "$KB_DIR" -type f -name "*.faiss" 2>/dev/null | head -1 || true)
if [ -n "$FAISS_FILE" ] && [ -f "$FAISS_FILE" ]; then
    SZ=$(stat -f '%z' "$FAISS_FILE" 2>/dev/null || stat -c '%s' "$FAISS_FILE" 2>/dev/null)
    echo "[smoke] index.faiss = $FAISS_FILE  ($SZ bytes)"
    # Empty/init-only FAISS index for bge-m3 is ~8 KB. Real data adds bytes.
    # A KB with one short .txt should be >9000 B (8K header + 1 vector × 1024 dims × 4 B = 4096 B).
    if [ "${SZ:-0}" -lt 9000 ]; then
        red "[FAIL] index.faiss too small ($SZ B) -- vector_store has no real vectors"
        FAIL=1
    fi
else
    red "[FAIL] no index.faiss anywhere under $KB_DIR"
    FAIL=1
fi

# ─── 9. /knowledge_base/search_docs --  THE actual retrieval check ─────────
echo "[smoke] POST /knowledge_base/search_docs query='sk-$SECRET'"
SEARCH=$(curl -s --max-time 30 -X POST \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"sk-$SECRET\",\"knowledge_base_name\":\"$KB\",\"top_k\":5,\"score_threshold\":2.0}" \
    "http://127.0.0.1:$PORT/knowledge_base/search_docs")
printf '%s\n' "$SEARCH" | python3 -m json.tool 2>/dev/null | head -30 || printf '%s\n' "$SEARCH" | head -10

HITS=$(printf '%s' "$SEARCH" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); v=d.get('value', d.get('data', [])); print(len(v) if isinstance(v, list) else 0)" 2>/dev/null || echo 0)
PATHS=$(printf '%s' "$SEARCH" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); v=d.get('value', d.get('data', [])); paths=sorted({(h.get('metadata') or {}).get('retrieval_path') or '?' for h in v}); print(','.join(paths))" 2>/dev/null || echo "?")
echo "[smoke] hits=$HITS  retrieval_paths=[$PATHS]"

if [ "${HITS:-0}" = "0" ]; then
    red "[FAIL] search_docs returned 0 hits for the planted secret"
    FAIL=1
elif printf '%s' "$PATHS" | grep -qE "vector|hybrid"; then
    green "[PASS] vector / hybrid retrieval engaged"
else
    yel  "[FAIL-soft] only keyword retrieval engaged (vector_store likely empty)"
    yel  "             paths=$PATHS"
    FAIL=1
fi

# Bonus: semantic query that should ONLY match via vector
echo ""
echo "[smoke] semantic query: '查询 DeepSeek API key'"
SEM_SEARCH=$(curl -s --max-time 30 -X POST \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"查询 DeepSeek API key\",\"knowledge_base_name\":\"$KB\",\"top_k\":5,\"score_threshold\":2.0}" \
    "http://127.0.0.1:$PORT/knowledge_base/search_docs")
SEM_HITS=$(printf '%s' "$SEM_SEARCH" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); v=d.get('value', d.get('data', [])); print(len(v) if isinstance(v, list) else 0)" 2>/dev/null || echo 0)
SEM_PATHS=$(printf '%s' "$SEM_SEARCH" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); v=d.get('value', d.get('data', [])); paths=sorted({(h.get('metadata') or {}).get('retrieval_path') or '?' for h in v}); print(','.join(paths))" 2>/dev/null || echo "?")
echo "[smoke] semantic hits=$SEM_HITS  retrieval_paths=[$SEM_PATHS]"

# ─── 10. Summary ───────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
if [ "$FAIL" = "0" ]; then
    green "[OK] E2E frozen KB smoke PASSED"
    green "     KB dir: $KB_DIR"
    green "     server log: $LOG_FILE"
    exit 0
else
    red   "[FAIL] E2E frozen KB smoke FAILED"
    red   "       Server log tail will be printed below."
    red   "       Test root preserved: $TEST_ROOT"
    KEEP_SERVER=0
    exit 1
fi
