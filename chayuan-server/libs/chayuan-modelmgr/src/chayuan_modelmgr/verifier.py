"""SHA256 + manifest helpers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST_NAME = ".chayuan-manifest.json"
SKIPPED_DIRS = {"__pycache__", ".git", ".cache"}


def sha256_of_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def iter_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*"):
        if any(part in SKIPPED_DIRS for part in p.parts):
            continue
        if p.is_file() and p.name != MANIFEST_NAME:
            out.append(p)
    return out


def write_manifest(model_dir: Path, *, source: str = "imported") -> Path:
    files = iter_files(model_dir)
    items = []
    total = 0
    for p in files:
        size = p.stat().st_size
        total += size
        items.append({
            "path": str(p.relative_to(model_dir)),
            "size_bytes": size,
            "sha256": sha256_of_file(p),
        })
    payload = {
        "version": 1,
        "source": source,
        "files": items,
        "total_bytes": total,
    }
    out = model_dir / MANIFEST_NAME
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def verify_directory(model_dir: Path) -> tuple[bool, list[str]]:
    """Returns (ok, problems[])."""
    mfile = model_dir / MANIFEST_NAME
    if not mfile.is_file():
        return False, [f"missing {MANIFEST_NAME}"]
    try:
        manifest = json.loads(mfile.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"manifest unreadable: {e}"]
    problems: list[str] = []
    for item in manifest.get("files", []):
        rel = item["path"]
        f = model_dir / rel
        if not f.is_file():
            problems.append(f"missing: {rel}")
            continue
        if f.stat().st_size != int(item["size_bytes"]):
            problems.append(f"size mismatch: {rel}")
            continue
        if item["sha256"] and sha256_of_file(f) != item["sha256"]:
            problems.append(f"sha256 mismatch: {rel}")
    return (not problems), problems


def directory_total_size(d: Path) -> int:
    return sum(p.stat().st_size for p in iter_files(d))
