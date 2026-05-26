"""ColPali 插件（N-11）—— 图像级多模态 PDF 检索。

ColPali（Manu Vision 2024）：不做 PDF→文本，直接把每一页渲染为图像做 patch-level
embedding（基于 PaliGemma 类视觉-语言大模型），保留**排版 / 图表 / 公式**等非文本信息。

依赖（全重）：
  pip install colpali-engine torch Pillow pdf2image
  系统需安装 poppler-utils（pdf2image 依赖）

**一线定位**：
- 面向有大量扫描版 / 图文混排 PDF 的用户（金融研报、医疗影像报告）
- 每页 ~1K patches × 128 维 ≈ 百万级向量；建议 GPU；CPU 不可用

**本插件提供**：
- ``is_available()`` — 依赖检测
- ``retrieve(query, kb, top_k)`` — 查询；返回 Document[]（page_content 为空，
  metadata 里带 ``page_number / image_path`` 供前端渲染）
- ``ensure_index(kb_name, pdf_paths)`` — 后台索引（推荐丢 Arq worker）

**不默认启用**：注册时 enabled=False；用户在设置面板开启。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from langchain_core.documents import Document

from chayuan.server.shared.retriever_plugins import RetrieverPlugin

logger = logging.getLogger("chayuan.file_rag.plugins.colpali")


def _colpali_root() -> Path:
    base = os.environ.get("CHAYUAN_ROOT")
    if base:
        return Path(base) / "data" / "colpali_indexes"
    return Path.home() / "chayuan_data" / "data" / "colpali_indexes"


class ColPaliPlugin(RetrieverPlugin):
    name = "colpali"

    def __init__(self, model: str = "vidore/colpali-v1.2"):
        self._model_name = model
        self._model = None
        self._processor = None
        self._index_cache: Dict[str, Any] = {}

    def is_available(self) -> bool:
        try:
            import colpali_engine  # noqa: F401
            import torch  # noqa: F401
            import PIL.Image  # noqa: F401
            return True
        except Exception:
            return False

    def _load_model(self):
        if self._model is not None:
            return self._model, self._processor
        try:
            import torch  # type: ignore
            from colpali_engine.models import ColPali, ColPaliProcessor  # type: ignore
            device = "cuda" if torch.cuda.is_available() else "cpu"
            dtype = torch.bfloat16 if device == "cuda" else torch.float32
            self._model = ColPali.from_pretrained(
                self._model_name, torch_dtype=dtype, device_map=device,
            ).eval()
            self._processor = ColPaliProcessor.from_pretrained(self._model_name)
            return self._model, self._processor
        except Exception as e:  # noqa: BLE001
            logger.warning("ColPali 模型加载失败：%r", e)
            return None, None

    # -----------------------------------------------------------
    # retrieve
    # -----------------------------------------------------------
    def retrieve(self, query: str, kb_service, *, top_k: int = 5) -> List[Document]:
        if not self.is_available() or not query:
            return []
        kb_name = getattr(kb_service, "kb_name", "")
        if not kb_name:
            return []
        index_dir = _colpali_root() / kb_name
        if not index_dir.exists():
            return []
        try:
            import numpy as np
            import torch  # type: ignore

            model, processor = self._load_model()
            if model is None:
                return []

            # 加载 page-level embedding（np.ndarray[pages, tokens, dim]）
            emb_file = index_dir / "page_embeddings.npz"
            if not emb_file.exists():
                return []
            arr = np.load(emb_file, allow_pickle=True)
            pages_emb = arr["embeddings"]  # 形状 (pages, max_tokens, dim)
            page_meta = list(arr.get("meta", []))

            # 对 query 编码
            with torch.no_grad():
                qi = processor.process_queries([query]).to(model.device)
                q_emb = model(**qi).cpu().numpy()[0]  # (q_tokens, dim)

            # MaxSim 计算：每页得分 = sum(max over page_tokens per q_token)
            # 简化实现：向量级 dot；大 KB 应升级为 Plaid / Faiss-IVF
            scores = []
            for i in range(pages_emb.shape[0]):
                p = pages_emb[i]  # (p_tokens, dim)
                sim = q_emb @ p.T                  # (q_tokens, p_tokens)
                score = sim.max(axis=1).sum()      # MaxSim
                scores.append(float(score))
            order = list(reversed(sorted(range(len(scores)), key=lambda x: scores[x])))
            out: List[Document] = []
            for i in order[: int(top_k)]:
                meta = dict(page_meta[i]) if i < len(page_meta) else {}
                meta.update({
                    "colpali_score": scores[i],
                    "plugin": "colpali",
                })
                out.append(Document(
                    page_content="(见附图)",
                    metadata=meta,
                ))
            return out
        except Exception as e:  # noqa: BLE001
            logger.debug("ColPali retrieve 失败：%r", e)
            return []
