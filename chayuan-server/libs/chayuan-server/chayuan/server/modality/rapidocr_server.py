"""RapidOCR HTTP wrapper(内置)。

调用方式:
    python -m chayuan.server.modality.rapidocr_server --host 127.0.0.1 --port 18380

配置文件: <CHAYUAN_ROOT>/runtime/rapidocr.yaml
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict

from chayuan.server.modality._runtime_server_base import (
    make_runtime_app,
    parse_serve_args,
    serve,
)

logger = logging.getLogger("chayuan.modality.rapidocr_server")

_DEFAULT_CONFIG: Dict[str, Any] = {
    "engine": "onnxruntime",       # onnxruntime / paddle / openvino
    "lang": "ch",                   # ch / en / japan / korean ...
    "use_det": True,                # 是否检测
    "use_cls": True,                # 是否方向分类
    "use_rec": True,                # 是否识别
    "min_box_size": 20,
}


def _register_routes(app: Any, cfg: Dict[str, Any]) -> None:
    from fastapi import Body, HTTPException

    def _ensure_loaded() -> Any:
        if app.state.lib_loaded and app.state.lib_handle is not None:
            return app.state.lib_handle
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except ImportError as e:
            app.state.lib_error = "rapidocr 未安装,请 pip install rapidocr-onnxruntime"
            raise HTTPException(status_code=503, detail=app.state.lib_error) from e
        try:
            handle = RapidOCR()
        except Exception as e:  # noqa: BLE001
            app.state.lib_error = f"RapidOCR 初始化失败: {e}"
            raise HTTPException(status_code=503, detail=app.state.lib_error) from e
        app.state.lib_handle = handle
        app.state.lib_loaded = True
        app.state.lib_error = ""
        return handle

    @app.post("/v1/ocr")
    async def ocr(
        # ⚠ 之前用 `request: Request`,FastAPI 0.110+ / Pydantic 2.11 环境下把
        # 它误识成 query param 返 422 `loc=("query","request")`。改用显式 Body 标注:
        # `embed=False` 让整个 JSON 作 body,不强制 ``{"key": ...}`` 包裹;
        # Body(default=...) 让空 body 不报缺失参数。
        payload: Dict[str, Any] = Body(default_factory=dict, embed=False),
    ) -> Dict[str, Any]:
        """OCR 端点。

        body:
            * {"image": "<base64>"}
            * {"image_url": "https://..."}
            * {"image_path": "/abs/path"}
        """
        image = None
        if isinstance(payload, dict):
            if payload.get("image"):
                image = base64.b64decode(payload["image"])
            elif payload.get("image_url"):
                import urllib.request
                with urllib.request.urlopen(payload["image_url"], timeout=30) as r:
                    image = r.read()
            elif payload.get("image_path"):
                from pathlib import Path
                p = Path(payload["image_path"])
                if not p.exists():
                    raise HTTPException(status_code=400, detail=f"路径不存在: {p}")
                image = p.read_bytes()

        if image is None:
            raise HTTPException(
                status_code=400,
                detail="必须提供 image(base64) / image_url / image_path 之一",
            )

        handle = _ensure_loaded()
        try:
            import io
            from PIL import Image  # type: ignore
            import numpy as np  # type: ignore

            arr = np.array(Image.open(io.BytesIO(image)).convert("RGB"))
            result, elapsed = handle(
                arr,
                use_det=cfg.get("use_det", True),
                use_cls=cfg.get("use_cls", True),
                use_rec=cfg.get("use_rec", True),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("RapidOCR ocr failed")
            raise HTTPException(status_code=500, detail=f"OCR 失败: {e}") from e

        boxes = []
        for item in result or []:
            box, text, score = item
            boxes.append({"box": box, "text": text, "score": float(score)})
        return {"boxes": boxes, "elapsed_ms": int((elapsed or [0])[0] * 1000)}

    @app.get("/version")
    async def version() -> Dict[str, str]:
        try:
            import rapidocr_onnxruntime  # type: ignore
            return {"version": getattr(rapidocr_onnxruntime, "__version__", "unknown")}
        except ImportError:
            return {"version": "(rapidocr 未安装)"}


def main() -> None:
    args = parse_serve_args(default_port=18380)
    app = make_runtime_app(
        framework="rapidocr",
        title="Chayuan RapidOCR Wrapper",
        default_config=_DEFAULT_CONFIG,
        register_routes=_register_routes,
    )
    serve(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
