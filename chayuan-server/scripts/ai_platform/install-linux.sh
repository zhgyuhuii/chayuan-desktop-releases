#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
TARGET="${CHAYUAN_HOME:-$HOME/.local/share/chayuan}"

echo "Installing Chayuan into $TARGET ..."
mkdir -p "$TARGET"
rsync -a --info=progress2 \
    --exclude '.git' --exclude 'dist' --exclude '__pycache__' \
    "$ROOT"/ "$TARGET"/

cd "$TARGET"
if command -v uv >/dev/null; then
    uv pip install --system -e . || true
else
    python3 -m pip install -e . || true
fi
echo
echo "Installed. Try:  CHAYUAN_HOME=\"$TARGET\" python3 -m chayuan_cli info"
