"""95-4:folder_sync_routes 路由测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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


@pytest.fixture
def fake_chayuan_root(tmp_path, monkeypatch):
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def no_scheduler(monkeypatch):
    """禁用真实 scheduler,让测试不启动 apscheduler。"""
    monkeypatch.setattr(
        "chayuan.server.folder_sync.scheduler.schedule_job",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "chayuan.server.folder_sync.scheduler.unschedule_job",
        lambda *a, **k: True,
    )


def _user(uid=1):
    return {"id": uid, "role": "user", "is_guest": False}


def test_create_job_validates_inputs(tmp_db, no_scheduler):
    from chayuan.server.api_server.folder_sync_routes import (
        create_job_endpoint,
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        create_job_endpoint(payload={"name": ""}, user=_user())
    assert exc.value.status_code == 400


def test_create_and_list_job(tmp_db, no_scheduler, fake_chayuan_root, tmp_path):
    from chayuan.server.api_server.folder_sync_routes import (
        create_job_endpoint, list_jobs_endpoint,
    )

    folder = tmp_path / "f"
    folder.mkdir()
    ret = create_job_endpoint(
        payload={
            "name": "项目同步", "folder_path": str(folder),
            "target": "doc:my_kb", "interval_seconds": 300,
        },
        user=_user(),
    )
    assert ret["code"] == 0
    listed = list_jobs_endpoint(user=_user())
    assert listed["data"]["total"] == 1


def test_owner_check_blocks_get(tmp_db, no_scheduler, fake_chayuan_root, tmp_path):
    from chayuan.server.api_server.folder_sync_routes import (
        create_job_endpoint, get_job_endpoint,
    )
    from fastapi import HTTPException

    folder = tmp_path / "f"; folder.mkdir()
    d = create_job_endpoint(
        payload={"name": "x", "folder_path": str(folder), "target": "t"},
        user=_user(uid=1),
    )["data"]
    with pytest.raises(HTTPException) as exc:
        get_job_endpoint(job_id=d["id"], user=_user(uid=999))
    assert exc.value.status_code == 403


def test_update_job(tmp_db, no_scheduler, fake_chayuan_root, tmp_path):
    from chayuan.server.api_server.folder_sync_routes import (
        create_job_endpoint, update_job_endpoint,
    )
    folder = tmp_path / "f"; folder.mkdir()
    d = create_job_endpoint(
        payload={"name": "x", "folder_path": str(folder), "target": "t"},
        user=_user(),
    )["data"]
    upd = update_job_endpoint(
        job_id=d["id"], payload={"name": "renamed", "interval_seconds": 600},
        user=_user(),
    )
    assert upd["data"]["name"] == "renamed"
    assert upd["data"]["interval_seconds"] == 600


def test_delete_job_unschedules(tmp_db, no_scheduler, fake_chayuan_root, tmp_path):
    from chayuan.server.api_server.folder_sync_routes import (
        create_job_endpoint, delete_job_endpoint,
    )
    folder = tmp_path / "f"; folder.mkdir()
    d = create_job_endpoint(
        payload={"name": "x", "folder_path": str(folder), "target": "t"},
        user=_user(),
    )["data"]

    unsched = MagicMock()
    with patch(
        "chayuan.server.folder_sync.scheduler.unschedule_job", unsched,
    ):
        ret = delete_job_endpoint(job_id=d["id"], user=_user())
    assert ret["code"] == 0
    unsched.assert_called_once_with(d["id"])


def test_dry_run_returns_diff_preview(tmp_db, no_scheduler, fake_chayuan_root, tmp_path):
    """干跑:不调用真 doc/img upload,只返 diff。"""
    from chayuan.server.api_server.folder_sync_routes import (
        create_job_endpoint, dry_run_endpoint,
    )

    folder = tmp_path / "src"; folder.mkdir()
    (folder / "a.pdf").write_bytes(b"x")
    (folder / "b.jpg").write_bytes(b"y")

    d = create_job_endpoint(
        payload={"name": "x", "folder_path": str(folder),
                 "target": "doc:any", "interval_seconds": 60},
        user=_user(),
    )["data"]
    ret = dry_run_endpoint(job_id=d["id"], user=_user())
    assert ret["code"] == 0
    preview = ret["data"]["diff_preview"]
    assert len(preview["added"]) == 2
    assert ret["data"]["applied"] is False


def test_trigger_job_calls_real_uploaders(
    tmp_db, no_scheduler, fake_chayuan_root, tmp_path,
):
    from chayuan.server.api_server.folder_sync_routes import (
        create_job_endpoint, trigger_job_endpoint,
    )

    folder = tmp_path / "src"; folder.mkdir()
    (folder / "a.pdf").write_bytes(b"x")
    (folder / "b.jpg").write_bytes(b"y")

    d = create_job_endpoint(
        payload={"name": "x", "folder_path": str(folder),
                 "target": "doc:my_kb", "interval_seconds": 60},
        user=_user(),
    )["data"]

    doc_calls = []

    def _doc_up(kb, p):
        doc_calls.append((kb, p))

    with patch(
        "chayuan.server.api_server.folder_sync_routes._doc_upload_hook",
        _doc_up,
    ), patch(
        "chayuan.server.api_server.folder_sync_routes._img_upload_hook",
        lambda sid, p: None,
    ):
        ret = trigger_job_endpoint(job_id=d["id"], user=_user())

    assert ret["code"] == 0
    assert ret["data"]["applied"] is True
    # a.pdf 应被 ingest;b.jpg 在 doc target 下会标 error
    pdf_calls = [c for c in doc_calls if c[1].endswith("a.pdf")]
    assert len(pdf_calls) == 1


def test_get_unknown_job_404(tmp_db, no_scheduler):
    from chayuan.server.api_server.folder_sync_routes import (
        get_job_endpoint,
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        get_job_endpoint(job_id=99999, user=_user())
    assert exc.value.status_code == 404


def test_create_job_with_disabled_does_not_schedule(
    tmp_db, fake_chayuan_root, tmp_path, monkeypatch,
):
    """enabled=false → 不调 schedule_job。"""
    from chayuan.server.api_server.folder_sync_routes import (
        create_job_endpoint,
    )

    sched_calls = []
    monkeypatch.setattr(
        "chayuan.server.folder_sync.scheduler.schedule_job",
        lambda jid, sec, fn: sched_calls.append(jid),
    )
    monkeypatch.setattr(
        "chayuan.server.folder_sync.scheduler.unschedule_job",
        lambda jid: True,
    )

    folder = tmp_path / "f"; folder.mkdir()
    create_job_endpoint(
        payload={
            "name": "disabled", "folder_path": str(folder),
            "target": "t", "enabled": False,
        },
        user=_user(),
    )
    assert sched_calls == []  # 没启用就不挂调度
