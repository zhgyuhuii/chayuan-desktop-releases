"""ImageEmbedderClient Protocol — 89-1。

契约设计原则:
  * **batch 优先**:`encode_image(blobs: List[bytes])` 一次接整批,
    实现层负责打包发请求或循环调本地 forward。这样 InfinityHttpClient
    的 batch 优势能透出来,InProc 也可以选 batched forward。
  * **async**:Infinity 走 HTTP 必须 async,InProc 同步逻辑包一层
    `asyncio.to_thread` 即可统一。
  * **健康同步**:`healthcheck()` 同步,O(ms),get_embedder 单例缓存里
    周期性探活用,不能 await 卡住。
  * **维度透出**:`dim` 让 store.py 维度校验直接读这个属性,不再嵌
    模型表。
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable


class EmbedderUnavailable(RuntimeError):
    """客户端不可用(主路径失败 + fallback 失败)。

    上层应该捕获这个异常 → 422 + 推荐安装信息(image_routes 已有逻辑)。
    """


@runtime_checkable
class ImageEmbedderClient(Protocol):
    """图像嵌入客户端协议。

    具体实现见 ``inproc.py`` / ``infinity_http.py``。
    """

    #: 客户端名,用于 trace / 日志,例:"infinity@jinaai/jina-clip-v1"
    name: str

    #: 模型 id(HF repo),如 "jinaai/jina-clip-v1"
    model_id: str

    #: 客户端家族:"inproc" / "infinity" / 未来 "vllm"
    kind: str

    #: 向量维度;首次 encode 后填入,未知时 -1
    dim: int

    async def encode_image(self, blobs: List[bytes]) -> List[List[float]]:
        """对 N 张图(已是 raw bytes,如 jpg/png/webp)求向量。

        返回 ``List[List[float]]``,长度 = N,每个内层 List 长度 = dim。
        L2 归一由实现层负责(in-proc 已归一;Infinity 服务端默认归一)。
        """
        ...

    async def encode_text(self, texts: List[str]) -> List[List[float]]:
        """对 N 段文本求向量(用于 text-image 跨模态查询)。

        模型不支持文本塔时(如 DINOv2)→ 抛 ``TextEmbeddingNotSupported``。
        """
        ...

    def healthcheck(self) -> bool:
        """同步快速探活,返 True/False,不抛。

        实现:
          * inproc → 检查模型是否已加载 / 依赖是否齐
          * infinity_http → GET /health 100ms 超时
        """
        ...

    def close(self) -> None:
        """释放资源(httpx pool / GPU 显存等)。幂等。"""
        ...
