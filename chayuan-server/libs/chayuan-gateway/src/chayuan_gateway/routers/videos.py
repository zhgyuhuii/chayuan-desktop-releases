"""Async-style /v1/videos/generations: returns a task_id then poll /v1/videos/<id>."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from chayuan_gateway.deps import get_repo
from chayuan_registry import ModelRepository
from chayuan_runtime import AdapterRequest, pick_adapter

router = APIRouter(tags=["videos"])

_TASKS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _store(task_id: str, payload: dict) -> None:
    with _LOCK:
        _TASKS[task_id] = payload


def _get(task_id: str) -> dict | None:
    with _LOCK:
        return _TASKS.get(task_id)


@router.post("/v1/videos/generations")
def t2v(payload: dict[str, Any] = Body(...), repo: ModelRepository = Depends(get_repo)):
    m = repo.get(payload.get("model"))
    if m is None or m.category != "t2v":
        raise HTTPException(404, "t2v model not found")
    adapter = pick_adapter(m)
    if adapter is None:
        raise HTTPException(503, "no adapter")
    task_id = uuid.uuid4().hex
    _store(task_id, {"id": task_id, "state": "queued", "created": int(time.time())})

    def _bg() -> None:
        try:
            body = adapter.call(AdapterRequest(op="t2v", model=m, payload=payload)).body
            _store(task_id, {"id": task_id, "state": "succeeded", "result": body})
        except Exception as e:
            _store(task_id, {"id": task_id, "state": "failed", "error": str(e)})

    threading.Thread(target=_bg, daemon=True).start()
    return {"id": task_id, "state": "queued"}


@router.get("/v1/videos/{task_id}")
def get_task(task_id: str):
    t = _get(task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    return t
