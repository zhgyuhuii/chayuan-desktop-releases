"""Filesystem walker that reconciles disk state with the registry.

Run periodically (every 60s by default) as a safety net for any events the
watchdog observer might miss (network filesystems, transient renames, etc).
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from chayuan_core import ensure_dirs, get_logger
from chayuan_identify import RuleSet, get_default_ruleset, identify
from chayuan_registry import ModelRepository, ModelStatus, session_scope

logger = get_logger("chayuan_discovery.poller")

CATEGORY_DIRS = ("chat", "embedding", "rerank", "clip", "t2i", "t2v", "tts", "asr", "ocr")


def candidate_dirs(models_root: Path) -> Iterable[Path]:
    """Each immediate child of <models_root>/<category>/ is a model dir."""
    for cat in CATEGORY_DIRS:
        cat_dir = models_root / cat
        if not cat_dir.is_dir():
            continue
        for child in cat_dir.iterdir():
            if child.is_dir() and not child.name.startswith("_"):
                yield child


def poll_once(rs: RuleSet | None = None) -> dict:
    """Sweep `<CHAYUAN_HOME>/models/`. Sync registry. Return summary."""
    paths = ensure_dirs()
    rules = rs or get_default_ruleset()

    seen: set[str] = set()
    added = updated = removed = 0
    skipped = 0

    with session_scope() as s:
        repo = ModelRepository(s)
        for d in candidate_dirs(paths.models):
            meta = identify(d, ruleset=rules, models_root=paths.models)
            if meta is None:
                skipped += 1
                continue
            payload = meta.to_payload()
            payload["status"] = ModelStatus.READY.value
            try:
                stat = d.stat()
                payload["file_mtime"] = stat.st_mtime
            except OSError:
                pass
            payload["size_bytes"] = _dir_size(d)
            _, created = repo.upsert(payload)
            seen.add(payload["path"])
            if created:
                added += 1
            else:
                updated += 1

        # soft-remove any registry entry whose files vanished
        for m in repo.list(include_removed=False):
            if m.path not in seen and not Path(m.path).exists():
                repo.soft_remove_by_path(m.path)
                removed += 1

    summary = {"added": added, "updated": updated, "removed": removed, "skipped": skipped}
    logger.info("poll.complete", **summary)
    return summary


def _dir_size(d: Path) -> int:
    total = 0
    for p in d.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total
