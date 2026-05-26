"""HuggingFace CLIP 系模型 loader（跨模态）。

覆盖家族：
- ``clip``              — openai/clip-vit-*
- ``siglip``            — google/siglip-* 与 google/siglip2-*
- ``chinese_clip``      — OFA-Sys/chinese-clip-vit-*
- ``jina_clip``         — jinaai/jina-clip-*
- ``eva_clip``          — BAAI/EVA-CLIP-*（实际上 transformers 对 EVA-CLIP 的
                          支持需 ``trust_remote_code=True``；我们全开，与 HF 社区
                          约定一致）

统一路径：``AutoProcessor + AutoModel.get_{image,text}_features``；对
没有这两个方法的子模型回退到 ``model(**inputs).image_embeds / text_embeds``。
"""
from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import Any, Union

from chayuan.server.image_source.embedder_base import BaseImageEmbedder
from chayuan.server.image_source.embedder_types import CROSSMODAL, ModelSpec
from chayuan.server.image_source.loaders import register_loader

logger = logging.getLogger("chayuan.image_source.loaders.hf_clip")


def _cache_dir() -> str:
    """与 model_manager 共享缓存根；避免各处写路径各执一词。"""
    from chayuan.server.image_source.model_manager import _cache_root
    return str(_cache_root())


