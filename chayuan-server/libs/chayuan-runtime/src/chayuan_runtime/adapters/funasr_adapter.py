from __future__ import annotations

from chayuan_runtime.adapters._http import get_client
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class FunAsrAdapter(RuntimeAdapter):
    name = "funasr"
    capabilities = AdapterCapabilities(categories=("asr",), formats=("onnx", "pytorch"))
    # 第三方 funasr-server 默认有 ``/health``；缺失时退到根路径
    health_path = "/health"

    def __init__(self, *, base_url: str = "http://127.0.0.1:18180", mock: bool = True) -> None:
        super().__init__(base_url=base_url, mock=mock)

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            return self._mock_simple(req, key="text")
        with get_client() as c:
            r = c.post(
                f"{self.base_url}/v1/audio/transcriptions",
                json={"model": req.model.public_id, "file": req.payload.get("file")},
            )
            r.raise_for_status()
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=r.json())
