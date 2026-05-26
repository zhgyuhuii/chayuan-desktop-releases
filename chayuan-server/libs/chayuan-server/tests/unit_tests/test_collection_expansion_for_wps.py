"""94-6:WPS 加载项老接口透明展开 ``coll:*`` 前缀。

WPS 加载项的搜索请求把 KB 名以字符串列表形式发过来(``knowledge_base_names``)。
集合用 ``coll:<name>`` / ``coll:<id>`` 表示,服务端自动展开成子 ku_id,
WPS 端零改动。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from chayuan.server.db.base import Base
    from chayuan.server.db.models import kb_collection_model  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("chayuan.server.db.base.SessionLocal", Session)
    monkeypatch.setattr(
        "chayuan.server.db.session.SessionLocal", Session, raising=False,
    )
    return Session


def test_expand_passthrough_when_no_coll_prefix(tmp_db):
    from chayuan.server.api_server.kb_routes import _expand_collection_targets
    out = _expand_collection_targets(["doc:my_kb", "src:5", "plain_kb"])
    assert out == ["doc:my_kb", "src:5", "plain_kb"]


def test_expand_empty_input(tmp_db):
    from chayuan.server.api_server.kb_routes import _expand_collection_targets
    assert _expand_collection_targets([]) == []
    assert _expand_collection_targets(None) == []


def test_expand_coll_by_id(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    from chayuan.server.api_server.kb_routes import _expand_collection_targets

    d = repo.create_collection("c1", owner_id=1)
    repo.add_member(d["id"], "doc_a", "document")
    repo.add_member(d["id"], "src:5", "image")

    out = _expand_collection_targets([f"coll:{d['id']}"])
    assert "doc:doc_a" in out
    assert "src:5" in out


def test_expand_coll_by_name(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    from chayuan.server.api_server.kb_routes import _expand_collection_targets

    d = repo.create_collection("named_coll", owner_id=1)
    repo.add_member(d["id"], "doc_x", "document")

    out = _expand_collection_targets(["coll:named_coll"])
    assert out == ["doc:doc_x"]


def test_expand_unknown_coll_skipped(tmp_db):
    from chayuan.server.api_server.kb_routes import _expand_collection_targets
    # 集合不存在 → 跳过,不阻断其它项
    out = _expand_collection_targets(
        ["coll:ghost", "doc:real_kb", "coll:9999"],
    )
    assert out == ["doc:real_kb"]


def test_expand_image_member_already_src_prefix(tmp_db):
    """image 子库的 ku_id 已是 ``src:5`` → 不重复加前缀。"""
    from chayuan.server.db.repository import kb_collection_repository as repo
    from chayuan.server.api_server.kb_routes import _expand_collection_targets

    d = repo.create_collection("c2", owner_id=1)
    repo.add_member(d["id"], "src:7", "image")

    out = _expand_collection_targets([f"coll:{d['id']}"])
    assert out == ["src:7"]
    # 不会变成 src:src:7
    assert "src:src:7" not in out


def test_expand_mixed_with_non_coll(tmp_db):
    """coll: + doc: + 裸名 混传 → 全部正确分发。"""
    from chayuan.server.db.repository import kb_collection_repository as repo
    from chayuan.server.api_server.kb_routes import _expand_collection_targets

    d = repo.create_collection("c3", owner_id=1)
    repo.add_member(d["id"], "doc_inside_coll", "document")

    out = _expand_collection_targets([
        "doc:standalone_doc",
        f"coll:{d['id']}",
        "src:8",
    ])
    assert "doc:standalone_doc" in out
    assert "doc:doc_inside_coll" in out
    assert "src:8" in out


def test_split_search_targets_with_coll_prefix(tmp_db):
    """端到端:_split_search_targets 自动展开 coll: 前缀。"""
    from chayuan.server.db.repository import kb_collection_repository as repo
    from chayuan.server.api_server.kb_routes import _split_search_targets

    d = repo.create_collection("for_wps", owner_id=1)
    repo.add_member(d["id"], "doc_a", "document")
    repo.add_member(d["id"], "src:9", "image")

    doc_names, ku_ids = _split_search_targets([f"coll:{d['id']}"])
    assert doc_names == ["doc_a"]
    assert ku_ids == ["src:9"]


def test_expand_coll_with_unsupported_kind_passes_through(tmp_db):
    """如果 member kind 是非 doc/image(理论上 ORM 已校验),不应抛。"""
    from chayuan.server.api_server.kb_routes import _expand_collection_targets
    # 直接调 helper,故意构造错误数据走 try/except
    fake_coll = {
        "id": 1, "name": "x", "members": [
            {"ku_id": "weird_ku", "kind": "structured"},
        ],
    }
    with patch(
        "chayuan.server.db.repository.kb_collection_repository.get_collection",
        return_value=fake_coll,
    ), patch(
        "chayuan.server.db.repository.kb_collection_repository.get_collection_by_name",
        return_value=None,
    ):
        out = _expand_collection_targets(["coll:1"])
    # weird_ku 不带 doc:/src: 前缀,但仍被原样透传
    assert "weird_ku" in out


def test_expand_ignores_repository_error(tmp_db):
    """repo 抛异常 → 跳过该项,不冒泡。"""
    from chayuan.server.api_server.kb_routes import _expand_collection_targets
    with patch(
        "chayuan.server.db.repository.kb_collection_repository.get_collection",
        side_effect=RuntimeError("db dead"),
    ), patch(
        "chayuan.server.db.repository.kb_collection_repository.get_collection_by_name",
        side_effect=RuntimeError("db dead"),
    ):
        out = _expand_collection_targets(["coll:1", "doc:safe"])
    assert out == ["doc:safe"]
