from __future__ import annotations

from chayuan_runtime.adapters._http import get_client
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class RapidOcrAdapter(RuntimeAdapter):
    name = "rapidocr"
    capabilities = AdapterCapabilities(categories=("ocr",), formats=("onnx",))
    health_path = "/health"

    def __init__(self, *, base_url: str = "http://127.0.0.1:18380", mock: bool = True) -> None:
        super().__init__(base_url=base_url, mock=mock)

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            body = {"model": req.model.public_id, "blocks": [{"text": "[mock OCR result]", "bbox": [0, 0, 100, 20]}]}
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)
        with get_client() as c:
            r = c.post(f"{self.base_url}/ocr",
                       files={"image": req.payload.get("file")} if req.payload.get("file") else None,
                       data={"model": req.model.public_id})
            r.raise_for_status()
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=r.json())
