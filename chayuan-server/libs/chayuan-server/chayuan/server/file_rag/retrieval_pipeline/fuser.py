"""RRF(Reciprocal Rank Fusion)融合 + 去重 + sandwich 重排。

为什么要做这件事:
- 当上游有多组候选(改写 query × KB / vector × BM25)时,各自的 score
  不在同一空间(cosine vs IP vs BM25 BM25 score 又是无界);**直接合并 sort
  by score 没有任何统计意义**。
- RRF 用 rank 而非 score 做融合,是 LangChain / Haystack / LlamaIndex 三家
  生产 RAG 的统一默认方案,k=60 是论文里的零参数配置(Cormack 2009)。
- sandwich:把高分文档放在序列首尾,中间放低分,利用 LLM 的 primacy + recency
  注意力效应。Anthropic / Microsoft RAG cookbook 共识。

所有函数都是纯函数:
- 不读 settings,不依赖任何全局状态
- 不修改入参(返回新 list,Document 内部 metadata 也是浅拷贝后再写)
- 线程安全
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from langchain_core.documents import Document


# ---------------------------------------------------------------------------
# Chunk 唯一键 — 跨 KB / 跨改写 query 去重的依据
# ---------------------------------------------------------------------------

def chunk_key(doc) -> str:
    """生成 chunk 的稳定唯一键。

    优先级:
        metadata.id (Milvus pk / FAISS docid / 等) → 跨调用稳定
        (kb_name, file_name, chunk_index) 三元组   → 跨后端可复算
        page_content 前 80 字哈希                  → 兜底,保证总能去重
    """
    meta = (doc.metadata or {}) if hasattr(doc, "metadata") else (doc.get("metadata") if isinstance(doc, dict) else {}) or {}
    raw_id = meta.get("id") or meta.get("doc_id") or meta.get("pk") or meta.get("vs_id")
    if raw_id not in (None, ""):
        return f"id:{raw_id}"
    kb_name = meta.get("kb_name") or ""
    file_name = meta.get("file_name") or meta.get("filename") or meta.get("source") or ""
    chunk_idx = meta.get("chunk_index") or meta.get("chunk_idx") or meta.get("chunk_id") or ""
    if file_name:
        return f"f:{kb_name}|{file_name}|{chunk_idx}"
    content = ""
    if hasattr(doc, "page_content"):
        content = doc.page_content or ""
    elif isinstance(doc, dict):
        content = doc.get("page_content") or doc.get("content") or ""
    # 用 80 字签名做兜底键 — 对于完全无 metadata 的退化 KB 仍能去重
    return f"c:{hash(content[:80])}"


def dedup_by_chunk_key(docs: Iterable) -> List:
    """按 chunk_key 去重,保留首次出现位置的文档(rank 最高的版本)。"""
    seen: set = set()
    out: List = []
    for d in docs:
        k = chunk_key(d)
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# RRF 融合
# ---------------------------------------------------------------------------

DEFAULT_RRF_K = 60  # Cormack 2009 论文默认值;社区 / LangChain / Haystack 一致


def rrf_fuse(
    ranked_lists: Sequence[Sequence],
    *,
    weights: Optional[Sequence[float]] = None,
    k: int = DEFAULT_RRF_K,
    top_n: Optional[int] = None,
) -> List:
    """Reciprocal Rank Fusion 把多个有序候选列表融合成一个。

    打分公式(每篇文档累加):
        rrf_score(d) = Σ_i weights[i] / (k + rank_i(d))

    rank_i 从 1 开始,k=60 是工业默认(对 rank 1 给 ~0.0164,rank 60 给 ~0.0083,
    平滑衰减,避免对 top-1 过度信任)。weights 默认全 1。

    入参:
        ranked_lists: 多个有序候选列表(每个是按相关性降序的 Document 列表)
        weights: 每个列表的权重(等长于 ranked_lists);None 视为全 1
        k: RRF 平滑参数,默认 60
        top_n: 截断输出长度;None 表示返回全部

    返回:按融合分数降序的 Document 列表(已去重)。
    每个 Document 的 metadata.fused_score 写入融合得分,方便上游观测。
    """
    if not ranked_lists:
        return []
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError(
            f"weights 长度 {len(weights)} 与 ranked_lists 长度 {len(ranked_lists)} 不一致"
        )

    fused_scores: Dict[str, float] = {}
    representative: Dict[str, Any] = {}  # key → 第一个见到的 Document 实例
    for i, lst in enumerate(ranked_lists):
        w = float(weights[i])
        for rank, doc in enumerate(lst or []):
            key = chunk_key(doc)
            fused_scores[key] = fused_scores.get(key, 0.0) + w / (k + rank + 1)
            if key not in representative:
                representative[key] = doc

    # 按融合分数降序;score tie 时 stable(Python 3.7+ dict 顺序 = 插入顺序)
    sorted_keys = sorted(
        fused_scores.keys(), key=lambda kk: fused_scores[kk], reverse=True
    )
    out: List = []
    for k_ in sorted_keys:
        d = representative[k_]
        # 把融合分数写到 metadata,UI / observability 拿得到
        try:
            if hasattr(d, "metadata"):
                if d.metadata is None:
                    d.metadata = {}
                d.metadata["fused_score"] = float(fused_scores[k_])
            elif isinstance(d, dict):
                d.setdefault("metadata", {})["fused_score"] = float(fused_scores[k_])
        except Exception:  # noqa: BLE001
            pass
        out.append(d)
        if top_n is not None and len(out) >= top_n:
            break
    return out


# ---------------------------------------------------------------------------
# Sandwich 重排
# ---------------------------------------------------------------------------

def sandwich_reorder(docs: Sequence) -> List:
    """Sandwich 排序:最高分放首位 + 末位,低分塞中间。

    结合 LLM 的 primacy + recency 注意力效应:
        N=5  → [1, 3, 5, 4, 2]
        N=4  → [1, 3, 4, 2]
        N=10 → [1, 3, 5, 7, 9, 10, 8, 6, 4, 2]

    输入假设已按相关性降序排列(rank 1 = 最相关)。
    """
    n = len(docs)
    if n <= 2:
        return list(docs)
    odds = list(range(0, n, 2))    # 奇数排名(0-indexed):0, 2, 4, ...
    evens = list(range(1, n, 2))   # 偶数排名:1, 3, 5, ...
    # 头放 odds 升序(最高分 doc[0] 在最前),尾放 evens 倒序(次高 doc[1] 在最后)
    order = odds + list(reversed(evens))
    return [docs[i] for i in order]
