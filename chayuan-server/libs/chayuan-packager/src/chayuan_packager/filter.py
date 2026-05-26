"""Release filter: lite / standard / pro presets, overridable by config.

Each preset declares which categories are included AND optionally caps the
number / total size of models per category.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from chayuan_core import get_paths
from chayuan_packager.scan import ScanManifest


@dataclass
class ReleasePreset:
    name: str
    categories: list[str]
    max_models_per_category: int = 999
    max_size_per_category_gb: float = 1024.0
    include_runtimes: list[str] | None = None
    include_services: list[str] | None = None


RELEASE_PRESETS: dict[str, ReleasePreset] = {
    "lite": ReleasePreset(
        name="lite",
        categories=["chat", "embedding", "rerank", "ocr"],
        max_models_per_category=1,
        max_size_per_category_gb=2.0,
        include_runtimes=["python"],
        include_services=["ollama", "redis"],
    ),
    "standard": ReleasePreset(
        name="standard",
        categories=["chat", "embedding", "rerank", "clip", "tts", "asr", "ocr"],
        max_models_per_category=2,
        max_size_per_category_gb=8.0,
        include_runtimes=["python"],
        include_services=["ollama", "postgres", "redis", "piper"],
    ),
    "pro": ReleasePreset(
        name="pro",
        categories=["chat", "embedding", "rerank", "clip", "t2i", "t2v", "tts", "asr", "ocr"],
        max_models_per_category=4,
        max_size_per_category_gb=64.0,
        include_runtimes=None,
        include_services=None,
    ),
}


def _load_overrides() -> dict[str, ReleasePreset]:
    p = get_paths().config / "releases.yaml"
    if not p.is_file():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[str, ReleasePreset] = {}
    for name, body in (raw.get("releases") or {}).items():
        out[name] = ReleasePreset(
            name=name,
            categories=list(body.get("categories", [])),
            max_models_per_category=int(body.get("max_models_per_category", 999)),
            max_size_per_category_gb=float(body.get("max_size_per_category_gb", 1024)),
            include_runtimes=body.get("include_runtimes"),
            include_services=body.get("include_services"),
        )
    return out


def get_preset(release: str) -> ReleasePreset:
    over = _load_overrides()
    if release in over:
        return over[release]
    if release in RELEASE_PRESETS:
        return RELEASE_PRESETS[release]
    raise KeyError(f"unknown release: {release}")


def filter_manifest(manifest: ScanManifest, release: str) -> ScanManifest:
    preset = get_preset(release)
    kept_components: list = []
    cat_count: dict[str, int] = {}
    cat_size: dict[str, int] = {}

    for c in manifest.components:
        if c.kind == "runtime" and preset.include_runtimes is not None and c.name not in preset.include_runtimes:
            continue
        if c.kind == "service" and preset.include_services is not None and c.name not in preset.include_services:
            continue
        if c.kind == "model":
            if c.category not in preset.categories:
                continue
            if cat_count.get(c.category, 0) >= preset.max_models_per_category:
                continue
            new_size = cat_size.get(c.category, 0) + c.size_bytes
            if new_size > preset.max_size_per_category_gb * 1024 * 1024 * 1024:
                continue
            cat_count[c.category] = cat_count.get(c.category, 0) + 1
            cat_size[c.category] = new_size
        kept_components.append(c)

    out = ScanManifest(
        workspace=manifest.workspace,
        components=kept_components,
        total_bytes=sum(c.size_bytes for c in kept_components),
    )
    return out
