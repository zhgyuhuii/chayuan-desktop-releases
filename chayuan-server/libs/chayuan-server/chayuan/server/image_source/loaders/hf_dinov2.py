"""DINOv2 loader（Meta 自监督纯视觉；仅图像 embed）。

DINOv2 模型 ``facebook/dinov2-{small, base, large, giant}`` 在 transformers
里是 ``Dinov2Model``——没有 ``get_image_features``，但 ``pooler_output``
（[CLS] token 的 pooled 投影）天然就是我们要的图像向量。

**无文本塔**：按 BaseImageEmbedder 契约，``embed_text`` 会抛
:class:`TextEmbeddingNotSupported`；Connector 应在 UI 侧就引导用户只用
"以图搜图"入口。

性能提示：DINOv2-large (768 维 / 1.1GB) 在自监督表征任务（特征相似度、
分类 linear probe）常常超越同等规模 CLIP；适合对"视觉相似而非语义匹配"
敏感的场景（商品图去重、医学影像检索）。
"""
from __future__ import annotations

import io
import logging
from typing import Any, Union

from chayuan.server.image_source.embedder_base import BaseImageEmbedder
from chayuan.server.image_source.embedder_types import IMAGE_ONLY, ModelSpec
from chayuan.server.image_source.loaders import register_loader

logger = logging.getLogger("chayuan.image_source.loaders.hf_dinov2")


class HFDinoV2Embedder(BaseImageEmbedder):
    capabilities = IMAGE_ONLY

    def __init__(self, spec: ModelSpec):
        super().__init__(spec.name)
        self.spec = spec
        self.dim = int(spec.dim or 0)
        self._model = None
        self._processor = None
        self._device = "cpu"

    def is_available(self) -> bool:
        try:
            import PIL.Image  # noqa: F401
            import torch  # noqa: F401
            import transformers  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def missing_deps_hint(self) -> str:
        return "pip install torch transformers pillow"

    def _lazy_load(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import torch
            from transformers import AutoImageProcessor, AutoModel  # type: ignore
            from chayuan.server.image_source.model_manager import _cache_root
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.bfloat16 if self._device == "cuda" else torch.float32
            cache = str(_cache_root())
            self._processor = AutoImageProcessor.from_pretrained(self.name, cache_dir=cache)
            self._model = AutoModel.from_pretrained(
                self.name, cache_dir=cache, torch_dtype=dtype,
            ).to(self._device).eval()
            if self.dim <= 0:
                cfg = getattr(self._model, "config", None)
                v = getattr(cfg, "hidden_size", None) if cfg else None
                if v:
                    self.dim = int(v)

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
        with torch.no_grad():
            inputs = self._processor(images=img, return_tensors="pt").to(self._device)
            out = self._model(**inputs)
            # DINOv2: 优先 pooler_output；缺失则取 last_hidden_state[:, 0, :] (CLS)
            feats = getattr(out, "pooler_output", None)
            if feats is None:
                feats = out.last_hidden_state[:, 0, :]
            vec = feats[0].detach().cpu().to(torch.float32).numpy()
        norm = float(np.linalg.norm(vec)) or 1.0
        return (vec / norm).astype("float32")

    # embed_text 走基类默认实现——抛 TextEmbeddingNotSupported


@register_loader("dinov2")
def _build(spec: ModelSpec) -> BaseImageEmbedder:
    return HFDinoV2Embedder(spec)
