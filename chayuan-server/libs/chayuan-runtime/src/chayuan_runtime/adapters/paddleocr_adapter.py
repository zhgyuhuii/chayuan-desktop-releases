from __future__ import annotations

from chayuan_runtime.adapters._http import get_client
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class PaddleOcrAdapter(RuntimeAdapter):
    name = "paddleocr"
    capabilities = AdapterCapabilities(categories=("ocr",), formats=("paddle",))
    # PaddleServing 有 ``/version``，对未实现 /health 的部署也友好
    health_path = "/version"

    def __init__(self, *, base_url: str = "http://127.0.0.1:18480", mock: bool = True) -> None:
        super().__init__(base_url=base_url, mock=mock)

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            body = {"model": req.model.public_id, "blocks": [{"text": "[mock PP-OCR]", "bbox": [0, 0, 100, 20]}]}
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)
        with get_client() as c:
            r = c.post(f"{self.base_url}/predict/ocr_system",
                       json={"images": req.payload.get("images", [])})
            r.raise_for_status()
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=r.json())
