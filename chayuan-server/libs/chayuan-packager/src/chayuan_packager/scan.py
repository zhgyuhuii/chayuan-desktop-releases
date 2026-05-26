"""Workspace scanner: discovers vendor + models drop-ins."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# kind values
KIND_RUNTIME = "runtime"
KIND_SERVICE = "service"
KIND_MODEL = "model"


@dataclass
class Component:
    kind: str
    name: str               # e.g. "ollama" / "Qwen/Qwen2.5-3B-Instruct-GGUF"
    category: str = ""      # for models: chat/embedding/...
    rel_path: str = ""      # path relative to the workspace root
    size_bytes: int = 0
    license_file: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanManifest:
    workspace: str
    components: list[Component] = field(default_factory=list)
    total_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "workspace": self.workspace,
            "components": [c.to_dict() for c in self.components],
            "total_bytes": self.total_bytes,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def _dir_size(d: Path) -> int:
    total = 0
    for p in d.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _find_license(d: Path) -> str:
    for cand in ("LICENSE", "LICENSE.md", "LICENSE.txt", "license", "COPYING"):
        f = d / cand
        if f.is_file():
            return str(f.relative_to(d.parent.parent.parent))
    return ""


def scan(workspace: Path | str) -> ScanManifest:
    root = Path(workspace).resolve()
    manifest = ScanManifest(workspace=str(root))

    vendor = root / "vendor"
    for kind_dir, kind in (("runtimes", KIND_RUNTIME), ("services", KIND_SERVICE)):
        base = vendor / kind_dir
        if not base.is_dir():
            continue
        for sub in base.iterdir():
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            sz = _dir_size(sub)
            if sz == 0:
                continue
            manifest.components.append(Component(
                kind=kind, name=sub.name,
                rel_path=str(sub.relative_to(root)),
                size_bytes=sz,
                license_file=_find_license(sub),
            ))

    models = root / "models"
    if models.is_dir():
        for cat_dir in models.iterdir():
            if not cat_dir.is_dir() or cat_dir.name.startswith("_") or cat_dir.name.startswith("."):
                continue
            for repo_dir in cat_dir.iterdir():
                if not repo_dir.is_dir() or repo_dir.name.startswith("."):
                    continue
                sz = _dir_size(repo_dir)
                if sz == 0:
                    continue
                repo = repo_dir.name.replace("--", "/", 1)
                manifest.components.append(Component(
                    kind=KIND_MODEL, name=repo, category=cat_dir.name,
                    rel_path=str(repo_dir.relative_to(root)),
                    size_bytes=sz,
                    license_file=_find_license(repo_dir),
                ))

    manifest.total_bytes = sum(c.size_bytes for c in manifest.components)
    return manifest
