"""95-1:folder_sync_repository 端到端测试。"""
from __future__ import annotations

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from chayuan.server.db.base import Base
    from chayuan.server.db.models import folder_sync_model  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("chayuan.server.db.base.SessionLocal", Session)
    monkeypatch.setattr(
        "chayuan.server.db.session.SessionLocal", Session, raising=False,
    )
    return Session


def test_create_and_get_job(tmp_db):
    from chayuan.server.db.repository import folder_sync_repository as repo
    d = repo.create_job(
        name="项目素材同步", folder_path="/data/proj_x", target="coll:7",
        owner_id=1, interval_seconds=300,
    )
    assert d["id"] > 0
    assert d["folder_path"] == "/data/proj_x"
    assert d["interval_seconds"] == 300
    assert d["enabled"] is True
    assert "*.pdf" in d["include_globs"]  # 默认 globs

    got = repo.get_job(d["id"])
    assert got["name"] == "项目素材同步"


def test_create_validates_inputs(tmp_db):
    from chayuan.server.db.repository import folder_sync_repository as repo
    with pytest.raises(ValueError, match="name"):
        repo.create_job(name="", folder_path="/x", target="coll:1", owner_id=1)
    with pytest.raises(ValueError, match="folder_path"):
        repo.create_job(name="x", folder_path="", target="coll:1", owner_id=1)
    with pytest.raises(ValueError, match="target"):
        repo.create_job(name="x", folder_path="/x", target="", owner_id=1)
    with pytest.raises(ValueError, match="30"):
        repo.create_job(
            name="x", folder_path="/x", target="t", owner_id=1,
            interval_seconds=10,
        )


def test_list_jobs_filter_by_owner(tmp_db):
    from chayuan.server.db.repository import folder_sync_repository as repo
    repo.create_job(name="a", folder_path="/a", target="t", owner_id=1)
    repo.create_job(name="b", folder_path="/b", target="t", owner_id=2)
    alice = repo.list_jobs(owner_id=1)
    assert {j["name"] for j in alice} == {"a"}


def test_list_jobs_enabled_only(tmp_db):
    from chayuan.server.db.repository import folder_sync_repository as repo
    j1 = repo.create_job(name="a", folder_path="/a", target="t", owner_id=1)
    repo.create_job(
        name="b", folder_path="/b", target="t", owner_id=1, enabled=False,
    )
    enabled = repo.list_jobs(owner_id=1, enabled_only=True)
    assert {j["name"] for j in enabled} == {"a"}


def test_update_job(tmp_db):
    from chayuan.server.db.repository import folder_sync_repository as repo
    d = repo.create_job(
        name="orig", folder_path="/o", target="t", owner_id=1,
    )
    upd = repo.update_job(
        d["id"], name="renamed", interval_seconds=600, enabled=False,
    )
    assert upd["name"] == "renamed"
    assert upd["interval_seconds"] == 600
    assert upd["enabled"] is False


def test_update_validates_interval(tmp_db):
    from chayuan.server.db.repository import folder_sync_repository as repo
    d = repo.create_job(
        name="x", folder_path="/x", target="t", owner_id=1,
    )
    with pytest.raises(ValueError, match="30"):
        repo.update_job(d["id"], interval_seconds=5)


def test_delete_job(tmp_db):
    from chayuan.server.db.repository import folder_sync_repository as repo
    d = repo.create_job(
        name="x", folder_path="/x", target="t", owner_id=1,
    )
    assert repo.delete_job(d["id"]) is True
    assert repo.get_job(d["id"]) is None
    assert repo.delete_job(d["id"]) is False  # 已删


def test_record_sync_result(tmp_db):
    from chayuan.server.db.repository import folder_sync_repository as repo
    d = repo.create_job(
        name="x", folder_path="/x", target="t", owner_id=1,
    )
    upd = repo.record_sync_result(
        d["id"], {"added": 3, "modified": 1, "removed": 0, "errors": 0},
    )
    assert upd["last_sync_at"] is not None
    assert upd["last_sync_summary"]["added"] == 3
