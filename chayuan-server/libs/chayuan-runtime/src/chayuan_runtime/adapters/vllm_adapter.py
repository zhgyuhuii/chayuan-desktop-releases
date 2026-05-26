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


class VllmAdapter(RuntimeAdapter):
    """vLLM OpenAI-compatible server.

    vLLM 直接讲 OpenAI 协议，``/v1/chat/completions`` 流式输出标准 SSE
    （``data: {...}\\n\\n`` + ``data: [DONE]\\n\\n``），所以本适配器在 chat 路径
    上只是穿透：非流式返回 ``r.json()``；流式返回逐帧 dict 的生成器。

    health probe 用 ``GET /v1/models`` —— 标准 OpenAI 协议端点。
    """

    name = "vllm"
    capabilities = AdapterCapabilities(
        categories=("chat", "embedding"),
        formats=("safetensors", "pytorch", "unknown"),
        needs_gpu=True,
    )
    health_path = "/v1/models"

    def __init__(self, *, base_url: str = "http://127.0.0.1:18000", mock: bool = True) -> None:
        super().__init__(base_url=base_url, mock=mock)

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            if req.op in ("embedding", "embed"):
                return self._mock_embedding(req)
            return self._mock_chat(req)

        if req.op in ("embedding", "embed"):
            return self._call_embeddings(req)
        return self._call_chat(req)

    # -- chat ----------------------------------------------------------

    def _call_chat(self, req: AdapterRequest) -> AdapterResponse:
        body = {
            "model": req.model.public_id,
            "messages": req.payload.get("messages", []),
            "stream": bool(req.stream),
            "max_tokens": req.payload.get("max_tokens", 1024),
            "temperature": req.payload.get("temperature", 0.7),
        }
        # 透传 OpenAI 协议里其它常见可选字段
        for k in ("top_p", "top_k", "stop", "presence_penalty", "frequency_penalty",
                  "tools", "tool_choice", "response_format", "seed"):
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

    # -- embedding -----------------------------------------------------

    def _call_embeddings(self, req: AdapterRequest) -> AdapterResponse:
        url = f"{self.base_url}/v1/embeddings"
        with get_client() as c:
            r = c.post(url, json={
                "model": req.model.public_id,
                "input": req.payload.get("input"),
                "encoding_format": req.payload.get("encoding_format", "float"),
            })
            r.raise_for_status()
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=r.json())
