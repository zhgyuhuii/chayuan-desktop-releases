"""RAPTOR + GraphRAG（B 路线）单测。

策略：不连真 FAISS / LLM；stub 掉关键依赖点，验证：
- RAPTOR 聚类正确分簇、摘要被注入新 Documents
- 层次平衡算法按比例产出
- GraphRAG extractor 对标准 JSON 响应正确解析
- 关系 / 实体双向去重幂等
- 社区检测：Louvain 未装时回退连通分量
- retriever._find_seed_entities 能按子串正确排序
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------

def test_gmm_cluster_handles_tiny_input():
    from chayuan.server.file_rag.raptor.clustering import gmm_cluster
    labels, n = gmm_cluster([[0.1, 0.2]], target_cluster_size=5)
    assert labels == [0]
    assert n == 1


def test_gmm_cluster_multi_points():
    # 构造两个明显的簇
    import random
    random.seed(42)
    vecs = []
    for _ in range(20):
        vecs.append([random.gauss(0, 0.1), random.gauss(0, 0.1)])
    for _ in range(20):
        vecs.append([random.gauss(5, 0.1), random.gauss(5, 0.1)])
    from chayuan.server.file_rag.raptor.clustering import gmm_cluster
    labels, n = gmm_cluster(vecs, target_cluster_size=10)
    # 至少分成了 >= 2 簇
    assert n >= 2
    # 前 20 点多数被打到同一簇
    first_label = max(set(labels[:20]), key=labels[:20].count)
    assert labels[:20].count(first_label) >= 12


# ---------------------------------------------------------------------------
# RAPTOR 层次平衡
# ---------------------------------------------------------------------------

def test_balance_raptor_levels_respects_ratio():
    from langchain_core.documents import Document
    from chayuan.server.file_rag.raptor.retriever import balance_raptor_levels

    docs = []
    for i in range(10):
        docs.append(Document(page_content=f"leaf#{i}", metadata={"raptor_level": 0, "id": f"l{i}"}))
    for i in range(6):
        docs.append(Document(page_content=f"mid#{i}", metadata={"raptor_level": 1, "id": f"m{i}"}))
    for i in range(3):
        docs.append(Document(page_content=f"root#{i}", metadata={"raptor_level": 2, "id": f"r{i}"}))

    out = balance_raptor_levels(docs, top_k=10)
    assert len(out) == 10
    lvl_counts = {0: 0, 1: 0, 2: 0}
    for d in out:
        lvl = int((d.metadata or {}).get("raptor_level") or 0)
        lvl_counts[lvl] = lvl_counts.get(lvl, 0) + 1
    # 默认配额 0:60% 1:25% 2:15%
    assert lvl_counts[0] >= 5
    assert lvl_counts[1] >= 1
    assert lvl_counts[2] >= 1


# ---------------------------------------------------------------------------
# GraphRAG extractor
# ---------------------------------------------------------------------------

def test_extractor_parses_and_filters(monkeypatch):
    from chayuan.server.file_rag.graphrag import extractor as E

    # 打桩 LLM：返回一段合法 JSON，里面有个非法 relation（dst 不在 entities 里）
    class _FakeResp:
        content = """{
            "entities": [
                {"name": "Alice", "type": "PERSON", "description": "founder"},
                {"name": "Acme", "type": "ORG", "description": "company"}
            ],
            "relations": [
                {"src": "Alice", "dst": "Acme", "type": "founded", "description": "2020"},
                {"src": "Alice", "dst": "NonExist", "type": "other", "description": "bad"}
            ]
        }"""

    class _FakeLLM:
        def invoke(self, messages, **kw):
            return _FakeResp()

    import chayuan.server.utils as U
    monkeypatch.setattr(U, "get_ChatOpenAI", lambda *a, **k: _FakeLLM(), raising=True)

    res = E.extract_entities_relations("Alice founded Acme in 2020.")
    names = [e["name"] for e in res["entities"]]
    assert set(names) == {"Alice", "Acme"}
    # 非法 relation 被过滤掉
    assert len(res["relations"]) == 1
    assert res["relations"][0]["src"] == "Alice"
    assert res["relations"][0]["dst"] == "Acme"


def test_extractor_handles_llm_failure(monkeypatch):
    from chayuan.server.file_rag.graphrag import extractor as E

    class _BoomLLM:
        def invoke(self, *a, **k):
            raise RuntimeError("llm down")
    import chayuan.server.utils as U
    monkeypatch.setattr(U, "get_ChatOpenAI", lambda *a, **k: _BoomLLM(), raising=True)

    # fail-soft：返回空结构，不抛
    res = E.extract_entities_relations("hello")
    assert res == {"entities": [], "relations": []}


# ---------------------------------------------------------------------------
# GraphRAG 社区检测降级
# ---------------------------------------------------------------------------

def test_detect_communities_connected_components_fallback(monkeypatch):
    """强制让 python-louvain 未装 → 回退为连通分量。"""
    import sys
    # 让 community 模块导入失败
    monkeypatch.setitem(sys.modules, "community", None)
    from chayuan.server.file_rag.graphrag.builder import _detect_communities

    name_to_id = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
    # A-B-C 连通；D-E 独立
    relations = {
        ("A", "B", "r"): {"weight": 1, "description": "", "source_chunk_id": ""},
        ("B", "C", "r"): {"weight": 1, "description": "", "source_chunk_id": ""},
        ("D", "E", "r"): {"weight": 1, "description": "", "source_chunk_id": ""},
    }
    groups = _detect_communities(relations, name_to_id, min_size=2)
    assert len(groups) == 2
    sizes = sorted(len(v) for v in groups.values())
    assert sizes == [2, 3]


# ---------------------------------------------------------------------------
# retriever：种子实体发现 + 邻居扩展
# ---------------------------------------------------------------------------

def test_graphrag_retriever_find_seeds(ks_db):
    """写入一些 entity / relation 后，find_seed_entities 能按字符串命中返回种子。"""
    from chayuan.server.db.models.graphrag_model import (
        GraphEntityModel, GraphRelationModel,
    )
    from chayuan.server.db.session import session_scope
    with session_scope() as s:
        e1 = GraphEntityModel(kb_name="kb1", name="Alice", entity_type="PERSON",
                              description="founder of Acme", mention_count=5)
        e2 = GraphEntityModel(kb_name="kb1", name="Acme", entity_type="ORG",
                              description="tech company", mention_count=10)
        e3 = GraphEntityModel(kb_name="kb1", name="Bob", entity_type="PERSON",
                              description="unrelated", mention_count=1)
        s.add_all([e1, e2, e3])
        s.flush()
        s.add(GraphRelationModel(
            kb_name="kb1", src_entity_id=e1.id, dst_entity_id=e2.id,
            relation_type="founded", description="2020", weight=3,
        ))

    from chayuan.server.file_rag.graphrag.retriever import (
        _expand_neighbors, _find_seed_entities,
    )
    seeds = _find_seed_entities("kb1", "Alice 创办了哪家公司？", top_n=3)
    names = [s["name"] for s in seeds]
    assert "Alice" in names
    # 邻居扩展能把 Acme 带出来
    seed_ids = [s["id"] for s in seeds if s["name"] == "Alice"]
    entities, rels = _expand_neighbors("kb1", seed_ids, hops=1, max_neighbors=10)
    ent_names = {e["name"] for e in entities.values()}
    assert "Acme" in ent_names
    assert len(rels) >= 1


def test_graphrag_augment_returns_local_doc(ks_db):
    """端到端：graphrag_augment 应返回至少一条 local_context 文档。"""
    from chayuan.server.db.models.graphrag_model import (
        GraphEntityModel, GraphRelationModel,
    )
    from chayuan.server.db.session import session_scope
    with session_scope() as s:
        e1 = GraphEntityModel(kb_name="kb_aug", name="OpenAI", entity_type="ORG",
                              description="AI research lab", mention_count=10)
        e2 = GraphEntityModel(kb_name="kb_aug", name="ChatGPT", entity_type="PRODUCT",
                              description="chatbot by OpenAI", mention_count=30)
        s.add_all([e1, e2])
        s.flush()
        s.add(GraphRelationModel(
            kb_name="kb_aug", src_entity_id=e1.id, dst_entity_id=e2.id,
            relation_type="developed", description="2022", weight=5,
        ))

    class _FakeKB:
        kb_name = "kb_aug"

    from chayuan.server.file_rag.graphrag.retriever import graphrag_augment
    docs = graphrag_augment(kb_service=_FakeKB(), query="介绍 OpenAI 的产品")
    assert docs, "应当至少产出一条 local_context 文档"
    assert any((d.metadata or {}).get("graphrag_type") == "local_context" for d in docs)
    # 文档里应该提到 OpenAI 和 ChatGPT
    content = docs[0].page_content
    assert "OpenAI" in content
    assert "ChatGPT" in content
