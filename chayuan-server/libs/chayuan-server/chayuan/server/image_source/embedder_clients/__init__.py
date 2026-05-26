"""89-1:图像嵌入客户端抽象层。

把"如何拿到图像向量"抽象成 Protocol,具体实现可以是:

- :class:`InProcEmbedderClient` —— 进程内直接加载 PyTorch/HF 权重(老路径)
- :class:`InfinityHttpClient`   —— HTTP 走 Infinity 容器(89-2 新链路)

入口:
- :class:`ImageEmbedderClient` —— Protocol,定义统一契约
- :class:`EmbedderUnavailable` —— 客户端不可用时抛(降级链路用)
"""
from __future__ import annotations

from chayuan.server.image_source.embedder_clients.base import (
    EmbedderUnavailable,
    ImageEmbedderClient,
)

__all__ = [
    "EmbedderUnavailable",
    "ImageEmbedderClient",
]
