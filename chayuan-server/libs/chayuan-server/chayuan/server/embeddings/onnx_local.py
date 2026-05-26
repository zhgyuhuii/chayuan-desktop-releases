"""本地 ONNX embedding adapter (单机版默认 embedding 实现)。

功能
----
* CPU 推理,纯 onnxruntime + tokenizers,**无任何 HTTP / 外部服务依赖**
* 支持 BGE / E5 / GTE 系列 ONNX 量化权重(.onnx + tokenizer.json 两文件)
* 实现 langchain ``Embeddings`` 接口 → 直接被 ``faiss_cache`` /
  ``langchain_community.vectorstores.FAISS`` 复用,业务零改动

权重布局约定
------------
``<model_dir>/`` 下必须包含::

    model.onnx         主模型权重(int8 / fp16 量化)
    tokenizer.json     HuggingFace tokenizers 风格,自带 vocab + 规则

可选::

    config.json        模型 hidden_size / max_seq_length 等元信息
    1_Pooling/         Sentence Transformers 风格的池化配置(目前未读)

来源
----
* HuggingFace ``Xenova/bge-m3`` / ``Xenova/multilingual-e5-small`` 等已转好的
  ONNX 仓库,git lfs 拉取或 ``huggingface_hub.snapshot_download``
* 自己用 ``optimum`` 转 PyTorch 模型到 ONNX::

    optimum-cli export onnx --model BAAI/bge-m3 ./bge-m3-onnx --device cpu --opset 14
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

import numpy as np
from langchain_core.embeddings import Embeddings

logger = logging.getLogger("chayuan.embeddings.onnx_local")


class OnnxEmbeddings(Embeddings):
    """ONNX-runtime 本地 embedding。

    每个进程**懒加载**:首次 ``embed_documents/embed_query`` 才 import onnxruntime
    + tokenizers + 实例化 InferenceSession,避免没用 embedding 的代码路径吃这
    几十 MB 的初始化开销。
    """

    def __init__(
        self,
        model_dir: str | os.PathLike[str],
        *,
        max_seq_length: int = 512,
        normalize: bool = True,
        pooling: str = "cls",  # 'cls' / 'mean' / 'last'
    ) -> None:
        self.model_dir = Path(model_dir)
        if not self.model_dir.is_dir():
            raise FileNotFoundError(f"ONNX embedding 目录不存在: {self.model_dir}")
        # 找 onnx 文件:优先 <dir>/model.onnx (扁平结构),没有再扫 <dir>/onnx/ 子目录
        # (HF BAAI/bge-m3 这种整仓结构会把 onnx 文件放在 onnx/ 子目录,根目录只有
        #  tokenizer.json + config.json + safetensors)。按"量化版 → 默认 → fp16"
        # 优先级选,量化版体积小推理快。
        onnx_candidates = [
            self.model_dir / "model.onnx",
            self.model_dir / "onnx" / "model_quantized.onnx",
            self.model_dir / "onnx" / "model_int8.onnx",
            self.model_dir / "onnx" / "model.onnx",
            self.model_dir / "onnx" / "model_fp16.onnx",
        ]
        self.onnx_path = next((p for p in onnx_candidates if p.is_file()), None)
        if self.onnx_path is None:
            raise FileNotFoundError(
                f"{self.model_dir} 下没找到 model.onnx (尝试过: "
                f"{[str(p.relative_to(self.model_dir)) for p in onnx_candidates]});"
                "参考 module docstring 准备权重。"
            )
        if not (self.model_dir / "tokenizer.json").is_file():
            raise FileNotFoundError(
                f"{self.model_dir}/tokenizer.json 缺失;HuggingFace tokenizers 风格。"
            )
        self.max_seq_length = max_seq_length
        self.normalize = normalize
        self.pooling = pooling
        self._session = None
        self._tokenizer = None

    # --------------------------------------------------------------------
    # 懒加载 + 缓存
    # --------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._session is not None and self._tokenizer is not None:
            return
        try:
            import onnxruntime as ort  # type: ignore
            from tokenizers import Tokenizer  # type: ignore
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError(
                "ONNX embedding 需要 onnxruntime + tokenizers 依赖, "
                f"运行 `pip install onnxruntime tokenizers`。底层错误: {e}"
            ) from e

        # CPU provider:单机版默认,不依赖 CUDA
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # 限定线程数避免后台 embedding 抢满 CPU(用户主线程做对话不被卡顿)
        sess_options.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
        sess_options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(self.onnx_path),
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        # 不在 tokenizer 上设 padding/truncation:不同 transformer 模型需求不同,
        # 在 _encode 里按 max_seq_length 显式截断/补齐。
        logger.info(
            "[OnnxEmbeddings] loaded model_dir=%s onnx=%s threads=%d",
            self.model_dir, self.onnx_path.name, sess_options.intra_op_num_threads,
        )

    # --------------------------------------------------------------------
    # 单条 / 批量 embedding (langchain Embeddings 接口)
    # --------------------------------------------------------------------
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed_one(text)

    # --------------------------------------------------------------------
    # 内部:tokenize + ONNX 前向 + pool
    # --------------------------------------------------------------------
    def _embed_one(self, text: str) -> List[float]:
        self._ensure_loaded()
        assert self._session is not None and self._tokenizer is not None  # for mypy

        enc = self._tokenizer.encode(text or "")
        ids = enc.ids[: self.max_seq_length]
        mask = enc.attention_mask[: self.max_seq_length]
        # Pad 到 max_seq_length(部分 ONNX export 要求固定 shape;能动态 shape 的模型也兼容)
        pad_len = self.max_seq_length - len(ids)
        if pad_len > 0:
            ids = ids + [0] * pad_len
            mask = mask + [0] * pad_len

        np_ids = np.array([ids], dtype=np.int64)
        np_mask = np.array([mask], dtype=np.int64)

        feeds: dict = {"input_ids": np_ids, "attention_mask": np_mask}
        # token_type_ids 可选,部分 BGE / sentence-transformers 模型需要
        input_names = {i.name for i in self._session.get_inputs()}
        if "token_type_ids" in input_names:
            feeds["token_type_ids"] = np.zeros_like(np_ids)

        outputs = self._session.run(None, feeds)
        # 第一个输出通常是 last_hidden_state: [batch, seq, hidden]
        last_hidden = outputs[0]
        if last_hidden.ndim != 3:
            # 已经是 pooled 向量(部分 ONNX export 把池化层包进图):直接用
            emb = last_hidden[0]
        else:
            emb = self._pool(last_hidden[0], np_mask[0])

        if self.normalize:
            n = float(np.linalg.norm(emb))
            if n > 1e-9:
                emb = emb / n
        return emb.astype(np.float32).tolist()

    def _pool(self, hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if self.pooling == "cls":
            return hidden[0]  # CLS token (BGE / BERT 风格)
        if self.pooling == "last":
            # 找最后一个非 padding token(GTE / E5 系列偏好)
            last_idx = int(mask.sum()) - 1
            return hidden[max(0, last_idx)]
        # mean pool with attention mask
        masked = hidden * mask[:, None]
        return masked.sum(axis=0) / max(1.0, float(mask.sum()))


# ────────────────────────────────────────────────────────────────────
# 单机版工厂:按 env CHAYUAN_ONNX_EMBED_DIR 实例化(整个进程一份单例,
# 避免每次 get_Embeddings 都重新加载 onnxruntime session)
# ────────────────────────────────────────────────────────────────────

_SINGLETON: Optional[OnnxEmbeddings] = None
_SINGLETON_DIR: Optional[str] = None


def _auto_discover_onnx_embed_dir() -> Optional[Path]:
    """扫 ``<CHAYUAN_ROOT>/models/bundled/embedding/<repo>/`` 找可用 ONNX 仓库。

    条件:
      - ``<repo>/tokenizer.json`` 存在 (HF 整仓标准布局)
      - 且 ``<repo>/model.onnx`` 或 ``<repo>/onnx/{model_quantized,model_int8,model,model_fp16}.onnx``
        任一存在

    第一个命中返目录路径 (指向 ``<repo>/``,具体 ONNX 文件由
    ``OnnxEmbeddings.__init__`` 内部按优先级挑)。

    扫不到返 None。
    """
    try:
        from chayuan.server.model_registry.local_index import models_dir
    except Exception:
        return None
    embed_root = models_dir() / "bundled" / "embedding"
    if not embed_root.is_dir():
        return None
    for sub in sorted(embed_root.iterdir()):
        if not sub.is_dir():
            continue
        if not (sub / "tokenizer.json").is_file():
            continue
        # 跟 OnnxEmbeddings.__init__ 的 onnx_candidates 保持同步
        for rel in (
            "model.onnx",
            "onnx/model_quantized.onnx",
            "onnx/model_int8.onnx",
            "onnx/model.onnx",
            "onnx/model_fp16.onnx",
        ):
            if (sub / rel).is_file():
                return sub
    return None


def try_get_local_onnx_embeddings() -> Optional[OnnxEmbeddings]:
    """单机版默认 embedding 入口。

    探测顺序:
    1. ``CHAYUAN_ONNX_EMBED_DIR`` 环境变量(显式,优先)
    2. 自动扫 ``<CHAYUAN_ROOT>/models/bundled/embedding/<repo>/`` 找带 tokenizer.json
       + model.onnx (含 onnx/ 子目录) 的目录
    3. (将来)``<sys._MEIPASS>/onnx_embed_default/`` PyInstaller 内置权重路径

    任一命中且权重完整 → 返单例。全部不命中 → 返 ``None``,调用方走原路由
    (ollama / openai / localai 等 HTTP embedding)。
    """
    global _SINGLETON, _SINGLETON_DIR

    # 1. env 显式覆盖
    env_dir = os.environ.get("CHAYUAN_ONNX_EMBED_DIR", "").strip()
    if env_dir:
        if _SINGLETON is not None and _SINGLETON_DIR == env_dir:
            return _SINGLETON
        try:
            inst = OnnxEmbeddings(env_dir)
            _SINGLETON = inst
            _SINGLETON_DIR = env_dir
            return inst
        except FileNotFoundError as e:
            logger.warning("[OnnxEmbeddings] CHAYUAN_ONNX_EMBED_DIR=%s 不可用: %s", env_dir, e)
            # env 明确指了路径但不可用 → 不再 fallback 到 auto-discover,直接返 None
            # (用户意图明确,不要悄悄换路径让人摸不着)
            return None
        except Exception as e:  # noqa: BLE001
            logger.exception("[OnnxEmbeddings] env-dir 实例化失败: %r", e)
            return None

    # 2. 自动扫描 bundled/embedding/<repo>/
    auto_dir = _auto_discover_onnx_embed_dir()
    if auto_dir is None:
        return None
    auto_key = str(auto_dir)
    if _SINGLETON is not None and _SINGLETON_DIR == auto_key:
        return _SINGLETON
    try:
        inst = OnnxEmbeddings(auto_dir)
        _SINGLETON = inst
        _SINGLETON_DIR = auto_key
        logger.info("[OnnxEmbeddings] auto-discovered embedding dir: %s", auto_dir)
        return inst
    except FileNotFoundError as e:
        logger.warning("[OnnxEmbeddings] auto-discover 命中 %s 但权重不完整: %s", auto_dir, e)
        return None
    except Exception as e:  # noqa: BLE001
        logger.exception("[OnnxEmbeddings] auto-discover 实例化失败: %r", e)
        return None
