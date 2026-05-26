"""``BaseImageEmbedder`` —— 所有家族 loader 都继承它。

契约：
- :meth:`embed_image`  能接受 path / bytes / PIL.Image，返回归一化 numpy 向量
- :meth:`embed_text`   可选；不支持文本塔的家族抛 :class:`TextEmbeddingNotSupported`
- :meth:`is_available` 只做**依赖探测**（不加载权重、不联网），O(ms) 无副作用
- :attr:`name`/``dim``/``capabilities``  供注册表与 Connector 校验一致性

懒加载策略：`_lazy_load` 由子类实现；首次 embed 时才拉权重。
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any, Union

from chayuan.server.image_source.embedder_types import (
    EmbedderCapabilities, IMAGE_ONLY,
)


class TextEmbeddingNotSupported(RuntimeError):
    """纯视觉模型（DINOv2 / ResNet / timm）被要求 embed_text 时抛这个异常。

    上层业务应在 UI 侧就屏蔽"文本查图"入口；真跑到这里说明配置与能力不匹配，
    属于应当暴露而非吞掉的错误。
    """


class BaseImageEmbedder(ABC):
    """所有图像向量化后端的统一基类。"""

    #: 模型名；子类在 __init__ 里赋值
    name: str = "base"
    #: 向量维度；懒加载成功后应准确；未加载时 0
    dim: int = 0
    #: 能力矩阵；默认"仅图像"（保守），跨模态子类构造时覆盖
    capabilities: EmbedderCapabilities = IMAGE_ONLY

    def __init__(self, model_name: str):
        self.name = model_name
        self._lock = threading.Lock()

    # ---- 依赖探测（不联网、不加载） -------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """是否具备运行时依赖（torch / transformers / timm ...）。"""

    def missing_deps_hint(self) -> str:
        """未就绪时的 ``pip install`` 提示；默认空。子类可覆写。"""
        return ""

    # ---- 实际 embed -----------------------------------------------------

    @abstractmethod
    def embed_image(self, src: Union[str, bytes, Any]) -> Any:
        """接受 path / bytes / PIL.Image，返回 numpy ndarray(dim,)，已 L2 归一。"""

    def embed_text(self, text: str) -> Any:  # noqa: D401
        """文本 embed；默认不支持（纯视觉家族直接抛）。"""
        if not self.capabilities.text:
            raise TextEmbeddingNotSupported(
                f"模型 {self.name} 不支持文本向量化（仅视觉编码器）；"
                "请改用 CLIP / SigLIP / Chinese-CLIP 等跨模态模型。"
            )
        raise NotImplementedError  # 子类必须覆写


__all__ = [
    "BaseImageEmbedder",
    "TextEmbeddingNotSupported",
]
