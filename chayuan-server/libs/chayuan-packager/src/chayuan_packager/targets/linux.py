"""Linux finalizer (AppImage script + tar.zst alias)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

APPDIR_SH = """#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
APPDIR="$ROOT/Chayuan.AppDir"
rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr/share/chayuan"
cp "$ROOT/{archive_name}" "$APPDIR/usr/share/chayuan/payload.tar.zst"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/share/chayuan/launcher" "$@"
EOF
chmod +x "$APPDIR/AppRun"

if command -v appimagetool >/dev/null; then
    appimagetool "$APPDIR" "$ROOT/chayuan-{version}-{release}.AppImage"
fi
"""


def finalize(*, archive: Path, version: str, release: str) -> Path:
    sh = archive.with_suffix("").with_suffix(".sh")
    sh.write_text(
        APPDIR_SH.format(archive_name=archive.name, version=version, release=release),
        encoding="utf-8",
    )
    sh.chmod(0o755)
    if shutil.which("appimagetool"):
        subprocess.run([str(sh)], check=False, cwd=str(archive.parent))
    return sh
