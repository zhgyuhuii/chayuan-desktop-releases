from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any, Dict

from chayuan_runtime.adapters._http import (
    get_client,
    post_streaming,
    stream_ndjson,
)
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class OllamaAdapter(RuntimeAdapter):
    """Ollama HTTP API（本地 / 远程都走 ``http://host:11434``）。

    支持的 op：
    * ``chat``  → ``POST /api/chat``  非流式 + NDJSON 流式；
    * ``embedding``/``embed`` → ``POST /api/embeddings``；
    * ``health`` → ``GET /api/tags``（doctor 用）。
    """

    name = "ollama"
    capabilities = AdapterCapabilities(
        categories=("chat", "embedding"),
        formats=("gguf", "unknown"),
        streaming=True,
    )
    health_path = "/api/tags"      # doctor probe 用；返回 ``{models: [...]}``

    def __init__(self, *, base_url: str = "http://127.0.0.1:11434", mock: bool = True) -> None:
        super().__init__(base_url=base_url, mock=mock)

    # -- 主入口 --------------------------------------------------------

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            if req.op in ("embedding", "embed"):
                return self._mock_embedding(req)
            return self._mock_chat(req)

        if req.op in ("embedding", "embed"):
            return self._call_embeddings(req)
        # 默认走 chat
        return self._call_chat(req)

    # -- chat（流式 / 非流式） -----------------------------------------

    def _call_chat(self, req: AdapterRequest) -> AdapterResponse:
        body = {
            "model": req.model.public_id,
            "messages": req.payload.get("messages", []),
            "stream": bool(req.stream),
            "options": req.payload.get("options", {}),
        }
        url = f"{self.base_url}/api/chat"

        if not req.stream:
            with get_client() as c:
                r = c.post(url, json=body)
                r.raise_for_status()
                native = r.json()
            # 把 ollama 原生 schema 转成 OpenAI 兼容
            return AdapterResponse(
                op=req.op,
                model_id=req.model.public_id,
                body=_ollama_to_openai_chat(native, req.model.public_id),
            )

        # 流式：用 post_streaming 拿到一个产 NDJSON 的生成器
        raw_iter = post_streaming(url, json_body=body, parser=stream_ndjson)
        return AdapterResponse(
            op=req.op,
            model_id=req.model.public_id,
            body=_ollama_stream_to_openai(raw_iter, req.model.public_id),
            streaming=True,
        )

    # -- embedding -----------------------------------------------------

    def _call_embeddings(self, req: AdapterRequest) -> AdapterResponse:
        url = f"{self.base_url}/api/embeddings"
        inputs = req.payload.get("input", "")
        if isinstance(inputs, str):
            inputs = [inputs]
        out = []
        with get_client() as c:
            # ollama 原生 /api/embeddings 一次只接受一条 prompt；批量串行调用。
            for i, text in enumerate(inputs):
                r = c.post(url, json={"model": req.model.public_id, "prompt": text})
                r.raise_for_status()
                emb = r.json().get("embedding") or []
                out.append({"object": "embedding", "index": i, "embedding": emb})
        body = {
            "object": "list",
            "model": req.model.public_id,
            "data": out,
            "usage": {"prompt_tokens": sum(len(t) for t in inputs),
                      "total_tokens": sum(len(t) for t in inputs)},
        }
        return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)


# --- 命名翻译 ---------------------------------------------------------


def _ollama_to_openai_chat(native: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    """ollama ``/api/chat`` 非流式 → OpenAI ``chat.completion``。"""
    msg = (native.get("message") or {})
    return {
        "id": f"chatcmpl-ollama-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": {
                "role": msg.get("role") or "assistant",
                "content": msg.get("content") or "",
            },
            "finish_reason": "stop" if native.get("done") else "length",
        }],
        "usage": {
            "prompt_tokens": int(native.get("prompt_eval_count") or 0),
            "completion_tokens": int(native.get("eval_count") or 0),
            "total_tokens": int(native.get("prompt_eval_count") or 0) + int(native.get("eval_count") or 0),
        },
    }


def _ollama_stream_to_openai(
    raw_iter: Iterator[Dict[str, Any]],
    model_id: str,
) -> Iterator[Dict[str, Any]]:
    """ollama 流式 NDJSON → OpenAI ``chat.completion.chunk``。

    OpenAI 协议要求每帧是 ``{choices: [{delta: {content: ...}, ...}]}``，
    最后一帧 ``finish_reason="stop"`` + ``delta={}``。
    """
    chunk_id = f"chatcmpl-ollama-stream-{int(time.time() * 1000)}"
    created = int(time.time())
    started = False
    for line in raw_iter:
        msg = line.get("message") or {}
        content = msg.get("content") or ""
        done = bool(line.get("done"))
        delta: Dict[str, Any] = {}
        if not started and content:
            delta["role"] = "assistant"
            started = True
        if content:
            delta["content"] = content
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_id,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": "stop" if done else None,
            }],
        }
        yield chunk
        if done:
            return
