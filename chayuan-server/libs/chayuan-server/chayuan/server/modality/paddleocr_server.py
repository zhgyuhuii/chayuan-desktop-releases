"""PaddleOCR HTTP wrapper(内置)。

调用方式:
    python -m chayuan.server.modality.paddleocr_server --host 127.0.0.1 --port 18480

配置文件: <CHAYUAN_ROOT>/runtime/paddleocr.yaml
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

logger = logging.getLogger("chayuan.modality.paddleocr_server")

_DEFAULT_CONFIG: Dict[str, Any] = {
    "lang": "ch",                   # ch / en / japan / korean / german / french ...
    "use_angle_cls": True,           # 角度分类
    "use_gpu": False,
    "det": True,
    "rec": True,
    "cls": True,
    "show_log": False,
}


def _register_routes(app: Any, cfg: Dict[str, Any]) -> None:
    from fastapi import HTTPException, Request

    def _ensure_loaded() -> Any:
        if app.state.lib_loaded and app.state.lib_handle is not None:
            return app.state.lib_handle
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except ImportError as e:
            app.state.lib_error = (
                "paddleocr 未安装,请 pip install paddleocr paddlepaddle"
            )
            raise HTTPException(status_code=503, detail=app.state.lib_error) from e
        try:
            handle = PaddleOCR(
                use_angle_cls=bool(cfg.get("use_angle_cls", True)),
                lang=cfg.get("lang", "ch"),
                use_gpu=bool(cfg.get("use_gpu", False)),
                show_log=bool(cfg.get("show_log", False)),
            )
        except Exception as e:  # noqa: BLE001
            app.state.lib_error = f"PaddleOCR 初始化失败: {e}"
            raise HTTPException(status_code=503, detail=app.state.lib_error) from e
        app.state.lib_handle = handle
        app.state.lib_loaded = True
        app.state.lib_error = ""
        return handle

    @app.post("/v1/ocr")
    async def ocr(request: Request) -> Dict[str, Any]:
        """OCR 端点。

        body:
            * {"image": "<base64>"}
            * {"image_url": "https://..."}
            * {"image_path": "/abs/path"}
        """
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        image_input: Any = None
        if isinstance(payload, dict):
            if payload.get("image"):
                import io
                from PIL import Image  # type: ignore
                import numpy as np  # type: ignore
                buf = base64.b64decode(payload["image"])
                image_input = np.array(Image.open(io.BytesIO(buf)).convert("RGB"))
            elif payload.get("image_url"):
                import urllib.request
                import io
                from PIL import Image  # type: ignore
                import numpy as np  # type: ignore
                with urllib.request.urlopen(payload["image_url"], timeout=30) as r:
                    image_input = np.array(Image.open(io.BytesIO(r.read())).convert("RGB"))
            elif payload.get("image_path"):
                image_input = payload["image_path"]

        if image_input is None:
            raise HTTPException(
                status_code=400,
                detail="必须提供 image(base64) / image_url / image_path 之一",
            )

        handle = _ensure_loaded()
        try:
            result = handle.ocr(
                image_input,
                det=cfg.get("det", True),
                rec=cfg.get("rec", True),
                cls=cfg.get("cls", True),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("PaddleOCR ocr failed")
            raise HTTPException(status_code=500, detail=f"OCR 失败: {e}") from e

        boxes = []
        # PaddleOCR 返回 [[ [ [box], (text, score) ], ... ]]
        if result and result[0]:
            for item in result[0]:
                box = item[0] if len(item) > 0 else []
                text = item[1][0] if len(item) > 1 and item[1] else ""
                score = float(item[1][1]) if len(item) > 1 and item[1] else 0.0
                boxes.append({"box": box, "text": text, "score": score})
        return {"boxes": boxes, "lang": cfg.get("lang", "ch")}

    @app.get("/version")
    async def version() -> Dict[str, str]:
        try:
            import paddleocr  # type: ignore
            return {"version": getattr(paddleocr, "__version__", "unknown")}
        except ImportError:
            return {"version": "(paddleocr 未安装)"}


def main() -> None:
    args = parse_serve_args(default_port=18480)
    app = make_runtime_app(
        framework="paddleocr",
        title="Chayuan PaddleOCR Wrapper",
        default_config=_DEFAULT_CONFIG,
        register_routes=_register_routes,
    )
    serve(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
