"""FastAPI dependency providers."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, status

from chayuan_core import load_config
from chayuan_registry import ModelRepository, session_scope


def get_repo() -> Iterator[ModelRepository]:
    with session_scope() as s:
        yield ModelRepository(s)


def require_model(public_id: str, repo: ModelRepository = Depends(get_repo)):
    m = repo.get(public_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown model: {public_id}")
    if not m.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"model disabled: {public_id}")
    return m


def get_config():
    return load_config()
