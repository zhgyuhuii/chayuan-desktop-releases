"""Manifest checksum + license sanity check."""
from __future__ import annotations

from pathlib import Path

from chayuan_packager.scan import ScanManifest


def verify_manifest(manifest: ScanManifest, *, require_license: bool = False) -> tuple[bool, list[str]]:
    workspace = Path(manifest.workspace)
    problems: list[str] = []
    for c in manifest.components:
        d = workspace / c.rel_path
        if not d.exists():
            problems.append(f"missing path: {c.rel_path}")
            continue
        if require_license and c.kind == "model" and not c.license_file:
            problems.append(f"no license file for model: {c.name}")
    return (not problems), problems
