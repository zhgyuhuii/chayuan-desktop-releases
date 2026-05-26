"""macOS pkg/dmg finalizer (writes a build script next to archive)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PKG_BUILD_SH = """#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PAYLOAD="{archive_name}"
PKG_NAME="chayuan-{version}-{release}.pkg"

WORK="$ROOT/_pkg-work"
rm -rf "$WORK" && mkdir -p "$WORK/payload/Library/Application Support/Chayuan"
cp "$ROOT/$PAYLOAD" "$WORK/payload/Library/Application Support/Chayuan/payload.tar.zst"

pkgbuild --root "$WORK/payload" --identifier com.chayuan.ai \
         --version {version} \
         --install-location / \
         "$ROOT/$PKG_NAME"

echo "Built: $ROOT/$PKG_NAME"
"""


def finalize(*, archive: Path, version: str, release: str) -> Path:
    sh = archive.with_suffix("").with_suffix(".sh")
    sh.write_text(
        PKG_BUILD_SH.format(
            archive_name=archive.name, version=version, release=release
        ),
        encoding="utf-8",
    )
    sh.chmod(0o755)
    if shutil.which("pkgbuild"):
        subprocess.run([str(sh)], check=False, cwd=str(archive.parent))
    return sh
