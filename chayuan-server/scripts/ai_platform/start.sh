#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"

if [ -f config/default.env ]; then
    set -a
    # shellcheck disable=SC1091
    . config/default.env
    set +a
fi

PY="${CHAYUAN_PYTHON:-python3}"
exec "$PY" -m chayuan_supervisor up --foreground "$@"
