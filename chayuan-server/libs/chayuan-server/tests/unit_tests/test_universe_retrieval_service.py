from __future__ import annotations

import asyncio

from chayuan.server.retrieval.query.refs import KnowledgeRef
from chayuan.server.retrieval.universe import service


def test_block_to_chunks_normalizes_structured_rows_and_sql():
    chunks = service.block_to_chunks(
        {
            "ku_id": "src:7",
            "kind": "structured",
            "summary": "共有 3 个用户",
            "sql": "select count(*) as total from users",
            "columns": ["total"],
            "rows": [{"total": 3}],
            "results": [],
        },
        tag="count",
        section_ids=["s1"],
    )

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["kb_name"] == "src:7"
    assert "共有 3 个用户" in chunk["text"]
    assert "select count(*)" in chunk["text"]
    assert chunk["metadata"]["rows"] == [{"total": 3}]
    assert chunk["from_query_tags"] == ["count"]
    assert chunk["from_section_ids"] == ["s1"]


def test_search_ku_chunks_keeps_partial_success_when_one_source_fails(monkeypatch):
    def fake_process(ku_id, query, top_k, **kwargs):
        if ku_id == "src:bad":
            raise RuntimeError("model 'qwen3.6:latest' not found")
        return {
            "ku_id": ku_id,
            "kind": "structured",
            "ok": True,
            "summary": "共有 1 个用户",
            "sql": "select count(*) as total from users",
            "rows": [{"total": 1}],
        }

    monkeypatch.setattr(service, "_process_one", fake_process)

    chunks, errors, blocks = asyncio.run(service.search_ku_chunks(
        [
            ("src:ok", "有多少个用户", "q1", []),
            ("src:bad", "有多少个用户", "q1", []),
        ],
        top_k=3,
        timeout_s=5,
        max_concurrency=2,
    ))

    assert len(blocks) == 2
    assert len(chunks) == 2
    assert any(chunk["kb_name"] == "src:ok" and "共有 1 个用户" in chunk["text"] for chunk in chunks)
    assert any(chunk["kb_name"] == "src:bad" and "结构化检索模型不可用" in chunk["text"] for chunk in chunks)
    assert errors == [{"kb": "src:bad", "tag": "q1", "error": "结构化检索模型不可用，请检查 Ollama 模型配置。"}]


def test_process_one_dispatches_image_kind(monkeypatch):
    """回归:首页内容搜索碰到 image KB,以前会落到"未知知识类型: image"。

    现在 _process_one 必须把 ref.kind == "image" 路由到 search_image。
    """
    image_ref = KnowledgeRef(
        kb_id="src:42", kind="image", raw_id="42", name="my-images",
        display_name="我的图片", sub_kind="image",
    )

    captured = {}

    def fake_resolve(ku_id: str) -> KnowledgeRef:
        captured["ku_id"] = ku_id
        return image_ref

    def fake_process_one_ku(ku_id, query, top_k, **kwargs):
        captured["routed"] = True
        return {
            "ku_id": ku_id,
            "kind": "image",
            "ok": True,
            "results": [{
                "id": "img1",
                "path": "/data/cat.jpg",
                "preview_url": "/preview/img1",
                "download_url": "/download/img1",
                "title": "cat.jpg",
                "caption": "一只猫",
                "score": 0.87,
            }],
        }

    monkeypatch.setattr(
        "chayuan.server.retrieval.query.refs.resolve_ref", fake_resolve
    )
    monkeypatch.setattr(
        "chayuan.server.api_server.knowledge_universe_routes._process_one_ku",
        fake_process_one_ku,
    )

    block = service._process_one(
        "src:42", "猫", 5,
        use_hybrid=None, use_rerank=None, rewrite_strategy="passthrough",
    )

    assert captured.get("routed") is True, "image kind 没被路由到 _process_one_ku"
    assert block["ok"] is True
    assert block["kind"] == "image"
    assert "未知知识类型" not in str(block.get("error") or "")
    assert len(block["results"]) == 1
    hit = block["results"][0]
    assert hit["source_type"] == "image"
    assert hit["retrieval_path"] == "image"
    assert hit["score"] == 0.87
    assert hit["metadata"]["path"] == "/data/cat.jpg"
    assert hit["metadata"]["caption"] == "一只猫"
    assert hit["citation"]["preview_url"] == "/preview/img1"


def test_process_one_image_kind_propagates_connector_error(monkeypatch):
    """image KB 报错(如 embedder 未就绪)要带出 block.error,不能吞。"""
    image_ref = KnowledgeRef(
        kb_id="src:42", kind="image", raw_id="42", name="my-images",
        display_name="我的图片", sub_kind="image",
    )

    monkeypatch.setattr(
        "chayuan.server.retrieval.query.refs.resolve_ref", lambda _: image_ref
    )
    monkeypatch.setattr(
        "chayuan.server.api_server.knowledge_universe_routes._process_one_ku",
        lambda *a, **k: {"ku_id": "src:42", "kind": "image", "ok": False,
                         "error": "图像 Embedder 未就绪"},
    )

    block = service._process_one(
        "src:42", "猫", 5,
        use_hybrid=None, use_rerank=None, rewrite_strategy="passthrough",
    )

    assert block["ok"] is False
    assert block["kind"] == "image"
    assert "Embedder 未就绪" in block["error"]
