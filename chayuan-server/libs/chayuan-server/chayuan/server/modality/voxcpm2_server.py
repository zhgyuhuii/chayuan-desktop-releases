"""VoxCPM 2 HTTP wrapper(内置)— TTS。

OpenBMB 开源的轻量 TTS:
  * 0.5B 参数,CPU 实时中文合成
  * 支持 voice clone(克隆任意人声)
  * https://github.com/OpenBMB/VoxCPM

调用方式:
    python -m chayuan.server.modality.voxcpm2_server --host 127.0.0.1 --port 18580

配置文件: ``<CHAYUAN_ROOT>/runtime/voxcpm2.yaml``(首次启动自动生成默认)。
被 chayuan-server "运行时与服务" 卡片的"▶ 启动"按钮调用。

懒加载:启动时不预载模型;首次 POST /v1/audio/speech 时才 import voxcpm。
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

logger = logging.getLogger("chayuan.modality.voxcpm2_server")

# 默认配置 — 首次启动写到 <CHAYUAN_ROOT>/runtime/voxcpm2.yaml,UI 可编辑
_DEFAULT_CONFIG: Dict[str, Any] = {
    "model_id": "openbmb/VoxCPM-0.5B",     # HuggingFace 模型 id;首次启动自动下载
    "device": "cpu",                        # cpu / cuda
    "sample_rate": 24000,                   # VoxCPM 默认 24kHz
    "default_voice": "default",             # 默认音色名
    "voice_clones_dir": "",                 # 自定义 voice clone wav 目录(空则关)
    # 优化:long-form 文本时分段合成的最大字符数
    "max_chunk_chars": 200,
}


def _register_routes(app: Any, cfg: Dict[str, Any]) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import StreamingResponse

    def _ensure_loaded() -> Any:
        """懒加载 voxcpm 模型;失败抛 HTTP 503 让 UI 提示。"""
        if app.state.lib_loaded and app.state.lib_handle is not None:
            return app.state.lib_handle
        try:
            from voxcpm import VoxCPM  # type: ignore
        except ImportError as e:
            app.state.lib_error = "voxcpm 未安装,请 pip install voxcpm"
            raise HTTPException(status_code=503, detail=app.state.lib_error) from e
        try:
            handle = VoxCPM.from_pretrained(
                cfg.get("model_id", "openbmb/VoxCPM-0.5B"),
            )
            # device 配置 — 不是所有版本 VoxCPM 都支持参数 device,加 try
            if hasattr(handle, "to") and cfg.get("device"):
                try:
                    handle.to(cfg["device"])
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            app.state.lib_error = f"VoxCPM 初始化失败: {e}"
            logger.exception("VoxCPM init failed")
            raise HTTPException(status_code=503, detail=app.state.lib_error) from e
        app.state.lib_handle = handle
        app.state.lib_loaded = True
        app.state.lib_error = ""
        return handle

    @app.post("/v1/audio/speech")
    async def synthesize(request: Request) -> StreamingResponse:
        """OpenAI 兼容的 TTS 端点。

        body:
            {"input": "你好", "voice": "default" 或 voice clone wav 路径,
             "format": "wav"}
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        text = (payload.get("input") or payload.get("text") or "").strip()
        voice = payload.get("voice") or cfg.get("default_voice", "default")
        if not text:
            raise HTTPException(status_code=400, detail="input 字段必填")

        handle = _ensure_loaded()
        try:
            import wave
            import numpy as np  # type: ignore

            # VoxCPM API 名假定为 ``generate`` 或 ``synthesize`` — 不同版本可能不一
            # 用 hasattr 兜底,适配多版本
            if hasattr(handle, "generate"):
                arr = handle.generate(text=text, prompt_speech_path=voice
                                      if voice and voice != "default" else None)
            elif hasattr(handle, "synthesize"):
                arr = handle.synthesize(text)
            elif callable(handle):
                arr = handle(text)
            else:
                raise RuntimeError(
                    "VoxCPM 实例无 generate/synthesize 方法,请检查版本"
                )
            # arr 可能是 torch.Tensor 或 np.ndarray,统一转 np
            if hasattr(arr, "cpu"):
                arr = arr.cpu().numpy()
            if hasattr(arr, "squeeze"):
                arr = arr.squeeze()
            audio = np.asarray(arr, dtype="float32")

            # 拼成 wav
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(int(cfg.get("sample_rate", 24000)))
                pcm16 = (audio * 32767).clip(-32768, 32767).astype("int16").tobytes()
                w.writeframes(pcm16)
            buf.seek(0)
        except Exception as e:  # noqa: BLE001
            logger.exception("VoxCPM synthesize failed")
            raise HTTPException(status_code=500, detail=f"TTS 合成失败: {e}") from e
        return StreamingResponse(buf, media_type="audio/wav")


def main() -> None:
    args = parse_serve_args(default_port=18580)
    app = make_runtime_app(
        framework="voxcpm2",
        title="Chayuan VoxCPM 2 Wrapper",
        default_config=_DEFAULT_CONFIG,
        register_routes=_register_routes,
    )
    serve(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
