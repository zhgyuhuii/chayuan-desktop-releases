"""Hybrid RAG / Rerank / 邻居 chunk 扩展 行为测试（P0-1）。

不依赖真实 embedding + FAISS；用伪 KBService + 小规模 Document 列表
验证检索流程的行为。
"""
from __future__ import annotations

import pytest
from langchain_core.documents import Document


class FakeKBService:
    """最小化 KBService，只实现 hybrid_service 需要的接口。"""

    def __init__(self, docs):
        self.kb_name = "test_kb"
        self._docs = docs
        # 假 vectorstore：docstore._dict 给 BM25 取全量；as_retriever 不重要
        self.vs = _FakeVS(docs)

    def do_search(self, query, top_k, score_threshold):
        # 假向量检索：包含 query 文本的 chunk 优先
        hits = [d for d in self._docs if query in d.page_content]
        miss = [d for d in self._docs if query not in d.page_content]
        return (hits + miss)[:top_k]

    def check_embed_model(self):
        return True, "ok"


class _FakeDocstore:
    def __init__(self, docs):
        self._dict = {str(i): d for i, d in enumerate(docs)}


class _FakeVS:
    def __init__(self, docs):
        self.docstore = _FakeDocstore(docs)

    def as_retriever(self, **kwargs):
        # 返回一个退化 retriever：直接返回前 k 条
        k = (kwargs.get("search_kwargs") or {}).get("k") or 5

        class _R:
            def __init__(self, docs, k):
                self.docs = docs
                self.k = k

            def invoke(self, q, **_):
                return [d for d in self.docs if q in d.page_content][: self.k]

            def get_relevant_documents(self, q):
                return self.invoke(q)

        return _R(list(self.docstore._dict.values()), k)

    def similarity_search(self, q, k=10):
        return list(self.docstore._dict.values())[:k]


def _make_docs():
    return [
        Document(page_content="iPhone 15 定价策略与销量分析", metadata={"source": "a.md", "id": "1", "chunk_index": 0}),
        Document(page_content="MacBook Pro 市场表现", metadata={"source": "b.md", "id": "2", "chunk_index": 0}),
        Document(page_content="AirPods 渠道布局", metadata={"source": "c.md", "id": "3", "chunk_index": 0}),
        # 给 a.md 多一个相邻 chunk
        Document(page_content="iPhone 15 生产成本拆解", metadata={"source": "a.md", "id": "4", "chunk_index": 1}),
    ]


# ---------------------------------------------------------------------------
# 开关全关 → 直接透传 do_search
# ---------------------------------------------------------------------------

def test_all_off_is_passthrough(monkeypatch):
    from chayuan.settings import Settings
    monkeypatch.setattr(Settings.kb_settings, "USE_HYBRID_RETRIEVER", False, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "USE_RERANKER", False, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "USE_CONTEXT_EXPANSION", False, raising=False)

    from chayuan.server.file_rag.hybrid_service import hybrid_search_docs
    kb = FakeKBService(_make_docs())
    out = hybrid_search_docs(kb, "iPhone", top_k=2, score_threshold=1.0)
    assert len(out) == 2
    assert all("iPhone" in d.page_content for d in out)


# ---------------------------------------------------------------------------
# Hybrid 启用：BM25 融合候选池
# ---------------------------------------------------------------------------

def test_hybrid_merges_candidates(monkeypatch):
    from chayuan.settings import Settings
    monkeypatch.setattr(Settings.kb_settings, "USE_HYBRID_RETRIEVER", True, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "USE_RERANKER", False, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "USE_CONTEXT_EXPANSION", False, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "HYBRID_BM25_WEIGHT", 0.5, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "HYBRID_CANDIDATE_TOP_K", 10, raising=False)

    # 清 BM25 cache
    from chayuan.server.file_rag.hybrid_service import (
        hybrid_search_docs, invalidate_bm25_cache,
    )
    invalidate_bm25_cache()

    kb = FakeKBService(_make_docs())
    out = hybrid_search_docs(kb, "iPhone", top_k=3, score_threshold=1.0)
    assert 1 <= len(out) <= 3


# ---------------------------------------------------------------------------
# Rerank：未装 sentence_transformers → 透传降级
# ---------------------------------------------------------------------------

def test_rerank_fallback_without_sentence_transformers(monkeypatch):
    """sentence_transformers 未装 → _get_reranker 返回 None → 保留候选顺序。"""
    from chayuan.settings import Settings
    monkeypatch.setattr(Settings.kb_settings, "USE_HYBRID_RETRIEVER", False, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "USE_RERANKER", True, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "USE_CONTEXT_EXPANSION", False, raising=False)

    import chayuan.server.file_rag.hybrid_service as H
    # 强制 reranker 不可用（即使本机装了也跳过）
    monkeypatch.setattr(H, "_get_reranker", lambda: None)
    monkeypatch.setattr(H, "_RERANKER_CACHE", {}, raising=False)

    kb = FakeKBService(_make_docs())
    out = H.hybrid_search_docs(kb, "iPhone", top_k=2, score_threshold=1.0)
    assert len(out) == 2
    # 没有 rerank_score（因为真的没 rerank）
    for d in out:
        assert "rerank_score" not in (d.metadata or {})


# ---------------------------------------------------------------------------
# 邻居扩展：命中 a.md/chunk_index=0 后，应把 chunk_index=1 也合并进 page_content
# ---------------------------------------------------------------------------

def test_context_expansion_merges_neighbors(monkeypatch):
    from chayuan.settings import Settings
    monkeypatch.setattr(Settings.kb_settings, "USE_HYBRID_RETRIEVER", False, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "USE_RERANKER", False, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "USE_CONTEXT_EXPANSION", True, raising=False)
    monkeypatch.setattr(Settings.kb_settings, "CONTEXT_EXPANSION_NEIGHBORS", 1, raising=False)

    from chayuan.server.file_rag.hybrid_service import hybrid_search_docs
    kb = FakeKBService(_make_docs())
    out = hybrid_search_docs(kb, "iPhone", top_k=1, score_threshold=1.0)
    assert len(out) == 1
    merged = out[0].page_content
    # 命中 chunk + 邻居 chunk 同属 a.md 应当被合并
    assert "定价策略" in merged and "生产成本" in merged
    assert out[0].metadata.get("expanded") is True


# ---------------------------------------------------------------------------
# 失效：add_doc / delete_doc 后 BM25 缓存应 invalid
# ---------------------------------------------------------------------------

def test_invalidate_bm25_cache():
    from chayuan.server.file_rag.hybrid_service import (
        _BM25_CACHE, invalidate_bm25_cache,
    )
    _BM25_CACHE["kb1"] = ("stub", 1)
    _BM25_CACHE["kb2"] = ("stub", 1)
    invalidate_bm25_cache("kb1")
    assert "kb1" not in _BM25_CACHE
    assert "kb2" in _BM25_CACHE
    invalidate_bm25_cache()  # 全清
    assert not _BM25_CACHE
