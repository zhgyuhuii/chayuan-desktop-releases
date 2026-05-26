"""Watchdog-based file event observer with debouncing.

Whenever a model directory's contents change, we re-run identification on the
*model directory* (NOT every leaf file event), and let the registry decide
whether to add / update / soft-remove.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from chayuan_core import ensure_dirs, get_logger
from chayuan_identify import get_default_ruleset, identify
from chayuan_registry import ModelRepository, ModelStatus, session_scope

logger = get_logger("chayuan_discovery.watcher")

CATEGORY_DIRS = ("chat", "embedding", "rerank", "clip", "t2i", "t2v", "tts", "asr", "ocr")


class _Debouncer:
    def __init__(self, interval_ms: int, fn) -> None:
        self.interval = interval_ms / 1000.0
        self.fn = fn
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def schedule(self, key: str, *args) -> None:
        with self._lock:
            t = self._timers.pop(key, None)
            if t is not None:
                t.cancel()
            new = threading.Timer(self.interval, self._fire, args=(key, args))
            new.daemon = True
            self._timers[key] = new
            new.start()

    def _fire(self, key: str, args) -> None:
        with self._lock:
            self._timers.pop(key, None)
        try:
            self.fn(*args)
        except Exception as e:
            logger.warning("debouncer.fire.error", key=key, err=str(e))


def _model_dir_for_event(event_path: Path, models_root: Path) -> Path | None:
    """Resolve a leaf-level event path to its owning <models_root>/<cat>/<repo>/ dir."""
    try:
        rel = event_path.resolve().relative_to(models_root.resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    cat = parts[0]
    if cat not in CATEGORY_DIRS:
        return None
    return models_root / cat / parts[1]


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "ModelTreeWatcher") -> None:
        self.w = watcher

    def _handle(self, event: FileSystemEvent) -> None:
        if event.is_directory and event.event_type not in ("created", "deleted"):
            return
        path = Path(event.src_path)
        d = _model_dir_for_event(path, self.w.models_root)
        if d is None:
            return
        self.w.debouncer.schedule(str(d), d)
        if hasattr(event, "dest_path") and event.dest_path:
            d2 = _model_dir_for_event(Path(event.dest_path), self.w.models_root)
            if d2 is not None and d2 != d:
                self.w.debouncer.schedule(str(d2), d2)

    def on_created(self, event):  # noqa: D401
        self._handle(event)

    def on_deleted(self, event):
        self._handle(event)

    def on_modified(self, event):
        self._handle(event)

    def on_moved(self, event):
        self._handle(event)


class ModelTreeWatcher:
    def __init__(self, models_root: Path | None = None, debounce_ms: int = 500) -> None:
        self.models_root = (models_root or ensure_dirs().models).resolve()
        self.debouncer = _Debouncer(debounce_ms, self._reidentify)
        self.observer = Observer()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        for cat in CATEGORY_DIRS:
            d = self.models_root / cat
            d.mkdir(parents=True, exist_ok=True)
        handler = _Handler(self)
        self.observer.schedule(handler, str(self.models_root), recursive=True)
        self.observer.start()
        self._started = True
        logger.info("watcher.started", root=str(self.models_root))

    def stop(self) -> None:
        if not self._started:
            return
        self.observer.stop()
        self.observer.join(timeout=5)
        self._started = False
        logger.info("watcher.stopped")

    def _reidentify(self, model_dir: Path) -> None:
        with session_scope() as s:
            repo = ModelRepository(s)
            if not model_dir.exists():
                repo.soft_remove_by_path(str(model_dir))
                return
            meta = identify(model_dir, ruleset=get_default_ruleset(), models_root=self.models_root)
            if meta is None:
                return
            payload = meta.to_payload()
            payload["status"] = ModelStatus.READY.value
            try:
                payload["file_mtime"] = model_dir.stat().st_mtime
            except OSError:
                pass
            payload["size_bytes"] = sum(
                p.stat().st_size for p in model_dir.rglob("*") if p.is_file()
            )
            repo.upsert(payload)


def _now() -> float:
    return time.time()
