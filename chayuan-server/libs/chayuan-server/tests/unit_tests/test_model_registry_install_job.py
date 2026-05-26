"""``InstallJobManager`` 行为测试 — 用 monkeypatch 替 ``huggingface_hub``
下载函数,不真起网络请求。

2026-05-15 重写:之前测试基于 ``subprocess.Popen`` mock,因为旧实现是
``sys.executable -m chayuan_packaging`` 子进程模式;新实现已改成 in-process
直接调 ``huggingface_hub.hf_hub_download / snapshot_download``,测试 mock 点
随之上移。
"""
from __future__ import annotations

import threading
import time
from typing import Any
from unittest import mock

import pytest

from chayuan.server.model_registry.install_job import (
    InstallJob,
    InstallJobManager,
    JobStatus,
    get_install_manager,
)


# ─────────────────────── 工具 ───────────────────────


@pytest.fixture(autouse=True)
def _isolate_chayuan_root(tmp_path, monkeypatch):
    """让 install_job 写入临时 CHAYUAN_ROOT/models/bundled/,不污染真实数据。"""
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    yield


def _wait_done(job: InstallJob, timeout: float = 5.0) -> None:
    """阻塞等任务进入终态(succeeded / failed / cancelled)。"""
    deadline = time.time() + timeout
    while job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        if time.time() > deadline:
            raise TimeoutError(
                f"job {job.id} 未在 {timeout}s 内结束;status={job.status.value}"
            )
        time.sleep(0.01)


def _fake_hub_ok(**kwargs: Any) -> str:
    """模拟下载成功:在 local_dir 下 touch 一个假文件。"""
    from pathlib import Path
    local_dir = Path(kwargs.get("local_dir") or kwargs.get("cache_dir") or "/tmp")
    local_dir.mkdir(parents=True, exist_ok=True)
    fname = kwargs.get("filename") or "snapshot.touch"
    p = local_dir / fname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return str(p)


# ─────────────────────── 输入校验 ───────────────────────


def test_rejects_unknown_release():
    mgr = InstallJobManager()
    with pytest.raises(ValueError, match="unsupported release"):
        mgr.start("super-pro")


def test_rejects_unknown_mirror():
    mgr = InstallJobManager()
    with pytest.raises(ValueError, match="unknown mirror"):
        mgr.start("lite", mirror="evilcdn.example.com")


# ─────────────────────── 成功路径 ───────────────────────


def test_successful_run_transitions_to_succeeded():
    mgr = InstallJobManager()
    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "chayuan.server.model_registry.local_index.scan_once"
    ), mock.patch(
        "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
        return_value={"chat": "fake"},
    ):
        job = mgr.start("lite", mirror="hf-mirror")
        _wait_done(job)

    assert job.status == JobStatus.SUCCEEDED
    assert job.exit_code == 0
    assert job.step == "done"
    assert job.progress_pct == pytest.approx(100.0)
    assert job.files_done == job.files_total
    assert job.files_total > 0  # lite release 不会是空清单


def test_progress_fields_grow_during_download():
    """逐个文件下载时 files_done / progress_pct 应单调递增。"""
    mgr = InstallJobManager()
    seen_progress: list[tuple[int, float]] = []

    def _record(*args, **kwargs):
        # 每次 hub call 时记下当前 progress
        for j in mgr.list():
            seen_progress.append((j.files_done, j.progress_pct))
        return _fake_hub_ok(**kwargs)

    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_record
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_record
    ), mock.patch(
        "chayuan.server.model_registry.local_index.scan_once"
    ), mock.patch(
        "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
        return_value={},
    ):
        job = mgr.start("lite", mirror="hf-mirror")
        _wait_done(job)

    # 至少有一次 progress 中间快照(progress_pct 介于 0 和 100 之间)
    intermediate = [pct for done, pct in seen_progress if 0 < pct < 100]
    assert intermediate, f"未观察到中间进度;snapshots={seen_progress}"
    # 单调递增(允许相等,但不应回退)
    pcts = [pct for _, pct in seen_progress]
    assert pcts == sorted(pcts), f"progress_pct 非单调递增:{pcts}"


# ─────────────────────── 失败路径 ───────────────────────


def test_hub_failure_transitions_to_failed():
    mgr = InstallJobManager()
    with mock.patch(
        "huggingface_hub.hf_hub_download",
        side_effect=RuntimeError("network down"),
    ), mock.patch(
        "huggingface_hub.snapshot_download",
        side_effect=RuntimeError("network down"),
    ):
        job = mgr.start("lite", mirror="hf-mirror")
        _wait_done(job)

    assert job.status == JobStatus.FAILED
    assert job.exit_code == 1
    assert any("network down" in line for line in job.log_tail)
    # 失败时 progress 应停在中途(可能 0% 也可能 25%,但 < 100%)
    assert job.progress_pct < 100.0


# ─────────────────────── 取消路径 ───────────────────────


