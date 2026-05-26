"""89-3:InProcEmbedderClient — 进程内 PyTorch/HF 加载。

把现有 :class:`BaseImageEmbedder`(同步、单条)包装成符合
:class:`ImageEmbedderClient` 协议的 batch + async 客户端。

性能策略:
  * 单条 forward 串行;batch 转 ``asyncio.gather`` + ``asyncio.to_thread``
    放到默认 executor 跑,避免阻塞 event loop。
  * 全局单例由上层 ``embedder.py:get_embedder`` 管理(本类不做)。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

from chayuan.server.image_source.embedder_clients.base import (
    EmbedderUnavailable,
    ImageEmbedderClient,
)

logger = logging.getLogger("chayuan.image_source.embedder.inproc")


class InProcEmbedderClient(ImageEmbedderClient):
    """把 BaseImageEmbedder 适配成 ImageEmbedderClient。"""

    kind = "inproc"

    def __init__(self, model_id: str, *, max_workers: int = 4) -> None:
        from chayuan.server.image_source.embedder import SUPPORTED_MODELS
        from chayuan.server.image_source.loaders import create_embedder

        self.model_id = model_id
        # 不在 SUPPORTED_MODELS → 直接抛 EmbedderUnavailable。
        # 老代码会"回退默认",但默认本身可能就是这个错误模型(用户在 ④ tab 选了
        # 一个云 API 视觉对话模型如 qwen-vl-max,被 image2text 通道复用),导致
        # KeyError 死循环 + 无意义日志噪声。
        if model_id not in SUPPORTED_MODELS:
            raise EmbedderUnavailable(
                f"InProc 不支持模型 {model_id}(不是本地图像嵌入模型);"
                f"可选:{', '.join(list(SUPPORTED_MODELS.keys())[:5])} ..."
            )
        self._spec = SUPPORTED_MODELS[model_id]
        try:
            self._impl: Optional[Any] = create_embedder(self._spec)
        except Exception as e:  # noqa: BLE001
            logger.warning("inproc 加载 %s 失败: %s", model_id, e)
            self._impl = None
            raise EmbedderUnavailable(
                f"InProc 加载失败: {type(e).__name__}: {e}"
            ) from e

        self.dim = int(getattr(self._spec, "dim", 0) or 0)
        self.name = f"inproc@{self.model_id}"
        self._max_workers = max_workers

    async def encode_image(self, blobs: List[bytes]) -> List[List[float]]:
        if self._impl is None:
            raise EmbedderUnavailable("InProc 未初始化")
        # 单条 forward 转线程池,批量 gather
        loop = asyncio.get_event_loop()
        sem = asyncio.Semaphore(self._max_workers)

        async def _one(b: bytes) -> List[float]:
            async with sem:
                vec = await loop.run_in_executor(None, self._impl.embed_image, b)
                return [float(x) for x in vec]

        return await asyncio.gather(*[_one(b) for b in blobs])

    async def encode_text(self, texts: List[str]) -> List[List[float]]:
        if self._impl is None:
            raise EmbedderUnavailable("InProc 未初始化")
        loop = asyncio.get_event_loop()
        sem = asyncio.Semaphore(self._max_workers)

        async def _one(t: str) -> List[float]:
            async with sem:
                vec = await loop.run_in_executor(None, self._impl.embed_text, t)
                return [float(x) for x in vec]

        return await asyncio.gather(*[_one(t) for t in texts])

    def healthcheck(self) -> bool:
        try:
            return self._impl is not None and self._impl.is_available()
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        # PyTorch 模型靠 GC + cuda.empty_cache 即可,这里不强制
        self._impl = None
