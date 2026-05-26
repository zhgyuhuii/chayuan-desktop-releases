from __future__ import annotations

from chayuan_runtime.adapters._http import get_client
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class CosyVoiceAdapter(RuntimeAdapter):
    name = "cosyvoice"
    capabilities = AdapterCapabilities(categories=("tts",), formats=("pytorch", "safetensors"))
    health_path = "/v1/models"   # cosyvoice-server 也讲 OpenAI 协议

    def __init__(self, *, base_url: str = "http://127.0.0.1:18280", mock: bool = True) -> None:
        super().__init__(base_url=base_url, mock=mock)

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            return self._mock_simple(req, key="audio")
        with get_client() as c:
            r = c.post(
                f"{self.base_url}/v1/audio/speech",
                json={"model": req.model.public_id, "input": req.payload.get("input"),
                      "voice": req.payload.get("voice", "default")},
            )
            r.raise_for_status()
            return AdapterResponse(op=req.op, model_id=req.model.public_id,
                                   body={"audio": r.content, "format": "wav"})
