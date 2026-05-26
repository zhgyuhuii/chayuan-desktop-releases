from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from chayuan_gateway.deps import get_repo
from chayuan_registry import ModelRepository
from chayuan_runtime import AdapterRequest, pick_adapter

router = APIRouter(tags=["audio"])


@router.post("/v1/audio/speech")
def tts(payload: dict[str, Any] = Body(...), repo: ModelRepository = Depends(get_repo)):
    model_id = payload.get("model")
    m = repo.get(model_id) if model_id else None
    if m is None or m.category != "tts":
        raise HTTPException(404, "tts model not found")
    adapter = pick_adapter(m)
    if adapter is None:
        raise HTTPException(503, "no adapter")
    out = adapter.call(AdapterRequest(op="tts", model=m, payload=payload)).body
    audio = out.get("audio") if isinstance(out, dict) else None
    if isinstance(audio, (bytes, bytearray)):
        return Response(content=bytes(audio), media_type="audio/wav")
    return out


@router.post("/v1/audio/transcriptions")
def asr(
    file: UploadFile = File(...),
    model: str = Form(...),
    repo: ModelRepository = Depends(get_repo),
):
    m = repo.get(model)
    if m is None or m.category != "asr":
        raise HTTPException(404, "asr model not found")
    adapter = pick_adapter(m)
    if adapter is None:
        raise HTTPException(503, "no adapter")
    return adapter.call(AdapterRequest(op="asr", model=m, payload={"file": file.filename})).body
