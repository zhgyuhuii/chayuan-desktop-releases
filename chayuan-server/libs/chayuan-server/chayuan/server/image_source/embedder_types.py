"""图像向量化 · 公共类型（ModelSpec / EmbedderCapabilities）。

把家族元数据与能力声明抽到独立模块，原因有二：

1. **打破循环**：``embedder.py``、``loaders/*``、``model_registry.py`` 都要读
   这些类型，放到独立文件消除了"loader 要引用 embedder，embedder 又要 import
   loader"的环路。
2. **契约稳定**：loader 作者只需实现 ``BaseImageEmbedder`` 并声明
   :class:`EmbedderCapabilities`；业务层（Connector / UI）永远只看能力位，不关心
   家族细节。

这是"Capability-first"设计的基石——同一 KB 必须绑死一个模型（维度一致），
UI / 搜索层可以按能力做不同分支（纯图像模型只开以图搜图入口等）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class EmbedderCapabilities:
    """模型能力矩阵——业务层据此启停 UI 入口 / 裁剪下拉。

    - ``image``       能 embed 图像（基本必选）
    - ``text``        能 embed 文本（CLIP 系列有；DINOv2 / ResNet 没有）
    - ``crossmodal``  文本与图像向量在**同一语义空间**、可余弦比较
                      （text=True 不等于 crossmodal=True——理论上可以有独立 text
                       encoder 但不对齐的模型；保留这个维度以后扩展）
    """

    image: bool = True
    text: bool = False
    crossmodal: bool = False

    def to_dict(self) -> dict:
        return {
            "image": self.image,
            "text": self.text,
            "crossmodal": self.crossmodal,
        }


# 语义便捷常量：绝大多数模型符合这两种组合之一
CROSSMODAL = EmbedderCapabilities(image=True, text=True, crossmodal=True)
IMAGE_ONLY = EmbedderCapabilities(image=True, text=False, crossmodal=False)


@dataclass(frozen=True)
class ModelSpec:
    """单个图像向量化模型的目录项。

    每项必须声明 ``family`` —— :mod:`.loaders` 注册表依据它 dispatch 到
    具体的 ``BaseImageEmbedder`` 实现。``extra_deps`` 是**可选依赖**（如
    timm、open_clip_torch），缺失时 UI 会提示用户 ``pip install``。
    """

    name: str                  # HF repo_id / timm model_id / open_clip id
    family: str                # "clip" / "siglip" / "chinese_clip" / "jina_clip"
                               # / "eva_clip" / "dinov2" / "timm_vision" / "open_clip"
    dim: int
    approx_size_mb: int
    chinese_level: str         # "strong" / "medium" / "weak"
    languages: str
    description: str
    capabilities: EmbedderCapabilities = CROSSMODAL
    extra_deps: Tuple[str, ...] = field(default_factory=tuple)
    license: str = ""          # 可选：Apache-2.0 / MIT / etc

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "family": self.family,
            "dim": self.dim,
            "approx_size_mb": self.approx_size_mb,
            "chinese_level": self.chinese_level,
            "languages": self.languages,
            "description": self.description,
            "capabilities": self.capabilities.to_dict(),
            "extra_deps": list(self.extra_deps),
            "license": self.license,
        }


__all__ = [
    "EmbedderCapabilities",
    "CROSSMODAL",
    "IMAGE_ONLY",
    "ModelSpec",
]
