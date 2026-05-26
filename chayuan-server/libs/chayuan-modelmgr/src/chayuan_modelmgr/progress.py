"""Progress sink shared by CLI (tqdm) and gateway (SSE)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from chayuan_core.events import TOPIC_MODEL_DOWNLOAD, get_bus


@dataclass
class ProgressEvent:
    repo: str
    filename: str = ""
    bytes_done: int = 0
    bytes_total: int = 0
    state: str = "running"  # running | done | error
    message: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "filename": self.filename,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "percent": (self.bytes_done / self.bytes_total * 100) if self.bytes_total else 0.0,
            "state": self.state,
            "message": self.message,
            "ts": self.ts,
        }


class ProgressSink:
    """Threadsafe fan-out: console callback + global event bus."""

    def __init__(self, console_cb: Callable[[ProgressEvent], None] | None = None) -> None:
        self._cb = console_cb
        self._lock = threading.Lock()

    def update(self, ev: ProgressEvent) -> None:
        with self._lock:
            if self._cb is not None:
                try:
                    self._cb(ev)
                except Exception:
                    pass
            try:
                get_bus().publish(TOPIC_MODEL_DOWNLOAD, ev.to_dict())
            except Exception:
                pass
