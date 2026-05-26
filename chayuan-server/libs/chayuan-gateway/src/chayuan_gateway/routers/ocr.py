from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from chayuan_gateway.deps import get_repo
from chayuan_registry import ModelRepository
from chayuan_runtime import AdapterRequest, pick_adapter

router = APIRouter(tags=["ocr"])


@router.post("/v1/ocr")
def ocr_json(payload: dict[str, Any] = Body(...), repo: ModelRepository = Depends(get_repo)):
    m = repo.get(payload.get("model"))
    if m is None or m.category != "ocr":
        raise HTTPException(404, "ocr model not found")
    adapter = pick_adapter(m)
    if adapter is None:
        raise HTTPException(503, "no adapter")
    return adapter.call(AdapterRequest(op="ocr", model=m, payload=payload)).body


@router.post("/v1/ocr/upload")
def ocr_upload(file: UploadFile = File(...), model: str = Form(...),
               repo: ModelRepository = Depends(get_repo)):
    m = repo.get(model)
    if m is None or m.category != "ocr":
        raise HTTPException(404, "ocr model not found")
    adapter = pick_adapter(m)
    if adapter is None:
        raise HTTPException(503, "no adapter")
    return adapter.call(AdapterRequest(op="ocr", model=m, payload={"file": file.filename})).body