class HFClipEmbedder(BaseImageEmbedder):
    """统一封装 CLIP 系 HF 模型；懒加载、线程安全、bf16 GPU 友好。"""

    capabilities = CROSSMODAL

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

    # ---- lazy load ------------------------------------------------------

    def _lazy_load(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import os as _os
            import torch
            from transformers import AutoModel, AutoProcessor  # type: ignore
            from chayuan.server.image_source.model_manager import (
                _candidate_cache_roots, find_model_load_path,
            )

            # ⚠ 关键防御:默认 local_files_only=True —— 模型不在 cache 时直接报
            # 清晰错误,引导用户去模型广场显式下载。
            #
            # 历史:from_pretrained 默认行为是缓存没命中就**静默拉 HuggingFace**,
            # SigLIP ~400MB / Jina-CLIP-v2 ~1GB,用户上传第一张图就触发后台下载,
            # 经常出现"图像向量服务不可达 + 后台自动下载某个模型 + 还是失败"。
            #
            # CHAYUAN_AUTO_DOWNLOAD_MODELS=1 可显式打开旧行为。
            allow_auto_dl = (
                _os.environ.get("CHAYUAN_AUTO_DOWNLOAD_MODELS", "").strip().lower()
                in ("1", "true", "yes")
            )
            local_only = not allow_auto_dl

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.bfloat16 if self._device == "cuda" else torch.float32

            # 用户反馈:"已经定了图像向量模型 并且文件夹中也有图像向量模型 为什
            # 么还要下载"。根因:之前只看 chayuan 自家 cache(<CHAYUAN_ROOT>/
            # models/huggingface),但用户的模型可能在 ~/.cache/huggingface(HF 默
            # 认)/ HF_HOME / TRANSFORMERS_CACHE / 扁平 git clone 目录。
            # find_model_load_path 在所有候选 cache 里查 → 找到时:
            #   * HF 规范布局 → 返 root,from_pretrained(name, cache_dir=root)
            #   * 扁平布局     → 返扁平 dir 自身,from_pretrained(那 dir)
            located = find_model_load_path(self.name)
            tried_roots = [str(p) for p in _candidate_cache_roots()]

            try:
                if located:
                    # 扁平 dir:含 config.json 的 dir 直接喂给 from_pretrained;
                    # HF 规范 root:把 root 当 cache_dir
                    is_flat = not located.endswith("huggingface") and not _os.path.isdir(
                        _os.path.join(located, "hub")
                    )
                    if is_flat:
                        self._processor = AutoProcessor.from_pretrained(
                            located, trust_remote_code=True,
                            local_files_only=local_only,
                        )
                        self._model = AutoModel.from_pretrained(
                            located, trust_remote_code=True,
                            torch_dtype=dtype,
                            local_files_only=local_only,
                        ).to(self._device).eval()
                    else:
                        self._processor = AutoProcessor.from_pretrained(
                            self.name, cache_dir=located, trust_remote_code=True,
                            local_files_only=local_only,
                        )
                        self._model = AutoModel.from_pretrained(
                            self.name, cache_dir=located, trust_remote_code=True,
                            torch_dtype=dtype,
                            local_files_only=local_only,
                        ).to(self._device).eval()
                else:
                    # finder 没找到 — 还是给 HF 一次机会(走 cache_dir=自家 +
                    # local_files_only=allow_auto_dl);多半失败,catch 后报清晰错
                    self._processor = AutoProcessor.from_pretrained(
                        self.name, cache_dir=_cache_dir(), trust_remote_code=True,
                        local_files_only=local_only,
                    )
                    self._model = AutoModel.from_pretrained(
                        self.name, cache_dir=_cache_dir(), trust_remote_code=True,
                        torch_dtype=dtype,
                        local_files_only=local_only,
                    ).to(self._device).eval()
            except (OSError, ValueError) as e:
                if local_only:
                    paths_str = "\n  • " + "\n  • ".join(tried_roots)
                    raise RuntimeError(
                        f"图像向量模型 {self.name!r} 没在本地缓存。已检查的路径:"
                        f"{paths_str}\n\n"
                        "请到「设置 → AI 平台 → 图像嵌入」点击模型旁的「下载」"
                        "(走国内镜像加速,有进度反馈);如果模型确实下载过但放在"
                        "其它位置,可在 chayuan-server 环境设 ``HF_HOME=<your_path>``"
                        " 让 chayuan 识别;或设 ``CHAYUAN_AUTO_DOWNLOAD_MODELS=1`` 打开"
                        f"静默自动下载(不推荐)。底层错误:{type(e).__name__}: {e}"
                    ) from e
                raise
            # 某些模型（JinaCLIP）projection_dim 才是真实输出维度
            if self.dim <= 0:
                try:
                    cfg = getattr(self._model, "config", None)
                    for attr in ("projection_dim", "hidden_size"):
                        v = getattr(cfg, attr, None) if cfg else None
                        if v:
                            self.dim = int(v)
                            break
                except Exception:  # noqa: BLE001
                    pass

    # ---- helpers --------------------------------------------------------

    def _open_image(self, src):
        import PIL.Image as Image
        if isinstance(src, Image.Image):
            return src.convert("RGB")
        if isinstance(src, (bytes, bytearray)):
            return Image.open(io.BytesIO(src)).convert("RGB")
        return Image.open(str(src)).convert("RGB")

    def _pool_and_norm(self, feats) -> Any:
        import numpy as np
        import torch
        vec = feats[0].detach().cpu().to(torch.float32).numpy()
        norm = float(np.linalg.norm(vec)) or 1.0
        return (vec / norm).astype("float32")

    # ---- embed ----------------------------------------------------------

    def embed_image(self, src: Union[str, bytes, Any]):
        import torch
        self._lazy_load()
        img = self._open_image(src)
        with torch.no_grad():
            inputs = self._processor(images=img, return_tensors="pt").to(self._device)
            if hasattr(self._model, "get_image_features"):
                feats = self._model.get_image_features(**inputs)
            else:
                out = self._model(**inputs)
                feats = getattr(out, "image_embeds", None) or getattr(out, "pooler_output")
            return self._pool_and_norm(feats)

    def embed_text(self, text: str):
        import torch
        self._lazy_load()
        with torch.no_grad():
            inputs = self._processor(
                text=[text or ""], return_tensors="pt", padding=True, truncation=True,
            ).to(self._device)
            if hasattr(self._model, "get_text_features"):
                feats = self._model.get_text_features(**inputs)
            else:
                out = self._model(**inputs)
                feats = getattr(out, "text_embeds", None) or getattr(out, "pooler_output")
            return self._pool_and_norm(feats)


@register_loader("clip", "siglip", "chinese_clip", "jina_clip", "eva_clip")
def _build(spec: ModelSpec) -> BaseImageEmbedder:
    return HFClipEmbedder(spec)
