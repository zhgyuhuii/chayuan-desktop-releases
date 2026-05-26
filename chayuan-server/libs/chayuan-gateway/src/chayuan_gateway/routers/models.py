"""GET /v1/models  +  GET /v1/models/events (SSE)."""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from chayuan_core.events import (
    TOPIC_MODEL_ADDED,
    TOPIC_MODEL_DOWNLOAD,
    TOPIC_MODEL_REMOVED,
    TOPIC_MODEL_UPDATED,
    EventBus,
    get_bus,
)
from chayuan_gateway.deps import get_repo
from chayuan_registry import ModelRepository

router = APIRouter(tags=["models"])


@router.get("/v1/models")
def list_models(
    category: str | None = Query(None),
    enabled: bool | None = Query(None),
    repo: ModelRepository = Depends(get_repo),
):
    items = repo.list(category=category, enabled=enabled)
    return {"object": "list", "data": [m.to_public() for m in items]}


@router.get("/v1/models/events")
async def model_events_stream(request: Request, bus: EventBus = Depends(get_bus)):
    queue: asyncio.Queue = asyncio.Queue(maxsize=128)
    loop = asyncio.get_event_loop()

    def _on_event(ev) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, ev)
        except Exception:
            pass

    bus.subscribe(_on_event, topics=[
        TOPIC_MODEL_ADDED, TOPIC_MODEL_REMOVED, TOPIC_MODEL_UPDATED, TOPIC_MODEL_DOWNLOAD
    ])

    async def _stream() -> AsyncIterator[dict]:
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {"event": ev.topic, "data": json.dumps(ev.to_dict(), ensure_ascii=False)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            bus.unsubscribe(_on_event)

    return EventSourceResponse(_stream())


@router.get("/v1/models/{model_id:path}")
def get_one(model_id: str, repo: ModelRepository = Depends(get_repo)):
    m = repo.get(model_id)
    if m is None:
        raise HTTPException(404, detail="model not found")
    return m.to_public()
