"""Process-local event bus.

Used as the lingua franca between discovery → registry → gateway SSE.

The bus is intentionally minimal: subscribers register a callable and an
optional topic filter; publishers fire-and-forget. Async listeners are
supported via `subscribe_async`.

For cross-process delivery (e.g. supervisor → gateway), publish to the bus
and let the gateway forward via SSE.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

# ---- topic constants (single source of truth) ----------------------------
TOPIC_MODEL_ADDED = "model.added"
TOPIC_MODEL_REMOVED = "model.removed"
TOPIC_MODEL_UPDATED = "model.updated"
TOPIC_MODEL_DOWNLOAD = "model.download"
TOPIC_MODEL_IMPORT = "model.import"
TOPIC_PROCESS_STATE = "process.state"
TOPIC_PROCESS_LOG = "process.log"
TOPIC_RULE_RELOADED = "rules.reloaded"

ALL_TOPICS = (
    TOPIC_MODEL_ADDED,
    TOPIC_MODEL_REMOVED,
    TOPIC_MODEL_UPDATED,
    TOPIC_MODEL_DOWNLOAD,
    TOPIC_MODEL_IMPORT,
    TOPIC_PROCESS_STATE,
    TOPIC_PROCESS_LOG,
    TOPIC_RULE_RELOADED,
)


@dataclass
class Event:
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return {"id": self.id, "topic": self.topic, "ts": self.ts, "payload": self.payload}


SyncListener = Callable[[Event], None]
AsyncListener = Callable[[Event], "asyncio.Future[None] | None"]


class EventBus:
    def __init__(self) -> None:
        self._sync: list[tuple[set[str] | None, SyncListener]] = []
        self._async: list[tuple[set[str] | None, AsyncListener, asyncio.AbstractEventLoop]] = []
        self._lock = threading.RLock()
        self._history: list[Event] = []
        self._max_history = 1024

    def subscribe(self, listener: SyncListener, topics: Iterable[str] | None = None) -> None:
        with self._lock:
            self._sync.append((set(topics) if topics else None, listener))

    def unsubscribe(self, listener: SyncListener) -> None:
        with self._lock:
            self._sync = [t for t in self._sync if t[1] is not listener]

    def subscribe_async(
        self,
        listener: AsyncListener,
        topics: Iterable[str] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        loop = loop or asyncio.get_event_loop()
        with self._lock:
            self._async.append((set(topics) if topics else None, listener, loop))

    def publish(self, topic: str, payload: dict | None = None) -> Event:
        ev = Event(topic=topic, payload=payload or {})
        with self._lock:
            self._history.append(ev)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]
            sync = list(self._sync)
            asy = list(self._async)
        for filt, fn in sync:
            if filt is None or topic in filt:
                try:
                    fn(ev)
                except Exception:  # noqa: BLE001 — listener errors must not poison the bus
                    pass
        for filt, fn, loop in asy:
            if filt is None or topic in filt:
                try:
                    asyncio.run_coroutine_threadsafe(_call_async(fn, ev), loop)
                except Exception:
                    pass
        return ev

    def history(self, since_id: str | None = None, topic: str | None = None) -> list[Event]:
        with self._lock:
            items = list(self._history)
        if since_id is not None:
            try:
                idx = next(i for i, e in enumerate(items) if e.id == since_id)
                items = items[idx + 1 :]
            except StopIteration:
                pass
        if topic:
            items = [e for e in items if e.topic == topic]
        return items


async def _call_async(fn: AsyncListener, ev: Event) -> None:
    res = fn(ev)
    if asyncio.iscoroutine(res):
        await res


_GLOBAL_BUS: EventBus | None = None
_GLOBAL_LOCK = threading.Lock()


def get_bus() -> EventBus:
    global _GLOBAL_BUS
    if _GLOBAL_BUS is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_BUS is None:
                _GLOBAL_BUS = EventBus()
    return _GLOBAL_BUS
