from __future__ import annotations

from chayuan_runtime.adapters._http import get_client
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class InfinityAdapter(RuntimeAdapter):
    """One process, three jobs: text embedding / reranking / image embedding (CLIP)."""

    name = "infinity"
    capabilities = AdapterCapabilities(
        categories=("embedding", "rerank", "clip"),
        formats=("safetensors", "bin", "onnx", "unknown"),
    )
    # infinity 暴露 ``GET /models``（OpenAI-likey）+ ``GET /health``；后者更轻
    health_path = "/health"

    def __init__(self, *, base_url: str = "http://127.0.0.1:7997", mock: bool = True) -> None:
        super().__init__(base_url=base_url, mock=mock)

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            if req.op in ("embedding", "embed"):
                return self._mock_embedding(req)
            if req.op == "rerank":
                docs = req.payload.get("documents", [])
                body = {
                    "model": req.model.public_id,
                    "results": [{"index": i, "relevance_score": 1.0 - i * 0.1} for i, _ in enumerate(docs)],
                }
                return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)
            return self._mock_simple(req, key="vector")
        with get_client() as c:
            if req.op in ("embedding", "embed"):
                r = c.post(
                    f"{self.base_url}/embeddings",
                    json={"model": req.model.public_id, "input": req.payload.get("input")},
                )
            elif req.op == "rerank":
                r = c.post(
                    f"{self.base_url}/rerank",
                    json={
                        "model": req.model.public_id,
                        "query": req.payload.get("query"),
                        "documents": req.payload.get("documents"),
                    },
                )
            else:
                r = c.post(f"{self.base_url}/{req.op}", json=req.payload)
            r.raise_for_status()
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=r.json())
