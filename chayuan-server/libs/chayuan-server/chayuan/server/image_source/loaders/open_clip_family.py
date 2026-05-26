"""OpenCLIP 生态 loader（``laion/CLIP-ViT-H-14-laion2B-s32B-b79K`` 等）。

OpenCLIP 是 CLIP 的开源重训版，包含 laion-2B 预训练的大模型（ViT-H / ViT-g）
——在很多 zero-shot benchmark 上比原 OpenAI CLIP 高 5~10pp。

**依赖**：``pip install open_clip_torch``（可选）。

**名字规约**：``ModelSpec.name`` 用 HF repo_id（``open_clip_torch`` ≥ 2.20
直接用 HF hub，无需手写 arch/pretrained 二元组）。
"""
from __future__ import annotations

import io
import logging
from typing import Any, Union

from chayuan.server.image_source.embedder_base import BaseImageEmbedder
from chayuan.server.image_source.embedder_types import CROSSMODAL, ModelSpec
from chayuan.server.image_source.loaders import register_loader

logger = logging.getLogger("chayuan.image_source.loaders.open_clip")


class OpenCLIPEmbedder(BaseImageEmbedder):
    capabilities = CROSSMODAL

    def __init__(self, spec: ModelSpec):
        super().__init__(spec.name)
        self.spec = spec
        self.dim = int(spec.dim or 0)
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._device = "cpu"

    def is_available(self) -> bool:
        try:
            import PIL.Image  # noqa: F401
            import open_clip  # type: ignore  # noqa: F401
            import torch  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def missing_deps_hint(self) -> str:
        return "pip install open_clip_torch torch pillow"

    def _lazy_load(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import open_clip  # type: ignore
            import torch
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            # open_clip 支持 "hf-hub:<repo_id>" 协议
            model, _, preprocess = open_clip.create_model_and_transforms(
                f"hf-hub:{self.name}"
            )
            self._model = model.to(self._device).eval()
            self._preprocess = preprocess
            self._tokenizer = open_clip.get_tokenizer(f"hf-hub:{self.name}")
            if self.dim <= 0:
                cfg = getattr(self._model, "visual", None)
                v = getattr(cfg, "output_dim", None) or getattr(self._model, "embed_dim", 0)
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
        x = self._preprocess(img).unsqueeze(0).to(self._device)
        with torch.no_grad():
            feats = self._model.encode_image(x)
            vec = feats[0].detach().cpu().to(torch.float32).numpy()
        norm = float(np.linalg.norm(vec)) or 1.0
        return (vec / norm).astype("float32")

    def embed_text(self, text: str):
        import numpy as np
        import torch
        self._lazy_load()
        with torch.no_grad():
            tokens = self._tokenizer([text or ""]).to(self._device)
            feats = self._model.encode_text(tokens)
            vec = feats[0].detach().cpu().to(torch.float32).numpy()
        norm = float(np.linalg.norm(vec)) or 1.0
        return (vec / norm).astype("float32")


@register_loader("open_clip")
def _build(spec: ModelSpec) -> BaseImageEmbedder:
    return OpenCLIPEmbedder(spec)
