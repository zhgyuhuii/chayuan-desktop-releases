"""ONNX 本地 cross-encoder reranker。

跟 ``chayuan.server.embeddings.onnx_local`` 同思路:
- 纯 ``onnxruntime`` + ``tokenizers`` in-process,不依赖 sidecar / HTTP
- 自动扫 ``<CHAYUAN_ROOT>/models/bundled/rerank/<repo>/`` 找权重
- 跳过 sentence_transformers (lite spec excludes 它,~200 MB 死重量)

为什么独立模块:
``chayuan.server.reranker.reranker`` 顶层 ``from sentence_transformers import CrossEncoder``,
lite 版会触发 ImportError,所以 ONNX 路径必须放在不 import sentence_transformers
的独立模块里,kb_chat 才能优先 try ONNX 而不被 ImportError 卡死。

目录布局 (跟 OnnxEmbeddings 同):

::

    <repo>/tokenizer.json         HuggingFace tokenizers
    <repo>/model.onnx                                      或
    <repo>/onnx/model_quantized.onnx                       或
    <repo>/onnx/model.onnx                                 ...按优先级

ONNX 模型契约:
- 输入: ``input_ids`` + ``attention_mask`` (+ 可选 ``token_type_ids``)
- 输出: logits, shape ``(batch, 1)`` 或 ``(batch,)`` 或 ``(batch, 2)``
  - (batch, 1) / (batch,):直接当 score
  - (batch, 2):softmax 后取 index 1 (正类) 当 score
  - 其它 shape:抛 RuntimeError 提示用户

bge-reranker-v2-m3 / bge-reranker-large-int8 等主流 ONNX rerank 仓库都满足。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, List, Optional, Sequence

from langchain_core.callbacks.manager import Callbacks
from langchain_core.documents import Document
from langchain_core.retrievers.document_compressors.base import BaseDocumentCompressor
from pydantic import Field, PrivateAttr

logger = logging.getLogger("chayuan.reranker.onnx_local")


# 跟 embedding 那边 onnx_candidates 保持同步;改一边记得对齐。
_ONNX_CANDIDATE_RELS = (
    "model.onnx",
    "onnx/model_quantized.onnx",
    "onnx/model_int8.onnx",
    "onnx/model.onnx",
    "onnx/model_fp16.onnx",
)


def _pick_onnx_path(model_dir: Path) -> Optional[Path]:
    for rel in _ONNX_CANDIDATE_RELS:
        p = model_dir / rel
        if p.is_file():
            return p
    return None


class OnnxReranker(BaseDocumentCompressor):
    """ONNX-runtime cross-encoder reranker。

    用法跟 ``LangchainReranker`` 兼容(``compress_documents(documents, query)``),
    可以无缝替换。
    """

    model_dir_str: str = Field()
    top_n: int = Field(default=3)
    max_length: int = Field(default=512)
    batch_size: int = Field(default=32)
    _session: Any = PrivateAttr(default=None)
    _tokenizer: Any = PrivateAttr(default=None)
    _onnx_path_str: str = PrivateAttr(default="")

    def __init__(
        self,
        model_dir: str | os.PathLike[str],
        *,
        top_n: int = 3,
        max_length: int = 512,
        batch_size: int = 32,
    ) -> None:
        md = Path(model_dir)
        if not md.is_dir():
            raise FileNotFoundError(f"ONNX rerank 目录不存在: {md}")
        onnx_path = _pick_onnx_path(md)
        if onnx_path is None:
            raise FileNotFoundError(
                f"{md} 下没找到 model.onnx (尝试过: {list(_ONNX_CANDIDATE_RELS)})"
            )
        if not (md / "tokenizer.json").is_file():
            raise FileNotFoundError(
                f"{md}/tokenizer.json 缺失;HuggingFace tokenizers 风格。"
            )
        super().__init__(
            model_dir_str=str(md),
            top_n=top_n,
            max_length=max_length,
            batch_size=batch_size,
        )
        self._onnx_path_str = str(onnx_path)

    @property
    def model_dir(self) -> Path:
        return Path(self.model_dir_str)

    @property
    def onnx_path(self) -> Path:
        return Path(self._onnx_path_str)

    # ------------------------------------------------------------------
    # 懒加载 — 首次 compress 调用才 import onnxruntime + 实例化 session
    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._session is not None and self._tokenizer is not None:
            return
        try:
            import onnxruntime as ort  # type: ignore
            from tokenizers import Tokenizer  # type: ignore
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError(
                "ONNX rerank 需要 onnxruntime + tokenizers 依赖, "
                f"`pip install onnxruntime tokenizers`。底层错误: {e}"
            ) from e

        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
        sess_options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            self._onnx_path_str,
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        logger.info(
            "[OnnxReranker] loaded model_dir=%s onnx=%s threads=%d",
            self.model_dir, self.onnx_path.name, sess_options.intra_op_num_threads,
        )

    # ------------------------------------------------------------------
    # langchain BaseDocumentCompressor 接口
    # ------------------------------------------------------------------
    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        if len(documents) == 0:
            return []
        self._ensure_loaded()
        doc_list = list(documents)
        texts = [d.page_content for d in doc_list]

        import numpy as np  # 推迟 import 不抢 cold start

        scores: List[float] = []
        sess_input_names = {inp.name for inp in self._session.get_inputs()}

        for i in range(0, len(texts), self.batch_size):
            batch_texts = texts[i : i + self.batch_size]
            batch_pairs = [(query, t) for t in batch_texts]
            inputs = self._tokenize_pairs(batch_pairs, sess_input_names)
            outputs = self._session.run(None, inputs)
            logits = np.asarray(outputs[0])
            # 归一化到 1D scores
            if logits.ndim == 2 and logits.shape[1] == 1:
                arr = logits.squeeze(-1)
            elif logits.ndim == 1:
                arr = logits
            elif logits.ndim == 2 and logits.shape[1] == 2:
                # softmax,取正类
                e = np.exp(logits - logits.max(axis=1, keepdims=True))
                arr = (e[:, 1] / e.sum(axis=1))
            else:
                raise RuntimeError(
                    f"OnnxReranker 不识别的 logits shape={logits.shape};"
                    "支持 (batch,1) / (batch,) / (batch,2),其他需要业务适配。"
                )
            scores.extend(float(x) for x in arr.tolist())

        # topk
        top_k = min(self.top_n, len(scores))
        idx_sorted = sorted(range(len(scores)), key=lambda j: scores[j], reverse=True)
        idx_sorted = idx_sorted[:top_k]

        result: List[Document] = []
        for idx in idx_sorted:
            doc = doc_list[idx]
            doc.metadata = dict(doc.metadata or {})
            doc.metadata["relevance_score"] = scores[idx]
            result.append(doc)
        return result

    # ------------------------------------------------------------------
    # tokenize pairs — Tokenizer.encode_batch 接受 (str, str) tuple list
    # ------------------------------------------------------------------
    def _tokenize_pairs(
        self,
        pairs: List[tuple],
        sess_input_names: set,
    ) -> dict:
        import numpy as np

        encodings = self._tokenizer.encode_batch(pairs, add_special_tokens=True)
        # truncate 到 max_length
        for enc in encodings:
            if len(enc.ids) > self.max_length:
                enc.truncate(self.max_length)
        max_len = max((len(enc.ids) for enc in encodings), default=0)
        max_len = max(max_len, 1)
        n = len(encodings)
        input_ids = np.zeros((n, max_len), dtype=np.int64)
        attention_mask = np.zeros((n, max_len), dtype=np.int64)
        token_type_ids = np.zeros((n, max_len), dtype=np.int64)
        for i, enc in enumerate(encodings):
            L = min(len(enc.ids), max_len)
            input_ids[i, :L] = enc.ids[:L]
            attention_mask[i, :L] = enc.attention_mask[:L]
            if hasattr(enc, "type_ids") and enc.type_ids:
                token_type_ids[i, :L] = enc.type_ids[:L]
        inputs = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in sess_input_names:
            inputs["token_type_ids"] = token_type_ids
        return inputs


# --------------------------------------------------------------------------
# 自动 discover — 跟 OnnxEmbeddings 那边逻辑同
# --------------------------------------------------------------------------
def _auto_discover_onnx_rerank_dir() -> Optional[Path]:
    try:
        from chayuan.server.model_registry.local_index import models_dir
    except Exception:
        return None
    rerank_root = models_dir() / "bundled" / "rerank"
    if not rerank_root.is_dir():
        return None
    for sub in sorted(rerank_root.iterdir()):
        if not sub.is_dir():
            continue
        if not (sub / "tokenizer.json").is_file():
            continue
        if _pick_onnx_path(sub) is not None:
            return sub
    return None


_SINGLETON: Optional[OnnxReranker] = None
_SINGLETON_DIR: Optional[str] = None


def try_get_local_onnx_reranker(
    *,
    top_n: int = 3,
    max_length: int = 512,
) -> Optional[OnnxReranker]:
    """单机版本地 ONNX rerank 入口。

    探测顺序:
    1. ``CHAYUAN_ONNX_RERANK_DIR`` env (显式,优先)
    2. 自动扫 ``<CHAYUAN_ROOT>/models/bundled/rerank/<repo>/``

    任一命中返单例。全不命中返 ``None``,kb_chat 走 LangchainReranker fallback
    (full 版有 sentence_transformers 的话能跑;lite 版 ImportError 被外层
    try/except 吞,用原 docs)。
    """
    global _SINGLETON, _SINGLETON_DIR

    env_dir = os.environ.get("CHAYUAN_ONNX_RERANK_DIR", "").strip()
    if env_dir:
        if _SINGLETON is not None and _SINGLETON_DIR == env_dir:
            # top_n 可能跟上次不同,但单例复用没必要每次重建;调用方可在外面覆盖 top_n
            return _SINGLETON
        try:
            inst = OnnxReranker(env_dir, top_n=top_n, max_length=max_length)
            _SINGLETON = inst
            _SINGLETON_DIR = env_dir
            return inst
        except FileNotFoundError as e:
            logger.warning(
                "[OnnxReranker] CHAYUAN_ONNX_RERANK_DIR=%s 不可用: %s",
                env_dir, e,
            )
            return None
        except Exception as e:  # noqa: BLE001
            logger.exception("[OnnxReranker] env-dir 实例化失败: %r", e)
            return None

    auto_dir = _auto_discover_onnx_rerank_dir()
    if auto_dir is None:
        return None
    auto_key = str(auto_dir)
    if _SINGLETON is not None and _SINGLETON_DIR == auto_key:
        return _SINGLETON
    try:
        inst = OnnxReranker(auto_dir, top_n=top_n, max_length=max_length)
        _SINGLETON = inst
        _SINGLETON_DIR = auto_key
        logger.info("[OnnxReranker] auto-discovered rerank dir: %s", auto_dir)
        return inst
    except FileNotFoundError as e:
        logger.warning(
            "[OnnxReranker] auto-discover 命中 %s 但权重不完整: %s",
            auto_dir, e,
        )
        return None
    except Exception as e:  # noqa: BLE001
        logger.exception("[OnnxReranker] auto-discover 实例化失败: %r", e)
        return None


__all__ = [
    "OnnxReranker",
    "try_get_local_onnx_reranker",
]
