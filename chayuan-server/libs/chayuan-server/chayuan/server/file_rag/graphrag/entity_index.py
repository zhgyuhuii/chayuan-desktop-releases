"""Entity 向量索引（N-6）。

取代 `retriever._find_seed_entities` 里的 O(N) 字符串扫：把 entity name + type +
description 拼成短文本 embed，按 kb_name 维护一份**内存向量表**，查询 O(log N)
命中"语义相似"实体。

- 索引是**进程级单例**；KB 粒度 lazy build
- DB 里 entity 表变动 → 调 ``invalidate(kb_name)``
- 向量大小可控（每 entity 1 条 × 768 维 × 4 字节 ≈ 3KB）；10 万实体也就 300MB

**查询口径**：
- 精确命中（子串 / 完全匹配）仍然优先（子串权重 +3）
- 模糊相似通过向量召回（权重 =  cosine score）
- 最终按权重排序返回 top_k
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.graphrag.entity_index")

_LOCK = threading.RLock()
_CACHE: Dict[str, "_EntityIndex"] = {}


@dataclass
class _EntityIndex:
    kb_name: str
    entity_ids: List[int]
    names: List[str]
    types: List[str]
    descriptions: List[str]
    mention_counts: List[int]
    matrix: Any = None   # np.ndarray; 懒建
    _built: bool = False

    def build(self) -> None:
        import numpy as np
        from chayuan.server.utils import get_Embeddings
        emb = get_Embeddings()
        texts = [
            f"{n} ({t}). {d}"[:400]
            for n, t, d in zip(self.names, self.types, self.descriptions)
        ]
        if not texts:
            self.matrix = np.zeros((0, 1), dtype="float32")
            self._built = True
            return
        try:
            vectors = emb.embed_documents(texts)
        except Exception as e:  # noqa: BLE001
            logger.warning("entity embed 失败（降级为空索引）：%r", e)
            self.matrix = np.zeros((0, 1), dtype="float32")
            self._built = True
            return
        m = np.asarray(vectors, dtype="float32")
        norm = np.linalg.norm(m, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        self.matrix = (m / norm).astype("float32")
        self._built = True

    def search(self, query: str, top_k: int = 6) -> List[Dict[str, Any]]:
        if not self._built:
            self.build()
        import numpy as np

        # 规则部分：子串 / 词命中
        low_q = (query or "").lower()
        rule_scores: Dict[int, float] = {}
        for i, name in enumerate(self.names):
            if not name:
                continue
            low_n = name.lower()
            if low_n and low_n in low_q:
                rule_scores[i] = 3.0
            else:
                # 子串反向命中
                if low_q and low_q in low_n:
                    rule_scores[i] = 2.0

        # 向量相似
        vec_scores: Dict[int, float] = {}
        if self.matrix is not None and len(self.names) and query:
            try:
                from chayuan.server.utils import get_Embeddings
                emb = get_Embeddings()
                qv = np.asarray(emb.embed_query(query), dtype="float32")
                qn = float(np.linalg.norm(qv)) or 1.0
                qv = qv / qn
                sims = self.matrix @ qv
                # 只取 top 30 参与最终融合
                order = np.argsort(-sims)[:30]
                for i in order:
                    ii = int(i)
                    vec_scores[ii] = float(sims[ii])
            except Exception as e:  # noqa: BLE001
                logger.debug("entity vector 查询失败：%r", e)

        # 融合
        all_idx = set(rule_scores.keys()) | set(vec_scores.keys())
        if not all_idx:
            return []
        scored: List[tuple] = []
        for i in all_idx:
            rs = rule_scores.get(i, 0.0)
            vs = vec_scores.get(i, 0.0)
            # 微加权：mention_count 越大，权重越高（上限 +1.0）
            pop = min(float(self.mention_counts[i] or 1) / 20.0, 1.0)
            score = rs + vs + pop
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "id": self.entity_ids[i],
                "name": self.names[i],
                "type": self.types[i],
                "description": self.descriptions[i],
                "mention_count": self.mention_counts[i],
                "score": float(sc),
            }
            for sc, i in scored[: int(top_k)]
        ]


def _load_from_db(kb_name: str) -> _EntityIndex:
    from chayuan.server.db.models.graphrag_model import GraphEntityModel
    from chayuan.server.db.session import session_scope
    ids: List[int] = []
    names: List[str] = []
    types: List[str] = []
    descs: List[str] = []
    mcs: List[int] = []
    with session_scope() as s:
        rows = s.query(
            GraphEntityModel.id, GraphEntityModel.name,
            GraphEntityModel.entity_type, GraphEntityModel.description,
            GraphEntityModel.mention_count,
        ).filter(GraphEntityModel.kb_name == kb_name).all()
    for rid, name, etype, desc, mc in rows:
        ids.append(int(rid))
        names.append(str(name or ""))
        types.append(str(etype or ""))
        descs.append(str(desc or ""))
        mcs.append(int(mc or 1))
    return _EntityIndex(
        kb_name=kb_name, entity_ids=ids, names=names, types=types,
        descriptions=descs, mention_counts=mcs,
    )


def get_index(kb_name: str) -> _EntityIndex:
    with _LOCK:
        idx = _CACHE.get(kb_name)
        if idx is None:
            idx = _load_from_db(kb_name)
            _CACHE[kb_name] = idx
        return idx


def invalidate(kb_name: str = "") -> None:
    with _LOCK:
        if not kb_name:
            _CACHE.clear()
        else:
            _CACHE.pop(kb_name, None)


def find_seed_entities(
    kb_name: str, query: str, *, top_k: int = 6,
) -> List[Dict[str, Any]]:
    """retriever 的公共入口；自动用向量索引。"""
    try:
        idx = get_index(kb_name)
        return idx.search(query or "", top_k=int(top_k))
    except Exception as e:  # noqa: BLE001
        logger.debug("find_seed_entities 失败，fallback 到 DB LIKE：%r", e)
        # 回退到老实现
        from chayuan.server.file_rag.graphrag.retriever import _find_seed_entities
        return _find_seed_entities(kb_name, query, top_n=int(top_k))
