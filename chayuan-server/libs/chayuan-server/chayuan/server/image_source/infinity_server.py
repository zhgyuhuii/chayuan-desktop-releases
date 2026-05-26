"""image_source 的 HTTP sidecar wrapper (Plan 3D)。

调用方式:
    python -m chayuan.server.image_source.infinity_server \\
        --host 127.0.0.1 --port 62586

配置文件: <CHAYUAN_ROOT>/runtime/infinity.yaml(首次启动自动生成默认配置)。

模型加载:启动时后台线程预加载进程内 embedder(_get_inproc_embedder),
       首张图不用干等;还没加载完时首次 POST /embeddings 会等待加载完成。
       ⚠ 用 _get_inproc_embedder 而非 get_embedder —— 后者会把请求路由回
       image-embedding sidecar,而本进程就是该 sidecar,会自己请求自己。

端点:
  * POST /embeddings — michaelf34/infinity 兼容签名
      body: {"input": [str | {"image": "data:image/...;base64,..."}], "model": str}
      ← : {"data": [{"index": int, "embedding": [float, ...]}], "model": str}
"""
from __future__ import annotations

import asyncio
import base64
import logging
import threading
from typing import Any, Dict, List

from fastapi import HTTPException, Request  # noqa: E402 — module-level 让 FastAPI 注解解析正确

from chayuan.server.modality._runtime_server_base import (
    make_runtime_app, parse_serve_args, serve,
)

logger = logging.getLogger("chayuan.image_source.infinity_server")


_DEFAULT_CONFIG: Dict[str, Any] = {
    "model": "openai/clip-vit-base-patch32",
    "device": "cpu",
    "max_batch_size": 16,
}


def _decode_data_url(s: str) -> bytes:
    """``data:image/...;base64,xxx`` → bytes(兼容裸 base64)。"""
    if not s.startswith("data:"):
        return base64.b64decode(s)
    try:
        _hdr, b64 = s.split(",", 1)
        return base64.b64decode(b64)
    except Exception as e:
        raise ValueError(f"invalid data url: {e!r}") from e


def _to_list(vec: Any) -> List[float]:
    """把 embedder 返的 np.ndarray / list / tensor 统一转 List[float]。"""
    if hasattr(vec, "tolist"):
        return list(vec.tolist())
    return list(vec)


def _register_routes(app: Any, cfg: Dict[str, Any]) -> None:
    """挂 /embeddings 端点。"""

    _load_lock = threading.Lock()

    def _ensure_loaded() -> Any:
        """加载进程内 embedder;失败抛 HTTP 503。线程安全(预加载线程 + 请求并发)。"""
        if app.state.lib_loaded and app.state.lib_handle is not None:
            return app.state.lib_handle
        with _load_lock:
            # 取到锁后再查一次 —— 可能别的线程刚加载好
            if app.state.lib_loaded and app.state.lib_handle is not None:
                return app.state.lib_handle
            try:
                # ⚠ 必须用 _get_inproc_embedder,不能用 get_embedder。
                # get_embedder 会先看 image-embedding sidecar 是否 ready,ready 就
                # 返回一个 POST 到该 sidecar 的 HTTP 客户端 —— 而本进程**就是**那个
                # sidecar,于是变成自己 POST 自己、递归挂死 → ReadTimeout。
                # sidecar 必须直接用进程内 loader 真正算 embedding。
                from chayuan.server.image_source.embedder import _get_inproc_embedder
                handle = _get_inproc_embedder(cfg.get("model"))
            except Exception as e:
                app.state.lib_error = f"image embedder 加载失败: {e}"
                logger.exception("image embedder init failed")
                raise HTTPException(status_code=503, detail=app.state.lib_error) from e
            app.state.lib_handle = handle
            app.state.lib_loaded = True
            app.state.lib_error = ""
            return handle

    def _embed_one(embedder: Any, item: Any, idx: int) -> List[float]:
        """对一个 input item 调 embedder,返 embedding 列表。

        BaseImageEmbedder API 是 embed_image(src) / embed_text(text) 单数,
        所以这里循环处理 batch(infinity 协议接 List 但我们一项一项跑)。
        """
        if isinstance(item, dict) and "image" in item:
            blob = _decode_data_url(item["image"])
            vec = embedder.embed_image(blob)
        elif isinstance(item, str):
            vec = embedder.embed_text(item)
        else:
            raise ValueError(f"input[{idx}] 不是 str 或 {{image}}")
        return _to_list(vec)

    @app.post("/embeddings")
    async def embeddings(request: Request) -> Dict[str, Any]:
        """OpenAI / infinity 兼容 embedding 端点。"""
        body = await request.json()
        embedder = _ensure_loaded()
        inputs = body.get("input", [])
        if not isinstance(inputs, list) or not inputs:
            raise HTTPException(status_code=400, detail="`input` 必须是非空 list")

        # 同步 embed_* 调用放 thread pool 避免阻塞事件循环
        loop = asyncio.get_running_loop()
        data: List[Dict[str, Any]] = []
        for i, item in enumerate(inputs):
            try:
                emb = await loop.run_in_executor(None, _embed_one, embedder, item, i)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            except Exception as e:
                # embed_image / embed_text 内部其它失败 → 500
                logger.exception("embed item %d failed", i)
                raise HTTPException(status_code=500, detail=f"embed failed: {e}") from e
            data.append({"index": i, "embedding": emb})

        return {"data": data, "model": cfg.get("model", "unknown")}

    @app.on_event("startup")
    async def _preload_image_model() -> None:
        """启动即在后台预加载模型 —— 不等首次请求,首张图就不用干等模型加载。

        放后台线程跑:模型加载(读权重 + torch 初始化)是慢的同步操作,不能
        阻塞 HTTP server 起来 / health 就绪(否则可能拖过 sidecar 健康超时)。
        """
        def _warm() -> None:
            try:
                emb = _ensure_loaded()
                emb.embed_text("warmup")  # 触发进程内 loader 真正加载模型
                logger.info("[infinity] 图像模型预加载完成")
            except Exception as e:  # noqa: BLE001 — 预加载失败不致命,首次请求会重试
                logger.warning("[infinity] 图像模型预加载失败(首次请求会重试): %r", e)

        asyncio.get_event_loop().run_in_executor(None, _warm)


def main() -> None:  # pragma: no cover - real serve path
    """image-embedding sidecar 入口。

    两条拉起路径都汇到这里:
      * 开发模式:``python -m chayuan.server.image_source.infinity_server ...``
      * frozen:``chayuan-server --sidecar-mode image-embedding ...`` —— frozen 下
        sys.executable 是 chayuan-server 本体、无法 ``python -m``,由
        ``chayuan/__main__.py`` 摘掉 ``--sidecar-mode <cap>`` 后调用本函数。
    """
    args = parse_serve_args(default_port=62586)
    cfg = dict(_DEFAULT_CONFIG)
    if args.model:
        # SidecarRuntimeManager 启动时透传 scanner 给的 model_id(本地路径或 HF repo)。
        # 没传时 fallback 到 _DEFAULT_CONFIG 的 openai/clip-vit-base-patch32(会去 HF 下载)。
        cfg["model"] = args.model
    app = make_runtime_app(
        framework="infinity",
        title="Chayuan Image Embedding Sidecar",
        default_config=cfg,
        register_routes=_register_routes,
    )
    serve(app, host=args.host, port=args.port)


if __name__ == "__main__":  # pragma: no cover - real serve path
    main()
