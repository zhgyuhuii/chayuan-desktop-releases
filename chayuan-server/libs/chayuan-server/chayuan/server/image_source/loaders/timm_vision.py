"""timm 经典 CNN 视觉骨干 loader（ResNet / ConvNeXt / EfficientNet ...）。

这些模型原本是 ImageNet 分类器：倒数第二层（classifier head 之前）的
feature embedding 是"通用视觉表征"，广泛用于细粒度图像检索。

**依赖**：``pip install timm``（可选，不装则 ``is_available()=False``，UI
会灰显并提示安装）。

**名字规约**：``ModelSpec.name`` 填 timm 识别的 model_id，如
``timm/resnet50.a1_in1k`` 或裸 ``resnet50``；loader 自动解析 ``timm/`` 前缀。

**无文本塔**：纯视觉，``embed_text`` 抛 :class:`TextEmbeddingNotSupported`。
"""
from __future__ import annotations

import io
import logging
from typing import Any, Union

from chayuan.server.image_source.embedder_base import BaseImageEmbedder
from chayuan.server.image_source.embedder_types import IMAGE_ONLY, ModelSpec
from chayuan.server.image_source.loaders import register_loader

logger = logging.getLogger("chayuan.image_source.loaders.timm_vision")


class TimmVisionEmbedder(BaseImageEmbedder):
    capabilities = IMAGE_ONLY

    def __init__(self, spec: ModelSpec):
        super().__init__(spec.name)
        self.spec = spec
        self.dim = int(spec.dim or 0)
        self._model = None
        self._transform = None
        self._device = "cpu"

    def is_available(self) -> bool:
        try:
            import PIL.Image  # noqa: F401
            import timm  # noqa: F401
            import torch  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def missing_deps_hint(self) -> str:
        return "pip install timm torch pillow"

    def _lazy_load(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import timm  # type: ignore
            import torch
            from timm.data import create_transform, resolve_model_data_config  # type: ignore

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            # timm/ 前缀兼容 HF hub；裸名走内置 registry
            model_id = self.name
            self._model = timm.create_model(
                model_id, pretrained=True, num_classes=0,   # num_classes=0 → 去掉分类头，输出 pooled feature
            ).to(self._device).eval()
            cfg = resolve_model_data_config(self._model)
            self._transform = create_transform(**cfg, is_training=False)
            # 动态维度：用伪输入前向一次拿维度
            if self.dim <= 0:
                with torch.no_grad():
                    x = torch.zeros(
                        1, 3, cfg.get("input_size", (3, 224, 224))[1],
                        cfg.get("input_size", (3, 224, 224))[2],
                    ).to(self._device)
                    y = self._model(x)
                    self.dim = int(y.shape[-1])

    def _open_image(self, src):
        import PIL.Image as Image
        if isinstance(src, Image.Image):
            return src.convert("RGB")
        if isinstance(src, (bytes, bytearray)):
            return Image.open(io.BytesIO(src)).convert("RGB")
        return Image.open(str(src)).convert("RGB")

    def embed_image(self, src: Union[str, bytes, Any]):
        import numpy as np
        import torch
        self._lazy_load()
        img = self._open_image(src)
        x = self._transform(img).unsqueeze(0).to(self._device)
        with torch.no_grad():
            y = self._model(x)
            vec = y[0].detach().cpu().to(torch.float32).numpy()
        norm = float(np.linalg.norm(vec)) or 1.0
        return (vec / norm).astype("float32")


@register_loader("timm_vision")
def _build(spec: ModelSpec) -> BaseImageEmbedder:
    return TimmVisionEmbedder(spec)
