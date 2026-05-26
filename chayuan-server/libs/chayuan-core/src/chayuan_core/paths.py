"""Cross-platform path resolution.

CHAYUAN_HOME resolution priority:
1. environment variable CHAYUAN_HOME
2. (Windows)  %LOCALAPPDATA%\\Chayuan
3. (macOS)    ~/Library/Application Support/Chayuan
4. (Linux)    ${XDG_DATA_HOME:-~/.local/share}/chayuan
5. (devmode)  $PWD if a `pyproject.toml` workspace marker exists here

Subdirectories under CHAYUAN_HOME (all created on demand):
    vendor/   runtimes & service binaries (drop-in)
    models/   model files (drop-in, by category)
    data/     postgres, redis, milvus state
    cache/    KV / intermediate
    outputs/  generated images / videos / audio
    logs/     per-process rolling logs
    config/   yaml / env
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _default_home() -> Path:
    """Best-effort OS-default path."""
    if env := os.environ.get("CHAYUAN_HOME"):
        return Path(env).expanduser().resolve()

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "Chayuan"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Chayuan"
    # linux + others
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "chayuan"


def _devmode_home() -> Path | None:
    """If $PWD is a workspace root, prefer it (developer mode)."""
    cwd = Path.cwd()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "pyproject.toml").is_file() and (candidate / "packages").is_dir():
            return candidate
        if candidate == candidate.parent:
            break
    return None


CHAYUAN_HOME: Path = _devmode_home() or _default_home()


@dataclass(frozen=True)
class Paths:
    home: Path
    vendor: Path
    runtimes: Path
    services: Path
    models: Path
    data: Path
    cache: Path
    outputs: Path
    logs: Path
    config: Path
    hf_cache: Path

    def all(self) -> list[Path]:
        return [
            self.home, self.vendor, self.runtimes, self.services, self.models,
            self.data, self.cache, self.outputs, self.logs, self.config, self.hf_cache,
        ]


_MODEL_CATEGORIES = ["chat", "embedding", "rerank", "clip", "t2i", "t2v", "tts", "asr", "ocr"]


def get_paths(home: Path | None = None) -> Paths:
    h = (home or CHAYUAN_HOME).resolve()
    vendor = h / "vendor"
    return Paths(
        home=h,
        vendor=vendor,
        runtimes=vendor / "runtimes",
        services=vendor / "services",
        models=h / "models",
        data=h / "data",
        cache=h / "cache",
        outputs=h / "outputs",
        logs=h / "logs",
        config=h / "config",
        hf_cache=h / "models" / "_hf_cache",
    )


def ensure_dirs(home: Path | None = None) -> Paths:
    """Create all known directories. Idempotent."""
    p = get_paths(home)
    for d in p.all():
        d.mkdir(parents=True, exist_ok=True)
    for cat in _MODEL_CATEGORIES:
        (p.models / cat).mkdir(parents=True, exist_ok=True)
    return p


def model_dir_for(category: str, repo: str, home: Path | None = None) -> Path:
    """Standardized destination directory for a (category, repo) pair.

    repo "owner/name" → "owner--name" (filesystem-safe and reversible).
    """
    safe = repo.replace("/", "--").strip()
    return get_paths(home).models / category / safe
