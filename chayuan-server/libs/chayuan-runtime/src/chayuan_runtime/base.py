"""Adapter ABC.

Each adapter knows:
  * which (category, runtime, format) tuples it can handle
  * how to translate an OpenAI-style request to the backend's wire format
  * how to surface a streaming response

For now we provide a sync `call()` returning either a dict or an iterator of
chunks. Real backends use httpx; mock-mode adapters echo input for tests.
"""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

from chayuan_registry.models import Model


@dataclass
class AdapterCapabilities:
    categories: tuple[str, ...]
    formats: tuple[str, ...]
    streaming: bool = True
    max_concurrent: int = 4
    needs_gpu: bool = False


@dataclass
class AdapterRequest:
    op: str               # "chat" | "embedding" | "rerank" | "tts" | "asr" | "ocr" | "t2i" | "t2v"
    model: Model
    payload: dict[str, Any] = field(default_factory=dict)
    stream: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterResponse:
    op: str
    model_id: str
    body: Any                          # dict or iterator
    headers: dict[str, str] = field(default_factory=dict)
    streaming: bool = False


class RuntimeAdapter(abc.ABC):
    """Abstract adapter base."""

    name: str = "base"
    capabilities: AdapterCapabilities = AdapterCapabilities(categories=(), formats=())
    # 子类可覆写：用于 doctor "/v1/admin/doctor" 的健康探针；空串表示"用 base_url"。
    # 写成相对路径（``/api/tags`` / ``/v1/models``），由 ``health_url()`` 拼接成绝对 URL。
    health_path: str = ""
    # 子类可覆写：subprocess 类适配器（piper / whisper.cpp）置 True，doctor 不会发 HTTP。
    is_subprocess: bool = False

    def __init__(self, *, base_url: str = "", mock: bool = True) -> None:
        self.base_url = base_url
        self.mock = mock

    def health_url(self) -> str:
        """返回当前实例的健康检查 URL；空串表示无可用的健康端点。"""
        if self.is_subprocess:
            return ""
        if not self.base_url:
            return ""
        path = (self.health_path or "").lstrip("/")
        if not path:
            return self.base_url.rstrip("/")
        return f"{self.base_url.rstrip('/')}/{path}"

    def supports(self, model: Model) -> bool:
        if model.runtime != self.name and model.runtime != "auto":
            return False
        cat_ok = (not self.capabilities.categories) or (model.category in self.capabilities.categories)
        fmt_ok = (not self.capabilities.formats) or (model.format in self.capabilities.formats) or model.format == "unknown"
        return cat_ok and fmt_ok

    @abc.abstractmethod
    def call(self, req: AdapterRequest) -> AdapterResponse:
        raise NotImplementedError

    async def acall(self, req: AdapterRequest) -> AdapterResponse:
        return self.call(req)

    # ---- helpers for mock-mode -----------------------------------------

    def _mock_chat(self, req: AdapterRequest) -> AdapterResponse:
        msgs = req.payload.get("messages", [])
        last = msgs[-1]["content"] if msgs else ""
        body = {
            "id": f"chatcmpl-mock-{self.name}",
            "object": "chat.completion",
            "model": req.model.public_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": f"[mock:{self.name}] echo: {last}"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": len(last), "completion_tokens": 8, "total_tokens": len(last) + 8},
        }
        if req.stream:
            def _gen() -> Iterator[dict]:
                for tok in body["choices"][0]["message"]["content"].split():
                    yield {"choices": [{"delta": {"content": tok + " "}, "index": 0}]}
                yield {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=_gen(), streaming=True)
        return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)

    def _mock_embedding(self, req: AdapterRequest) -> AdapterResponse:
        inputs = req.payload.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        body = {
            "object": "list",
            "model": req.model.public_id,
            "data": [
                {"object": "embedding", "index": i, "embedding": [float((j + i) % 11) / 10 for j in range(8)]}
                for i, _ in enumerate(inputs)
            ],
            "usage": {"prompt_tokens": sum(len(t) for t in inputs), "total_tokens": sum(len(t) for t in inputs)},
        }
        return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)

    def _mock_simple(self, req: AdapterRequest, key: str = "result") -> AdapterResponse:
        body = {"model": req.model.public_id, key: f"[mock:{self.name}] op={req.op}"}
        return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)
