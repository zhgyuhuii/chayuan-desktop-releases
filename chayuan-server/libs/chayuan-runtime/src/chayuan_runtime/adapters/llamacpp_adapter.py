from __future__ import annotations

from chayuan_runtime.adapters._http import (
    get_client,
    post_streaming,
    stream_openai_sse,
)
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class LlamaCppAdapter(RuntimeAdapter):
    """``llama-server`` (llama.cpp) — 与 OpenAI 协议兼容。

    与 vLLM 区别：它没有 ``/v1/embeddings``（要起单独 ``llama-embedding`` 服务）
    本适配器只暴露 chat。流式同样走标准 OpenAI SSE。

    health probe 用 ``GET /health`` —— llama.cpp 自带的健康端点（200 = ok）。
    """

    name = "llamacpp"
    capabilities = AdapterCapabilities(categories=("chat",), formats=("gguf",))
    health_path = "/health"

    def __init__(self, *, base_url: str = "http://127.0.0.1:18080", mock: bool = True) -> None:
        super().__init__(base_url=base_url, mock=mock)

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            return self._mock_chat(req)

        body = {
            "model": req.model.public_id,
            "messages": req.payload.get("messages", []),
            "stream": bool(req.stream),
        }
        for k in ("temperature", "top_p", "top_k", "max_tokens", "stop", "seed"):
            if k in req.payload:
                body[k] = req.payload[k]

        url = f"{self.base_url}/v1/chat/completions"
        if not req.stream:
            with get_client() as c:
                r = c.post(url, json=body)
                r.raise_for_status()
                return AdapterResponse(op=req.op, model_id=req.model.public_id, body=r.json())

        gen = post_streaming(url, json_body=body, parser=stream_openai_sse)
        return AdapterResponse(
            op=req.op, model_id=req.model.public_id, body=gen, streaming=True,
        )
