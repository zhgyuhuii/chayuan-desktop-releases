"""``POST/GET /admin/models/install_release`` 路由集成测试。

2026-05-15 重写:install_job 已从 subprocess(chayuan_packaging)改为
in-process(huggingface_hub),mock 点从 ``install_job.subprocess.Popen``
移到 ``huggingface_hub.hf_hub_download / snapshot_download``。
"""
from __future__ import annotations

import functools
import threading
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chayuan.server.api_server.admin_routes import admin_router
from chayuan.server.model_registry.install_job import (
    InstallJobManager,
    JobStatus,
)


@pytest.fixture(autouse=True)
def _isolate_chayuan_root(tmp_path, monkeypatch):
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    yield


@pytest.fixture
def client_with_fresh_manager(monkeypatch) -> TestClient:
    """每个测试给一个全新的 InstallJobManager,避免互相污染。"""
    fresh = InstallJobManager()
    monkeypatch.setattr(
        "chayuan.server.model_registry.install_job.get_install_manager",
        lambda: fresh,
    )
    app = FastAPI()
    app.include_router(admin_router)
    return TestClient(app)


def _wait_terminal(client: TestClient, timeout: float = 3.0) -> None:
    """阻塞直到所有任务进入终态(succeeded / failed / cancelled)。"""
    from chayuan.server.model_registry.install_job import get_install_manager
    deadline = time.time() + timeout
    while time.time() < deadline:
        all_done = all(
            j.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
            for j in get_install_manager().list()
        )
        if all_done and get_install_manager().list():
            return
        time.sleep(0.02)


def _fake_hub_ok(**kwargs: Any) -> str:
    local_dir = Path(kwargs.get("local_dir") or kwargs.get("cache_dir") or "/tmp")
    local_dir.mkdir(parents=True, exist_ok=True)
    fname = kwargs.get("filename") or "snapshot.touch"
    p = local_dir / fname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return str(p)


def _patch_hub_ok():
    """组合 patch:hf_hub_download / snapshot_download / scan_once / promote。"""
    return [
        mock.patch("huggingface_hub.hf_hub_download", side_effect=_fake_hub_ok),
        mock.patch("huggingface_hub.snapshot_download", side_effect=_fake_hub_ok),
        mock.patch("chayuan.server.model_registry.local_index.scan_once"),
        mock.patch(
            "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
            return_value={},
        ),
    ]


