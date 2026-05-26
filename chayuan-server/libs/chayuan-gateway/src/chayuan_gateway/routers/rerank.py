from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from chayuan_gateway.deps import get_repo
from chayuan_registry import ModelRepository
from chayuan_runtime import AdapterRequest, pick_adapter

router = APIRouter(tags=["rerank"])


@router.post("/v1/rerank")
def rerank(payload: dict[str, Any] = Body(...), repo: ModelRepository = Depends(get_repo)):
    model_id = payload.get("model")
    if not model_id:
        raise HTTPException(400, "missing 'model'")
    m = repo.get(model_id)
    if m is None:
        raise HTTPException(404, "unknown model")
    if m.category != "rerank":
        raise HTTPException(409, "model is not a reranker")
    adapter = pick_adapter(m)
    if adapter is None:
        raise HTTPException(503, "no adapter")
    return adapter.call(AdapterRequest(op="rerank", model=m, payload=payload)).body
