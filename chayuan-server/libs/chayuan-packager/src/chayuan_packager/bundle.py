"""Generic bundling: zstd-compressed tar dispatched to per-target post-processors."""
from __future__ import annotations

import json
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

import zstandard as zstd

from chayuan_packager.scan import ScanManifest


@dataclass
class BundleResult:
    archive: Path
    manifest_path: Path
    size_bytes: int


def _make_tar_zst(workspace: Path, manifest: ScanManifest, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cctx = zstd.ZstdCompressor(level=15, threads=-1)
    with out_path.open("wb") as raw, cctx.stream_writer(raw) as zw, tarfile.open(fileobj=zw, mode="w|") as tar:
        for c in manifest.components:
            d = workspace / c.rel_path
            if not d.exists():
                continue
            tar.add(d, arcname=c.rel_path, recursive=True)
        # ship the manifest INSIDE the archive too
        tmp_manifest = workspace / ".chayuan-bundle-manifest.json"
        tmp_manifest.write_text(manifest.to_json(), encoding="utf-8")
        tar.add(tmp_manifest, arcname="manifest.json")
        tmp_manifest.unlink(missing_ok=True)
    return out_path.stat().st_size


def bundle(
    manifest: ScanManifest,
    *,
    target: str,
    release: str,
    version: str,
    out_dir: Path,
    dry_run: bool = False,
) -> BundleResult:
    workspace = Path(manifest.workspace)
    name = f"chayuan-{version}-{target}-{release}.tar.zst"
    archive = out_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    side_manifest = out_dir / f"{name}.manifest.json"

    if dry_run:
        side_manifest.write_text(json.dumps({
            "dry_run": True,
            "target": target, "release": release, "version": version,
            "workspace": str(workspace),
            "expected_archive": str(archive),
            "components": [c.to_dict() for c in manifest.components],
            "total_bytes": manifest.total_bytes,
            "ts": int(time.time()),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        return BundleResult(archive=archive, manifest_path=side_manifest, size_bytes=0)

    size = _make_tar_zst(workspace, manifest, archive)
    side_manifest.write_text(manifest.to_json(), encoding="utf-8")

    # delegate to platform-specific finalizer (e.g. wrap in .nsis / .pkg / .AppImage)
    if target == "win":
        from chayuan_packager.targets import win
        win.finalize(archive=archive, version=version, release=release)
    elif target == "mac":
        from chayuan_packager.targets import mac
        mac.finalize(archive=archive, version=version, release=release)
    elif target == "linux":
        from chayuan_packager.targets import linux
        linux.finalize(archive=archive, version=version, release=release)

    return BundleResult(archive=archive, manifest_path=side_manifest, size_bytes=size)
