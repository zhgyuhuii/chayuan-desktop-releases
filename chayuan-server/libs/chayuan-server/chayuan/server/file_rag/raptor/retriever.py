"""RAPTOR 检索适配层。

因为我们把摘要也存进了 kb 的**同一**向量库，所以原 do_search / HybridRetriever
**无需修改**就能召回摘要。本文件提供：

- ``raptor_docs_metadata(doc)``：从 metadata 判定是否 RAPTOR 生成的摘要
- ``balance_raptor_levels(docs, per_level_quota)``：重排候选，让每层摘要都有配额
  避免"全部都是 level=0 原文"或"全部都是 level=3 粗粒度摘要"

在 `hybrid_service.hybrid_search_docs` 里可选接入：配置 USE_RAPTOR=True 时
做一次 level 平衡后再送 rerank。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from langchain_core.documents import Document


def raptor_docs_metadata(doc: Document) -> Dict[str, Optional[int]]:
    meta = doc.metadata or {}
    return {
        "level": int(meta.get("raptor_level") or 0),
        "cluster": meta.get("raptor_cluster_id"),
        "kb": meta.get("raptor_kb"),
    }


def balance_raptor_levels(
    docs: List[Document],
    *,
    top_k: int,
    per_level_ratio: Optional[Dict[int, float]] = None,
) -> List[Document]:
    """按 level 做配额重排。

    默认配额：level=0（原文）占 60%，level=1 占 25%，level>=2 占 15%。
    每层内部保持原顺序（假定已按相关性降序）。
    """
    if not docs:
        return []
    ratio = per_level_ratio or {0: 0.6, 1: 0.25, 2: 0.15}
    # 分桶
    buckets: Dict[int, List[Document]] = {}
    for d in docs:
        lvl = int((d.metadata or {}).get("raptor_level") or 0)
        lvl = min(lvl, 2)  # 把 >=2 合并
        buckets.setdefault(lvl, []).append(d)

    quotas: Dict[int, int] = {}
    assigned = 0
    for lvl, r in ratio.items():
        q = max(1, int(round(top_k * r)))
        quotas[lvl] = q
        assigned += q
    # 余量往 level 0 补
    quotas[0] = quotas.get(0, 0) + max(0, top_k - assigned)

    out: List[Document] = []
    for lvl, q in quotas.items():
        out.extend((buckets.get(lvl) or [])[:q])
    # 若某层不足，从其它层补到 top_k
    if len(out) < top_k:
        remaining_ids = {id(d) for d in out}
        for d in docs:
            if id(d) in remaining_ids:
                continue
            out.append(d)
            if len(out) >= top_k:
                break
    return out[:top_k]