def test_cancel_running_job():
    """让 hf_hub_download 阻塞,触发 cancel 后任务应进入 CANCELLED。"""
    mgr = InstallJobManager()
    blocker = threading.Event()

    def _blocking_download(**kwargs):
        # 第一个文件触发阻塞,让外面发 cancel
        blocker.wait(timeout=3.0)
        return _fake_hub_ok(**kwargs)

    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_blocking_download
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_blocking_download
    ):
        job = mgr.start("lite", mirror="hf-mirror")
        # 给后台线程一点时间进入 RUNNING
        deadline = time.time() + 1.0
        while job.status != JobStatus.RUNNING and time.time() < deadline:
            time.sleep(0.01)
        assert job.status == JobStatus.RUNNING

        result = mgr.cancel(job.id)
        assert result is job
        # 解锁,让 hub call 返回;循环再下一轮会 check cancel
        blocker.set()
        _wait_done(job)

    assert job.status == JobStatus.CANCELLED
    assert job.exit_code == -1
    assert any("取消" in line for line in job.log_tail)


def test_cancel_unknown_job_raises():
    mgr = InstallJobManager()
    with pytest.raises(KeyError):
        mgr.cancel("does-not-exist")


def test_cancel_terminal_job_is_noop():
    mgr = InstallJobManager()
    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "chayuan.server.model_registry.local_index.scan_once"
    ), mock.patch(
        "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
        return_value={},
    ):
        job = mgr.start("lite", mirror="hf-mirror")
        _wait_done(job)
    assert job.status == JobStatus.SUCCEEDED

    # 对终态 job 再 cancel,应原状态返回,不报错
    result = mgr.cancel(job.id)
    assert result.status == JobStatus.SUCCEEDED


# ─────────────────────── 并发 / 重复 ───────────────────────


def test_rejects_duplicate_running_release():
    mgr = InstallJobManager()
    blocker = threading.Event()

    def _block(**kwargs):
        blocker.wait(timeout=3.0)
        return _fake_hub_ok(**kwargs)

    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_block
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_block
    ):
        first = mgr.start("lite", mirror="hf-mirror")
        # 等任务真正进入 RUNNING
        deadline = time.time() + 1.0
        while first.status != JobStatus.RUNNING and time.time() < deadline:
            time.sleep(0.01)
        with pytest.raises(RuntimeError, match="已有运行中任务"):
            mgr.start("lite", mirror="hf-mirror")
        # 收尾
        blocker.set()
        _wait_done(first)


def test_can_start_after_previous_terminal():
    """前一任务结束后,同 release 可以再开新任务。"""
    mgr = InstallJobManager()
    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "chayuan.server.model_registry.local_index.scan_once"
    ), mock.patch(
        "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
        return_value={},
    ):
        first = mgr.start("lite", mirror="hf-mirror")
        _wait_done(first)
        assert first.status == JobStatus.SUCCEEDED
        second = mgr.start("lite", mirror="hf-mirror")
        _wait_done(second)
        assert second.status == JobStatus.SUCCEEDED
        assert first.id != second.id


# ─────────────────────── list / get / mirror ───────────────────────


def test_list_returns_jobs_newest_first():
    mgr = InstallJobManager()
    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "chayuan.server.model_registry.local_index.scan_once"
    ), mock.patch(
        "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
        return_value={},
    ):
        a = mgr.start("lite", mirror="hf-mirror")
        _wait_done(a)
        time.sleep(0.02)  # 让 started_at 有差
        b = mgr.start("standard", mirror="hf-mirror")
        _wait_done(b)

    items = mgr.list()
    assert [j.id for j in items][:2] == [b.id, a.id]


def test_mirror_translated_to_hf_endpoint_env(monkeypatch):
    """mirror=hf-mirror 应设 HF_ENDPOINT=https://hf-mirror.com。"""
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    mgr = InstallJobManager()

    seen_endpoint: list[str] = []

    def _capture(**kwargs):
        import os as _os
        seen_endpoint.append(_os.environ.get("HF_ENDPOINT", ""))
        return _fake_hub_ok(**kwargs)

    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_capture
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_capture
    ), mock.patch(
        "chayuan.server.model_registry.local_index.scan_once"
    ), mock.patch(
        "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
        return_value={},
    ):
        job = mgr.start("lite", mirror="hf-mirror")
        _wait_done(job)

    assert all(e == "https://hf-mirror.com" for e in seen_endpoint), seen_endpoint


# ─────────────────────── to_dict 输出 ───────────────────────


def test_to_dict_includes_progress_fields():
    mgr = InstallJobManager()
    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "chayuan.server.model_registry.local_index.scan_once"
    ), mock.patch(
        "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
        return_value={},
    ):
        job = mgr.start("lite", mirror="hf-mirror")
        _wait_done(job)

    d = job.to_dict()
    for k in (
        "id", "release", "mirror", "status", "step",
        "started_at", "ended_at", "exit_code",
        "progress_pct", "progress_message", "files_total", "files_done",
        "log_tail",
    ):
        assert k in d, f"to_dict 缺字段 {k}"
    assert d["status"] == "succeeded"
    assert d["progress_pct"] == 100.0
    assert d["files_total"] > 0
    assert d["files_done"] == d["files_total"]


def test_to_dict_is_json_safe():
    import json
    mgr = InstallJobManager()
    with mock.patch(
        "huggingface_hub.hf_hub_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "huggingface_hub.snapshot_download", side_effect=_fake_hub_ok
    ), mock.patch(
        "chayuan.server.model_registry.local_index.scan_once"
    ), mock.patch(
        "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
        return_value={},
    ):
        job = mgr.start("lite", mirror="hf-mirror")
        _wait_done(job)
    # 不抛
    json.dumps(job.to_dict())


# ─────────────────────── 单例 ───────────────────────


def test_get_install_manager_returns_singleton():
    a = get_install_manager()
    b = get_install_manager()
    assert a is b
