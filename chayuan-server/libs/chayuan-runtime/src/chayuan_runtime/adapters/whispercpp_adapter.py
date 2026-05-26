from __future__ import annotations

import subprocess
from pathlib import Path

from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)


class WhisperCppAdapter(RuntimeAdapter):
    """ASR via whisper.cpp CLI (`whisper-cli` / `main`). Cross-platform binary."""

    name = "whispercpp"
    capabilities = AdapterCapabilities(categories=("asr",), formats=("gguf", "ggml"))
    is_subprocess = True   # subprocess 调用，无 HTTP；doctor 跳过 ping

    def __init__(self, *, base_url: str = "", mock: bool = True, binary: str = "whisper-cli") -> None:
        super().__init__(base_url=base_url, mock=mock)
        self.binary = binary

    def call(self, req: AdapterRequest) -> AdapterResponse:
        if self.mock:
            return self._mock_simple(req, key="text")
        wav = req.payload.get("file") or req.payload.get("audio_path")
        if not wav:
            raise ValueError("audio file path required")
        model_path = Path(req.model.path)
        gguf = next((p for p in model_path.rglob("*.gguf")), None) or next(model_path.rglob("*.bin"), None)
        if gguf is None:
            raise FileNotFoundError(f"no whisper model file found under {model_path}")
        cmd = [self.binary, "-m", str(gguf), "-f", str(wav), "-otxt", "-of", "/tmp/whisper-out"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        text = Path("/tmp/whisper-out.txt").read_text(encoding="utf-8", errors="ignore").strip()
        return AdapterResponse(op=req.op, model_id=req.model.public_id, body={"text": text})