def _with_hub(fn):
    """装饰:启用一组 patch 跑测试主体,自动清理。

    用 ``functools.wraps`` 保留 fn 签名,让 pytest 能正确注入 fixture。
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        patches = _patch_hub_ok()
        for p in patches:
            p.start()
        try:
            return fn(*args, **kwargs)
        finally:
            for p in patches:
                p.stop()
    return wrapper


# ───────────────────────── POST /install_release ─────────────────────────


@_with_hub
def test_post_starts_job_and_returns_job_dict(client_with_fresh_manager):
    resp = client_with_fresh_manager.post(
        "/admin/models/install_release",
        json={"release": "lite"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["release"] == "lite"
    assert "id" in data
    assert data["status"] in ("queued", "running", "succeeded")


def test_post_400_on_unknown_release(client_with_fresh_manager):
    resp = client_with_fresh_manager.post(
        "/admin/models/install_release",
        json={"release": "ultra"},
    )
    assert resp.status_code == 400
    assert "unsupported release" in resp.text


def test_post_400_on_unknown_mirror(client_with_fresh_manager):
    resp = client_with_fresh_manager.post(
        "/admin/models/install_release",
        json={"release": "lite", "mirror": "evil-mirror"},
    )
    assert resp.status_code == 400
    assert "unknown mirror" in resp.text


def test_post_409_on_duplicate_running(client_with_fresh_manager):
    """同 release 已有运行任务应返回 409 Conflict。"""
    blocker = threading.Event()

    def _block(**kwargs):
        blocker.wait(timeout=3.0)
        return _fake_hub_ok(**kwargs)

    patches = [
        mock.patch("huggingface_hub.hf_hub_download", side_effect=_block),
        mock.patch("huggingface_hub.snapshot_download", side_effect=_block),
        mock.patch("chayuan.server.model_registry.local_index.scan_once"),
        mock.patch(
            "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
            return_value={},
        ),
    ]
    for p in patches:
        p.start()
    try:
        r1 = client_with_fresh_manager.post(
            "/admin/models/install_release", json={"release": "lite"})
        assert r1.status_code == 200
        time.sleep(0.1)
        r2 = client_with_fresh_manager.post(
            "/admin/models/install_release", json={"release": "lite"})
        assert r2.status_code == 409
        assert "已有运行中任务" in r2.text
    finally:
        blocker.set()
        for p in patches:
            p.stop()


# ───────────────────────── GET /install_release/{id} ─────────────────────────


def test_get_returns_404_for_unknown_id(client_with_fresh_manager):
    resp = client_with_fresh_manager.get(
        "/admin/models/install_release/00000000-aaaa-bbbb-cccc-000000000000"
    )
    assert resp.status_code == 404


@_with_hub
def test_get_returns_job_status(client_with_fresh_manager):
    post = client_with_fresh_manager.post(
        "/admin/models/install_release", json={"release": "lite"})
    job_id = post.json()["data"]["id"]
    _wait_terminal(client_with_fresh_manager)
    resp = client_with_fresh_manager.get(
        f"/admin/models/install_release/{job_id}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == job_id
    assert data["status"] == "succeeded"
    assert "log_tail" in data
    assert "progress_pct" in data
    assert data["progress_pct"] == 100.0
    assert data["files_total"] == data["files_done"]


# ───────────────────────── GET /install_release (list) ─────────────────────────


@_with_hub
def test_list_returns_jobs_newest_first(client_with_fresh_manager):
    for rel in ("lite", "standard"):
        client_with_fresh_manager.post(
            "/admin/models/install_release", json={"release": rel})
        _wait_terminal(client_with_fresh_manager)
    resp = client_with_fresh_manager.get("/admin/models/install_release")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) >= 2
    releases = [i["release"] for i in items[:2]]
    assert releases[0] == "standard"
    assert releases[1] == "lite"


@_with_hub
def test_list_respects_limit(client_with_fresh_manager):
    for rel in ("lite", "standard", "pro"):
        client_with_fresh_manager.post(
            "/admin/models/install_release", json={"release": rel})
        _wait_terminal(client_with_fresh_manager)
    resp = client_with_fresh_manager.get(
        "/admin/models/install_release", params={"limit": 2})
    items = resp.json()["data"]["items"]
    assert len(items) == 2


# ─────────────────────── POST /install_release/{id}/cancel ───────────────────────


def test_cancel_404_on_unknown_id(client_with_fresh_manager):
    resp = client_with_fresh_manager.post(
        "/admin/models/install_release/00000000-0000-0000-0000-000000000000/cancel"
    )
    assert resp.status_code == 404


@_with_hub
def test_cancel_terminal_job_is_noop(client_with_fresh_manager):
    post = client_with_fresh_manager.post(
        "/admin/models/install_release", json={"release": "lite"})
    job_id = post.json()["data"]["id"]
    _wait_terminal(client_with_fresh_manager)
    resp = client_with_fresh_manager.post(
        f"/admin/models/install_release/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "succeeded"


def test_cancel_running_job_transitions_to_cancelled(client_with_fresh_manager):
    blocker = threading.Event()

    def _block(**kwargs):
        blocker.wait(timeout=3.0)
        return _fake_hub_ok(**kwargs)

    patches = [
        mock.patch("huggingface_hub.hf_hub_download", side_effect=_block),
        mock.patch("huggingface_hub.snapshot_download", side_effect=_block),
        mock.patch("chayuan.server.model_registry.local_index.scan_once"),
        mock.patch(
            "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
            return_value={},
        ),
    ]
    for p in patches:
        p.start()
    try:
        post = client_with_fresh_manager.post(
            "/admin/models/install_release", json={"release": "lite"})
        job_id = post.json()["data"]["id"]
        time.sleep(0.1)  # 让任务进 RUNNING
        resp = client_with_fresh_manager.post(
            f"/admin/models/install_release/{job_id}/cancel")
        assert resp.status_code == 200
        blocker.set()
        _wait_terminal(client_with_fresh_manager)
        final = client_with_fresh_manager.get(
            f"/admin/models/install_release/{job_id}")
        assert final.json()["data"]["status"] == "cancelled"
    finally:
        blocker.set()
        for p in patches:
            p.stop()
