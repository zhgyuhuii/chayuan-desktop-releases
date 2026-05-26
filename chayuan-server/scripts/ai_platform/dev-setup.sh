#!/usr/bin/env bash
# Bootstrap a developer environment using `uv`. Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null; then
    echo "uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

uv venv -p 3.11 .venv
source .venv/bin/activate
uv pip install -e ./packages/chayuan-core
uv pip install -e ./packages/chayuan-identify
uv pip install -e ./packages/chayuan-registry
uv pip install -e ./packages/chayuan-modelmgr
uv pip install -e ./packages/chayuan-discovery
uv pip install -e ./packages/chayuan-runtime
uv pip install -e ./packages/chayuan-supervisor
uv pip install -e ./packages/chayuan-gateway
uv pip install -e ./packages/chayuan-preflight
uv pip install -e ./packages/chayuan-packager
uv pip install -e ./packages/chayuan-cli
uv pip install pytest httpx 'fastapi[testclient]' || true

echo
echo "Done. Activate with:  source .venv/bin/activate"
echo "Then try:             chayuan info && chayuan doctor"
