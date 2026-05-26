from __future__ import annotations

import subprocess
from pathlib import Path

from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class PiperAdapter(RuntimeAdapter):
    """piper TTS via subprocess. Outputs raw PCM/wav bytes."""

    name = "piper"
    capabilities = AdapterCapabilities(categories=("tts",), formats=("onnx",))
    is_subprocess = True   # 没有 HTTP 端点；doctor 不去 ping

    def __init__(self, *, base_url: str = "", mock: bool = True, binary: str = "piper") -> None:
        super().__init__(base_url=base_url, mock=mock)
        self.binary = binary

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            body = {"audio": b"RIFFmockwav", "format": "wav", "model": req.model.public_id}
            return AdapterResponse(op=req.op, model_id=req.model.public_id, body=body)
        text = req.payload.get("input") or req.payload.get("text") or ""
        model_path = next(Path(req.model.path).rglob("*.onnx"), None)
        if model_path is None:
            raise FileNotFoundError(f"no .onnx voice in {req.model.path}")
        proc = subprocess.run(
            [self.binary, "--model", str(model_path), "--output_raw"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=120,
            check=True,
        )
        return AdapterResponse(
            op=req.op,
            model_id=req.model.public_id,
            body={"audio": proc.stdout, "format": "raw", "model": req.model.public_id},
        )
