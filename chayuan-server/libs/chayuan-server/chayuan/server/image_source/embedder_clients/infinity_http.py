"""89-2:InfinityHttpClient — HTTP 走 Infinity 容器。

Infinity (michaelf34/infinity:0.0.75+) 提供 OpenAI 兼容的 ``/embeddings``
接口,接受文本 + 图像(base64)输入。本客户端把它包成
:class:`ImageEmbedderClient`。

性能要点:
  * 全局 ``httpx.AsyncClient`` 复用,连接池 keep-alive
  * encode_* 一次性把 batch 打包发送,Infinity 服务端 batched forward
  * 健康检查 100ms 超时,快速失败让上层降级
"""
from __future__ import annotations

import base64
import logging
from typing import List, Optional

from chayuan.server.image_source.embedder_clients.base import (
    EmbedderUnavailable,
    ImageEmbedderClient,
)

logger = logging.getLogger("chayuan.image_source.embedder.infinity")


def _b64_data_url(blob: bytes) -> str:
    """把 raw image bytes → ``data:image/...;base64,...``。

    Infinity 0.0.75+ 兼容这种 data url 与裸 base64 两种;选 data url 更稳。
    简单按 magic bytes 推 mime,不做严格识别。
    """
    if blob.startswith(b"\x89PNG"):
        mime = "image/png"
    elif blob.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        mime = "image/webp"
    elif blob.startswith(b"GIF8"):
        mime = "image/gif"
    else:
        mime = "image/jpeg"  # fallback
    return f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"


class InfinityHttpClient(ImageEmbedderClient):
    """走 Infinity HTTP /embeddings 的实现。"""

    kind = "infinity"

    def __init__(
        self,
        base_url: str,
        model_id: str,
        *,
        timeout: float = 30.0,
        pool_size: int = 8,
    ) -> None:
        try:
            import httpx
        except ImportError as e:  # noqa: BLE001
            raise EmbedderUnavailable(
                f"httpx 未安装:{e}。pip install httpx"
            ) from e

        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.dim = -1
        self.name = f"infinity@{model_id}"
        self._timeout = timeout
        self._pool_size = pool_size
        self._client: Optional["httpx.AsyncClient"] = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(
                max_connections=pool_size,
                max_keepalive_connections=pool_size,
            ),
            headers={"User-Agent": "chayuan-image-embedder/1.0"},
        )

    async def _post_embeddings(self, inputs: List[str]) -> List[List[float]]:
        if self._client is None:
            raise EmbedderUnavailable("InfinityHttpClient 已 close")
        payload = {"model": self.model_id, "input": inputs}
        try:
            r = await self._client.post("/embeddings", json=payload)
        except Exception as e:  # noqa: BLE001
            raise EmbedderUnavailable(
                f"Infinity 不可达:{type(e).__name__}: {e}"
            ) from e
        if r.status_code != 200:
            raise EmbedderUnavailable(
                f"Infinity 返回 {r.status_code}: {r.text[:200]}"
            )
        try:
            data = r.json()["data"]
            vectors = [d["embedding"] for d in data]
        except (KeyError, ValueError) as e:
            raise EmbedderUnavailable(
                f"Infinity 响应解析失败:{type(e).__name__}: {e}"
            ) from e
        if vectors and self.dim < 0:
            self.dim = len(vectors[0])
        return vectors

    async def encode_image(self, blobs: List[bytes]) -> List[List[float]]:
        if not blobs:
            return []
        inputs = [_b64_data_url(b) for b in blobs]
        return await self._post_embeddings(inputs)

    async def encode_text(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return await self._post_embeddings(list(texts))

    def healthcheck(self) -> bool:
        # 同步 + 100ms 超时;用单独的 sync httpx 客户端,避免 event loop 干扰
        try:
            import httpx
            with httpx.Client(timeout=httpx.Timeout(0.5, connect=0.3)) as c:
                r = c.get(f"{self.base_url}/health")
                return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        # AsyncClient.aclose 必须 async 调;这里尽量幂等,event loop 不在
        # 时(进程退出)忽略错误。
        if self._client is None:
            return
        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._client.aclose())
                else:
                    loop.run_until_complete(self._client.aclose())
            except RuntimeError:
                # 没有 event loop → 同步 close 兜底
                pass
        except Exception:  # noqa: BLE001
            pass
        self._client = None
