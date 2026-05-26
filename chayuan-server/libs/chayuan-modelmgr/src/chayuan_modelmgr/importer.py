"""Offline import: copy or hardlink an external directory into the chayuan models tree.

The discovery service will pick it up automatically; we only have to:
  1. detect the proper category via identify()
  2. compute the standardised destination path
  3. copy / move / link
  4. write the manifest
  5. push a `model.import` event for the UI
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

from chayuan_core import ensure_dirs, model_dir_for
from chayuan_core.events import TOPIC_MODEL_IMPORT, get_bus
from chayuan_identify import ModelMeta, identify
from chayuan_modelmgr.verifier import write_manifest


def _copy_tree(src: Path, dst: Path, link: bool, on_progress: Callable[[Path], None] | None) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        if link:
            try:
                os.link(p, target)
            except OSError:
                shutil.copy2(p, target)
        else:
            shutil.copy2(p, target)
        if on_progress is not None:
            on_progress(target)


def import_model(
    src: Path | str,
    *,
    repo: str | None = None,
    category: str | None = None,
    move: bool = False,
    hardlink: bool = False,
    on_progress: Callable[[Path], None] | None = None,
) -> tuple[Path, ModelMeta | None]:
    """Standardised import. Returns (destination_dir, identified_meta).

    `repo` defaults to the source dir name (with `--` ↔ `/` round-tripping).
    `category` defaults to the result of identify(). If neither identify nor
    user input gives a category, raises ValueError.
    """
    src_path = Path(src).expanduser().resolve()
    if not src_path.is_dir():
        raise FileNotFoundError(f"source is not a directory: {src_path}")

    ensure_dirs()
    meta_pre = identify(src_path)
    if category is None:
        if meta_pre is None:
            raise ValueError(
                "cannot determine model category — please pass --category explicitly "
                "or place the source under a known category directory"
            )
        category = meta_pre.category

    if repo is None:
        if meta_pre is not None:
            repo = meta_pre.repo
        else:
            name = src_path.name
            repo = name.replace("--", "/", 1)

    dest = model_dir_for(category, repo)
    if dest.exists() and not move and dest.resolve() != src_path:
        for p in src_path.rglob("*"):
            if p.is_file():
                rel = p.relative_to(src_path)
                if (dest / rel).exists():
                    continue
                _copy_tree(src_path, dest, hardlink, on_progress)
                break
        else:
            _copy_tree(src_path, dest, hardlink, on_progress)
    elif move:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(src_path), str(dest))
    else:
        _copy_tree(src_path, dest, hardlink, on_progress)

    try:
        write_manifest(dest, source="imported")
    except Exception:
        pass

    meta_post = identify(dest)
    payload = (meta_post or meta_pre or ModelMeta(repo=repo, name=repo.split("/")[-1],
                                                  category=category, runtime="auto",
                                                  format="unknown", path=str(dest))).to_payload()
    payload["source"] = "imported"
    get_bus().publish(TOPIC_MODEL_IMPORT, payload)
    return dest, meta_post
