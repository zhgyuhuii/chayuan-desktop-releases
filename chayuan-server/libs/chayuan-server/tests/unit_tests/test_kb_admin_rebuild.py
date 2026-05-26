from __future__ import annotations

import pytest
from fastapi import HTTPException


def test_require_admin_rejects_non_admin():
    from chayuan.server.api_server.kb_routes import _require_admin

    with pytest.raises(HTTPException) as exc:
        _require_admin({"id": 1, "role": "user"})

    assert exc.value.status_code == 403


def test_require_admin_accepts_admin():
    from chayuan.server.api_server.kb_routes import _require_admin

    _require_admin({"id": 1, "role": "admin"})


def test_embedding_model_selectable_requires_configured_model(monkeypatch):
    from chayuan.server.api_server import kb_routes
    from chayuan.server import utils

    monkeypatch.setattr(utils, "get_config_models", lambda model_type=None: {})

    with pytest.raises(HTTPException) as exc:
        kb_routes._assert_embedding_model_selectable("missing-model", check_access=False)

    assert exc.value.status_code == 400
    assert "not configured" in str(exc.value.detail)


def test_embedding_model_selectable_rejects_unreachable_model(monkeypatch):
    from chayuan.server.api_server import kb_routes
    from chayuan.server import utils

    monkeypatch.setattr(
        utils,
        "get_config_models",
        lambda model_type=None: {
            "bge-large": {
                "platform_name": "test",
                "platform_type": "openai",
                "model_type": "embed",
            }
        },
    )
    monkeypatch.setattr(utils, "check_embed_model", lambda model: (False, "connection refused"))

    with pytest.raises(HTTPException) as exc:
        kb_routes._assert_embedding_model_selectable("bge-large", check_access=True)

    assert exc.value.status_code == 400
    assert "connection refused" in str(exc.value.detail)


def test_available_embedding_models_reports_probe_result(monkeypatch):
    from chayuan.server.api_server import kb_routes
    from chayuan.server import utils

    monkeypatch.setattr(
        utils,
        "get_config_models",
        lambda model_type=None: {
            "bge-large": {
                "platform_name": "test",
                "platform_type": "openai",
                "model_type": "embed",
            }
        },
    )
    monkeypatch.setattr(utils, "check_embed_model", lambda model: (True, ""))

    rows = kb_routes._available_embedding_models(check_access=True)

    assert rows == [
        {
            "id": "bge-large",
            "model_name": "bge-large",
            "platform_name": "test",
            "platform_type": "openai",
            "model_type": "embed",
            "available": True,
            "reason": "",
        }
    ]


def test_normalize_chunk_payload_skips_empty_and_preserves_metadata():
    from chayuan.server.api_server.kb_routes import _normalize_chunk_payload

    chunks = _normalize_chunk_payload(
        [
            {"chunk_id": "c1", "text": " 第一段 ", "metadata": {"page": 1}},
            {"chunk_id": "empty", "text": "   "},
            {"id": "c2", "page_content": "第二段"},
        ]
    )

    assert chunks == [
        {"chunk_id": "c1", "text": "第一段", "metadata": {"page": 1}},
        {"chunk_id": "c2", "text": "第二段", "metadata": {}},
    ]


def test_normalize_chunk_payload_rejects_all_empty():
    from chayuan.server.api_server.kb_routes import _normalize_chunk_payload

    with pytest.raises(HTTPException) as exc:
        _normalize_chunk_payload([{"text": ""}])

    assert exc.value.status_code == 400


def test_list_file_chunks_from_source_does_not_touch_vector_store(monkeypatch):
    from langchain_core.documents import Document

    from chayuan.server.api_server import kb_routes
    from chayuan.server.db.repository import knowledge_base_repository, knowledge_file_repository
    from chayuan.server.knowledge_base import utils as kb_utils

    monkeypatch.setattr(
        knowledge_base_repository,
        "load_kb_from_db",
        lambda kb_name: (kb_name, "milvus", "embed"),
    )
    monkeypatch.setattr(
        knowledge_file_repository,
        "get_file_detail",
        lambda kb_name, filename: {
            "file_name": filename,
            "text_splitter": "RecursiveCharacterTextSplitter",
            "custom_docs": False,
        },
    )

    class FakeKnowledgeFile:
        def __init__(self, filename: str, knowledge_base_name: str):
            self.filename = filename
            self.kb_name = knowledge_base_name
            self.text_splitter_name = ""

        def file2text(self, **kwargs):
            return [
                Document(
                    page_content="第一段",
                    metadata={"source": self.filename, "id": "chunk-1"},
                )
            ]

    monkeypatch.setattr(kb_utils, "KnowledgeFile", FakeKnowledgeFile)

    data = kb_routes._list_file_chunks_from_source("kb1", "a.md")

    assert data["source"] == "source_file"
    assert data["chunk_count"] == 1
    assert data["chunks"][0]["text"] == "第一段"
    assert data["chunks"][0]["chunk_id"] == "chunk-1"
