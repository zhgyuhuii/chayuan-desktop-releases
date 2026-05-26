"""94-1:kb_collection_repository 端到端测试(内存 SQLite)。"""
from __future__ import annotations

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    """临时内存 SQLite + 全部 ORM 表。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from chayuan.server.db.base import Base
    # 关键:import 模型 module 让 ORM 注册到 Base.metadata
    from chayuan.server.db.models import kb_collection_model  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr("chayuan.server.db.base.SessionLocal", Session)
    monkeypatch.setattr(
        "chayuan.server.db.session.SessionLocal", Session, raising=False,
    )
    return Session


def test_create_and_get_collection(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo

    d = repo.create_collection(
        "proj_alpha", owner_id=1,
        display_name="项目 Alpha", description="混合知识库",
    )
    assert d["id"] > 0
    assert d["name"] == "proj_alpha"
    assert d["display_name"] == "项目 Alpha"

    got = repo.get_collection(d["id"])
    assert got is not None
    assert got["name"] == "proj_alpha"
    assert got["members"] == []


def test_create_duplicate_name_raises(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    repo.create_collection("dup", owner_id=1)
    with pytest.raises(ValueError, match="已存在"):
        repo.create_collection("dup", owner_id=1)


def test_create_empty_name_raises(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    with pytest.raises(ValueError, match="name required"):
        repo.create_collection("", owner_id=1)
    with pytest.raises(ValueError, match="name required"):
        repo.create_collection("   ", owner_id=1)


def test_list_collections_filter_by_owner(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    repo.create_collection("alice_1", owner_id=1)
    repo.create_collection("alice_2", owner_id=1)
    repo.create_collection("bob_1", owner_id=2)

    alice = repo.list_collections(owner_id=1)
    assert {c["name"] for c in alice} == {"alice_1", "alice_2"}
    bob = repo.list_collections(owner_id=2)
    assert {c["name"] for c in bob} == {"bob_1"}
    all_c = repo.list_collections()
    assert len(all_c) == 3


def test_get_collection_returns_none_for_unknown(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    assert repo.get_collection(99999) is None


def test_update_collection_changes_display_name_and_description(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    d = repo.create_collection("orig", owner_id=1, display_name="A")
    updated = repo.update_collection(d["id"], display_name="B", description="new")
    assert updated["display_name"] == "B"
    assert updated["description"] == "new"


def test_delete_collection_cascades_members(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo

    d = repo.create_collection("tobedel", owner_id=1)
    repo.add_member(d["id"], "doc_kb_1", "document")
    repo.add_member(d["id"], "img_kb_1", "image")
    assert len(repo.list_members(d["id"])) == 2

    deleted = repo.delete_collection(d["id"])
    assert deleted is True
    assert repo.get_collection(d["id"]) is None
    assert repo.list_members(d["id"]) == []


def test_delete_unknown_collection_returns_false(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    assert repo.delete_collection(99999) is False


# ---------------------------------------------------------------------------
# 成员关系
# ---------------------------------------------------------------------------

def test_add_member_kind_validation(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    d = repo.create_collection("c1", owner_id=1)
    with pytest.raises(ValueError, match="kind 必须"):
        repo.add_member(d["id"], "x", "structured")


def test_add_member_unknown_collection_raises(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    with pytest.raises(ValueError, match="不存在"):
        repo.add_member(99999, "x", "document")


def test_add_member_duplicate_ku_raises(tmp_db):
    """一个 ku_id 同时只能在一个集合(包含跨集合)。"""
    from chayuan.server.db.repository import kb_collection_repository as repo
    a = repo.create_collection("a", owner_id=1)
    b = repo.create_collection("b", owner_id=1)
    repo.add_member(a["id"], "shared_ku", "document")
    with pytest.raises(ValueError, match="已在"):
        repo.add_member(b["id"], "shared_ku", "document")


def test_add_and_list_members_sorted(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    d = repo.create_collection("c1", owner_id=1)
    repo.add_member(d["id"], "ku_2", "document", sort_order=1)
    repo.add_member(d["id"], "ku_1", "image", sort_order=0)
    members = repo.list_members(d["id"])
    # sort_order asc
    assert [m["ku_id"] for m in members] == ["ku_1", "ku_2"]


def test_remove_member(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    d = repo.create_collection("c1", owner_id=1)
    repo.add_member(d["id"], "ku_x", "document")
    assert repo.remove_member(d["id"], "ku_x") is True
    assert repo.remove_member(d["id"], "ku_x") is False  # 已移除


def test_get_collection_for_ku_reverse_lookup(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    d = repo.create_collection("c1", owner_id=1, display_name="C-1")
    repo.add_member(d["id"], "ku_x", "image")
    coll = repo.get_collection_for_ku("ku_x")
    assert coll is not None
    assert coll["name"] == "c1"

    assert repo.get_collection_for_ku("not_exist") is None


def test_get_collection_with_members_field(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    d = repo.create_collection("c1", owner_id=1)
    repo.add_member(d["id"], "ku_a", "document")
    repo.add_member(d["id"], "ku_b", "image")
    detail = repo.get_collection(d["id"])
    assert len(detail["members"]) == 2
    kinds = {m["kind"] for m in detail["members"]}
    assert kinds == {"document", "image"}


def test_member_count_in_list(tmp_db):
    from chayuan.server.db.repository import kb_collection_repository as repo
    d1 = repo.create_collection("c1", owner_id=1)
    d2 = repo.create_collection("c2", owner_id=1)
    repo.add_member(d1["id"], "ku_a", "document")
    repo.add_member(d1["id"], "ku_b", "image")
    repo.add_member(d2["id"], "ku_c", "document")

    items = {c["name"]: c for c in repo.list_collections(owner_id=1)}
    assert items["c1"]["member_count"] == 2
    assert items["c2"]["member_count"] == 1
