"""图像向量化 · Loader 注册表（按家族 dispatch）。

扩展新家族只需三步：

1. 新建 ``loaders/<family>.py``，实现一个继承 :class:`BaseImageEmbedder` 的类
2. 用 :func:`register_loader` 装饰构造函数（或直接塞进 ``LOADERS`` dict）
3. 在 :mod:`chayuan.server.image_source.embedder` 的 ``SUPPORTED_MODELS``
   里添加对应 :class:`ModelSpec`（family= 新家族名）

使用方：

    >>> from chayuan.server.image_source.loaders import create_embedder
    >>> emb = create_embedder(spec)   # spec 来自 SUPPORTED_MODELS

已注册家族：
- ``clip``, ``siglip``, ``chinese_clip``, ``jina_clip``, ``eva_clip``
  → :mod:`.hf_clip`（统一走 transformers AutoModel.get_image_features）
- ``dinov2`` → :mod:`.hf_dinov2`（纯视觉，pooler_output）
- ``timm_vision`` → :mod:`.timm_vision`（ResNet / ConvNeXt 等，可选依赖）
- ``open_clip`` → :mod:`.open_clip_family`（OpenCLIP 生态，可选依赖）
"""
from __future__ import annotations

import logging
from typing import Callable, Dict

from chayuan.server.image_source.embedder_base import BaseImageEmbedder
from chayuan.server.image_source.embedder_types import ModelSpec

logger = logging.getLogger("chayuan.image_source.loaders")

# family → loader factory（接 ModelSpec，返回 BaseImageEmbedder 实例）
LOADERS: Dict[str, Callable[[ModelSpec], BaseImageEmbedder]] = {}


def register_loader(*families: str):
    """装饰器：把一个工厂函数注册为若干家族的 loader。

    常见用法：一个 HF CLIP loader 覆盖 CLIP / SigLIP / Chinese-CLIP 等多个家族。
    """

    def deco(fn: Callable[[ModelSpec], BaseImageEmbedder]):
        for fam in families:
            if fam in LOADERS:
                logger.warning(
                    "family %r 的 loader 被覆盖：%s → %s",
                    fam, LOADERS[fam].__qualname__, fn.__qualname__,
                )
            LOADERS[fam] = fn
        return fn

    return deco


def create_embedder(spec: ModelSpec) -> BaseImageEmbedder:
    """按 spec.family 路由到具体 loader；未注册家族抛 KeyError。"""
    loader = LOADERS.get(spec.family)
    if loader is None:
        raise KeyError(
            f"图像模型家族 {spec.family!r} 未注册 loader；"
            f"请先 `from chayuan.server.image_source.loaders import <module>` 导入。"
        )
    return loader(spec)


# ---- 预导入所有内置 loader（副作用：执行 @register_loader）------------
# 放在模块末尾避免循环 import；如需延迟加载（例如 timm 依赖缺失但不想 import
# 失败），各 loader 模块内部应该**延迟 import 运行时重依赖**（torch / timm）。
from chayuan.server.image_source.loaders import hf_clip  # noqa: E402,F401
from chayuan.server.image_source.loaders import hf_dinov2  # noqa: E402,F401
from chayuan.server.image_source.loaders import timm_vision  # noqa: E402,F401
from chayuan.server.image_source.loaders import open_clip_family  # noqa: E402,F401


__all__ = ["LOADERS", "register_loader", "create_embedder"]
