from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from chayuan_gateway.deps import get_repo
from chayuan_registry import ModelRepository
from chayuan_runtime import AdapterRequest, pick_adapter

router = APIRouter(tags=["images"])


@router.post("/v1/images/generations")
def t2i(payload: dict[str, Any] = Body(...), repo: ModelRepository = Depends(get_repo)):
    m = repo.get(payload.get("model"))
    if m is None or m.category != "t2i":
        raise HTTPException(404, "t2i model not found")
    adapter = pick_adapter(m)
    if adapter is None:
        raise HTTPException(503, "no adapter")
    body = adapter.call(AdapterRequest(op="t2i", model=m, payload=payload)).body
    return {"created": int(__import__("time").time()), "data": [body]}
