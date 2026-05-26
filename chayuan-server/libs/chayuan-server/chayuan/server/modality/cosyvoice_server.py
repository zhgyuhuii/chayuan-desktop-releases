"""CosyVoice HTTP wrapper(内置)— TTS。

调用方式:
    python -m chayuan.server.modality.cosyvoice_server --host 127.0.0.1 --port 18280

配置文件: <CHAYUAN_ROOT>/runtime/cosyvoice.yaml
"""
from __future__ import annotations

import io
import logging
from typing import Any, Dict

from chayuan.server.modality._runtime_server_base import (
    make_runtime_app,
    parse_serve_args,
    serve,
)

logger = logging.getLogger("chayuan.modality.cosyvoice_server")

_DEFAULT_CONFIG: Dict[str, Any] = {
    "model_dir": "iic/CosyVoice2-0.5B",  # 模型路径或 modelscope id
    "voice": "中文女",                      # 默认音色
    "load_jit": False,
    "load_onnx": False,
    "fp16": False,
    "sample_rate": 22050,
}


def _register_routes(app: Any, cfg: Dict[str, Any]) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import StreamingResponse

    def _ensure_loaded() -> Any:
        if app.state.lib_loaded and app.state.lib_handle is not None:
            return app.state.lib_handle
        try:
            from cosyvoice.cli.cosyvoice import CosyVoice  # type: ignore
        except ImportError as e:
            app.state.lib_error = "cosyvoice 未安装,请 pip install cosyvoice"
            raise HTTPException(status_code=503, detail=app.state.lib_error) from e
        try:
            handle = CosyVoice(
                model_dir=cfg.get("model_dir", "iic/CosyVoice2-0.5B"),
                load_jit=bool(cfg.get("load_jit", False)),
                load_onnx=bool(cfg.get("load_onnx", False)),
                fp16=bool(cfg.get("fp16", False)),
            )
        except Exception as e:  # noqa: BLE001
            app.state.lib_error = f"CosyVoice 初始化失败: {e}"
            logger.exception("CosyVoice init failed")
            raise HTTPException(status_code=503, detail=app.state.lib_error) from e
        app.state.lib_handle = handle
        app.state.lib_loaded = True
        app.state.lib_error = ""
        return handle

    @app.post("/v1/audio/speech")
    async def synthesize(request: Request) -> StreamingResponse:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        text = (payload.get("input") or payload.get("text") or "").strip()
        voice = payload.get("voice") or cfg.get("voice", "中文女")
        if not text:
            raise HTTPException(status_code=400, detail="input 字段必填")

        handle = _ensure_loaded()
        try:
            buf = io.BytesIO()
            # CosyVoice 返回 generator,每帧 numpy float32;直接拼成 wav
            import wave
            import numpy as np  # type: ignore

            chunks = []
            for out in handle.inference_sft(text, voice):
                speech = out.get("tts_speech")
                if speech is None:
                    continue
                arr = speech.cpu().numpy().squeeze()
                chunks.append(arr)
            audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(cfg.get("sample_rate", 22050)))
                pcm16 = (audio * 32767).clip(-32768, 32767).astype("int16").tobytes()
                w.writeframes(pcm16)
            buf.seek(0)
        except Exception as e:  # noqa: BLE001
            logger.exception("CosyVoice synthesize failed")
            raise HTTPException(status_code=500, detail=f"TTS 合成失败: {e}") from e
        return StreamingResponse(buf, media_type="audio/wav")


def main() -> None:
    args = parse_serve_args(default_port=18280)
    app = make_runtime_app(
        framework="cosyvoice",
        title="Chayuan CosyVoice Wrapper",
        default_config=_DEFAULT_CONFIG,
        register_routes=_register_routes,
    )
    serve(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
