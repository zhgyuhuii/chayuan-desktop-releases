"""Unit tests for plan v1.3 §4 — batch_search + download_token + app_acl boundary cases.

不依赖真实 KBService / Milvus；mock ``search_docs`` 即可覆盖核心融合 / provenance / dl_token / 限额。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# batch_search.SearchBatchIn — pydantic 校验
# ---------------------------------------------------------------------------

def test_search_batch_in_basic_ok():
    from chayuan.server.file_rag import batch_search as bs
    body = bs.SearchBatchIn(
        queries=[{"tag": "c1", "text": "x"}, {"tag": "c2", "text": "y"}],
        knowledge_base_names=["kb1"],
    )
    assert len(body.queries) == 2
    assert body.fusion == "rrf"
    assert body.final_top_k == bs.DEFAULT_FINAL_TOP_K


def test_search_batch_in_rejects_too_many_queries():
    from chayuan.server.file_rag import batch_search as bs
    qs = [{"tag": f"c{i}", "text": "x"} for i in range(bs.MAX_QUERIES + 1)]
    with pytest.raises(Exception):
        bs.SearchBatchIn(queries=qs, knowledge_base_names=["kb1"])


def test_search_batch_in_rejects_too_many_chars():
    from chayuan.server.file_rag import batch_search as bs
    huge = "a" * (bs.MAX_TOTAL_QUERY_CHARS + 1)
    with pytest.raises(Exception):
        bs.SearchBatchIn(queries=[{"tag": "c1", "text": huge}], knowledge_base_names=["kb1"])


def test_search_batch_in_rejects_dup_tags():
    from chayuan.server.file_rag import batch_search as bs
    with pytest.raises(Exception):
        bs.SearchBatchIn(
            queries=[{"tag": "c1", "text": "x"}, {"tag": "c1", "text": "y"}],
            knowledge_base_names=["kb1"],
        )


def test_search_batch_in_rejects_too_many_kbs():
    from chayuan.server.file_rag import batch_search as bs
    kbs = [f"kb{i}" for i in range(bs.MAX_KBS + 1)]
    with pytest.raises(Exception):
        bs.SearchBatchIn(queries=[{"tag": "c1", "text": "x"}], knowledge_base_names=kbs)


def test_search_batch_in_dedups_kbs():
    from chayuan.server.file_rag import batch_search as bs
    body = bs.SearchBatchIn(
        queries=[{"tag": "c1", "text": "x"}],
        knowledge_base_names=["kbA", "kbB", "kbA"],
    )
    assert body.knowledge_base_names == ["kbA", "kbB"]


# ---------------------------------------------------------------------------
# batch_search.run_batch — 业务流
# ---------------------------------------------------------------------------

def _fake_search(query, knowledge_base_name, top_k, score_threshold, file_name, metadata, use_hybrid, use_rerank):
    """每个查询都返回 top_k 条 mock 文档。"""
    return [
        {
            "id": f"{knowledge_base_name}#{query[:6]}#{i}",
            "page_content": f"text-{knowledge_base_name}-{i}",
            "score": 0.3 + 0.1 * i,
            "metadata": {"kb_name": knowledge_base_name, "source": f"{knowledge_base_name}/file_{i}.txt"},
        }
        for i in range(min(top_k, 3))
    ]


def test_run_batch_rrf_fusion_and_provenance():
    from chayuan.server.file_rag import batch_search as bs
    body = bs.SearchBatchIn(
        queries=[
            {"tag": "c1", "text": "x", "weight": 1.0, "section_ids": ["u1"]},
            {"tag": "c2", "text": "y", "weight": 0.5, "section_ids": ["u2"]},
        ],
        knowledge_base_names=["kbA", "kbB"],
        final_top_k=4, per_query_top_k=3,
    )
    user = {"id": 7, "role": "user"}
    with patch("chayuan.server.knowledge_base.kb_doc_api.search_docs", side_effect=_fake_search):
        out = asyncio.run(bs.run_batch(body, user=user, subject_for_token=user))

    assert out.summary.queries == 2
    assert out.summary.knowledge_bases == 2
    assert out.summary.fused_by == "rrf"
    assert out.summary.failed_subqueries == 0
    assert len(out.merged) == 4
    # provenance 完整
    for c in out.merged:
        assert "from_query_tags" in c
        assert "from_section_ids" in c
        assert c["chunk_id"]
        # download_token 已签发
        assert c["download_token"]


def test_run_batch_skips_failed_subqueries():
    from chayuan.server.file_rag import batch_search as bs

    def half_failing(query, knowledge_base_name, *args, **kwargs):
        if "fail" in query:
            raise RuntimeError("vector store down")
        return _fake_search(query, knowledge_base_name, *args, **kwargs)

    body = bs.SearchBatchIn(
        queries=[
            {"tag": "ok", "text": "good"},
            {"tag": "bad", "text": "fail-this-one"},
        ],
        knowledge_base_names=["kbA"],
        final_top_k=3, per_query_top_k=3,
    )
    with patch("chayuan.server.knowledge_base.kb_doc_api.search_docs", side_effect=half_failing):
        out = asyncio.run(bs.run_batch(body, user={"id": 1}, subject_for_token={"id": 1}))

    assert out.summary.failed_subqueries == 1
    # 没失败的子查询的结果照常返回
    assert len(out.merged) > 0
    assert any(e["tag"] == "bad" for e in out.errors)


def test_run_batch_filters_unauth_kbs_defensively():
    from chayuan.server.file_rag import batch_search as bs
    body = bs.SearchBatchIn(
        queries=[{"tag": "c1", "text": "x"}],
        knowledge_base_names=["kbA", "kbB"],
        final_top_k=4, per_query_top_k=3,
    )
    with patch("chayuan.server.knowledge_base.kb_doc_api.search_docs", side_effect=_fake_search):
        out = asyncio.run(bs.run_batch(
            body, user={"id": 1}, subject_for_token={"id": 1},
            accessible_kbs=["kbA"],   # 只允许 kbA
        ))
    # 即便 body.knowledge_base_names 含 kbB,实际只查了 kbA
    assert out.summary.knowledge_bases == 1
    for c in out.merged:
        assert c["kb_name"] == "kbA"


def test_run_batch_weighted_fusion_does_something():
    from chayuan.server.file_rag import batch_search as bs
    body = bs.SearchBatchIn(
        queries=[{"tag": "c1", "text": "x"}],
        knowledge_base_names=["kbA"],
        fusion="weighted", final_top_k=5,
    )
    with patch("chayuan.server.knowledge_base.kb_doc_api.search_docs", side_effect=_fake_search):
        out = asyncio.run(bs.run_batch(body, user={"id": 1}, subject_for_token={"id": 1}))
    assert out.summary.fused_by == "weighted"
    assert len(out.merged) >= 1


# ---------------------------------------------------------------------------
# auth/download_token — sign / verify / aud / mismatch
# ---------------------------------------------------------------------------

def test_download_token_sign_verify_user():
    from chayuan.server.auth.download_token import sign_download, verify_download
    tok = sign_download({"id": 42, "role": "user"}, "kbA", "file.txt")
    payload = verify_download(tok, expected_subject={"id": 42}, expected_kb="kbA", expected_file="file.txt")
    assert payload["aud"] == "42"
    assert payload["k"] == "kbA"
    assert payload["f"] == "file.txt"


def test_download_token_sign_verify_app():
    from chayuan.server.auth.download_token import sign_download, verify_download
    tok = sign_download({"id": "app:demo"}, "kbA", "file.txt")
    payload = verify_download(tok, expected_subject="app:demo")
    assert payload["aud"] == "app:demo"


def test_download_token_aud_mismatch_rejected():
    from chayuan.server.auth.download_token import sign_download, verify_download
    from chayuan.server.auth.tokens import TokenError
    tok = sign_download({"id": 42}, "kbA", "file.txt")
    with pytest.raises(TokenError, match="aud mismatch"):
        verify_download(tok, expected_subject={"id": 99})


def test_download_token_kb_mismatch_rejected():
    from chayuan.server.auth.download_token import sign_download, verify_download
    from chayuan.server.auth.tokens import TokenError
    tok = sign_download({"id": 42}, "kbA", "file.txt")
    with pytest.raises(TokenError, match="kb mismatch"):
        verify_download(tok, expected_kb="kbB")


def test_download_token_file_mismatch_rejected():
    from chayuan.server.auth.download_token import sign_download, verify_download
    from chayuan.server.auth.tokens import TokenError
    tok = sign_download({"id": 42}, "kbA", "file.txt")
    with pytest.raises(TokenError, match="file mismatch"):
        verify_download(tok, expected_file="other.txt")


def test_download_token_wrong_type_rejected():
    """access JWT 不应被 verify_download 接受(type 必须是 kb_download)。"""
    from chayuan.server.auth.download_token import verify_download
    from chayuan.server.auth.tokens import create_access_token, TokenError
    access = create_access_token(user_id=1, username="alice")
    with pytest.raises(TokenError):
        verify_download(access)


def test_is_download_token_helper():
    from chayuan.server.auth.download_token import is_download_token, sign_download
    from chayuan.server.auth.tokens import create_access_token
    assert is_download_token(sign_download({"id": 1}, "kb", "f"))
    assert not is_download_token(create_access_token(user_id=1, username="alice"))
    assert not is_download_token("not.a.token")


# ---------------------------------------------------------------------------
# app_acl — pure helpers (无需 DB)
# ---------------------------------------------------------------------------

def test_app_acl_is_app_user():
    from chayuan.server.auth.app_acl import is_app_user, extract_app_id
    assert is_app_user({"id": "app:abc"})
    assert not is_app_user({"id": 42})
    assert not is_app_user(None)
    assert extract_app_id({"id": "app:xyz"}) == "xyz"
    assert extract_app_id({"id": 42}) is None


def test_app_acl_no_grant_no_access(tmp_path, monkeypatch):
    """无 AppSpec 或 disabled 一定不可读。"""
    from chayuan.server.auth.app_acl import app_can_read_kb, list_app_accessible_kbs
    # 不存在的 app_id
    assert not app_can_read_kb("nonexistent-app-id-99999", "anykb")
    assert list_app_accessible_kbs("nonexistent-app-id-99999") == []
