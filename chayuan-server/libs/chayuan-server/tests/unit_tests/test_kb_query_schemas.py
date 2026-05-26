from __future__ import annotations

import pytest
from pydantic import ValidationError

from chayuan.server.kb_query.schemas import SearchRequest


def test_search_request_accepts_normal_payload():
    req = SearchRequest(
        kb_id="doc:samples",
        contents=[
            {
                "content_id": "q1",
                "text": "介绍一下知识库",
                "metadata": {"source": "unit-test"},
            }
        ],
        options={"top_k": 3, "return_diagnostics": True},
    )

    assert req.kb_id == "doc:samples"
    assert req.contents[0].content_id == "q1"
    assert req.options.top_k == 3


def test_search_request_normalizes_unified_refs_and_options():
    req = SearchRequest(
        ku_ids=["src:42"],
        contents=[{"text": "有几个用户"}],
        structured_scopes={"src:42": ["users"]},
        options={"top_k_per_query": 8, "merge_strategy": "weighted"},
    )

    assert req.ku_ids == ["src:42"]
    assert req.options.effective_top_k == 8
    assert req.options.effective_fusion == "weighted"
    assert req.options.structured_scopes == {"src:42": ["users"]}


def test_search_request_backfills_kb_id_from_ku_ids():
    """前端只发 ku_ids 时,kb_id / knowledge_base 必须被回填,避免旧 service.py 报 400。"""
    req = SearchRequest(
        ku_ids=["doc:samples"],
        contents=[{"text": "查一下"}],
    )

    assert req.kb_id == "doc:samples"
    # 旧 registry.resolve_ref(knowledge_base=...) 走的是裸名,不带 doc: 前缀
    assert req.knowledge_base == "samples"


def test_search_request_backfills_kb_id_for_source_ku_ids():
    req = SearchRequest(
        ku_ids=["src:42"],
        contents=[{"text": "有几个用户"}],
    )

    assert req.kb_id == "src:42"
    assert req.knowledge_base == "42"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kb_id": "doc:samples",
            "embed_model": "bge-large-zh",
            "contents": [{"text": "query"}],
        },
        {
            "kb_id": "doc:samples",
            "contents": [{"text": "query"}],
            "options": {"embedding_model": "bge-large-zh"},
        },
        {
            "kb_id": "doc:samples",
            "contents": [
                {
                    "text": "query",
                    "metadata": {"model_name_for_embedding": "bge-large-zh"},
                }
            ],
        },
    ],
)
def test_search_request_rejects_embedding_model_overrides(payload):
    with pytest.raises(ValidationError) as exc:
        SearchRequest(**payload)

    assert "embedding_model_not_allowed" in str(exc.value)
