"""``ServiceInstallJobManager`` / ``service_status`` 行为测试。

用 monkeypatch 替下载/解压,不真起网络请求。覆盖:
  * 资产名解析(平台 ↔ release zip schema)
  * 输入校验(未知 engine / 不支持平台)
  * 成功路径(下载 → 解压 → 校验 → succeeded)
  * 失败路径(HTTP 非 200 / zip 内无 binary)
  * 取消
  * 重复任务拒绝
  * service_status 就绪/缺失分支
"""
from __future__ import annotations

import io
import time
import zipfile
from typing import Any
from unittest import mock

import pytest

from chayuan.server.runtime import install_service as svc
from chayuan.server.runtime.install_service import (
    ServiceInstallJob,
    ServiceInstallJobManager,
    ServiceJobStatus,
    get_install_service_manager,
)


# ─────────────────────── 工具 ───────────────────────


def _wait_done(job: ServiceInstallJob, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while job.status in (ServiceJobStatus.QUEUED, ServiceJobStatus.RUNNING):
        if time.time() > deadline:
            raise TimeoutError(
                f"job {job.id} 未在 {timeout}s 内结束;status={job.status.value}"
            )
        time.sleep(0.01)


def _make_zip(*names: str) -> bytes:
    """构造一个含指定文件名的内存 zip。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, b"\x00" * 16)
    return buf.getvalue()


@pytest.fixture()
def services_root(tmp_path, monkeypatch):
    """把下载目标 services 根重定向到临时目录。"""
    root = tmp_path / "services"
    root.mkdir()
    monkeypatch.setattr(svc, "_services_root", lambda: root)
    return root


# ─────────────────────── 资产名解析 ───────────────────────


def test_llama_asset_new_schema_win_x64():
    # b9174 ≥ 5400 → 新 schema 单一 -cpu-x64.zip
    assert svc._llama_asset_for("win-x64", "b9174") == "llama-b9174-bin-win-cpu-x64.zip"
    assert (
        svc._llama_asset_for("win-x64-noavx", "b9174")
        == "llama-b9174-bin-win-cpu-x64.zip"
    )


def test_llama_asset_old_schema_win_x64():
    assert svc._llama_asset_for("win-x64", "b4404") == "llama-b4404-bin-win-avx2-x64.zip"


def test_llama_asset_linux_macos():
    assert svc._llama_asset_for("linux-x64", "b9174") == "llama-b9174-bin-ubuntu-x64.zip"
    assert (
        svc._llama_asset_for("macos-arm64", "b9174")
        == "llama-b9174-bin-macos-arm64.zip"
    )


def test_llama_asset_linux_arm64_unavailable():
    assert svc._llama_asset_for("linux-arm64", "b9174") is None


def test_whisper_asset_only_win_x64():
    assert svc._whisper_asset_for("win-x64", "v1.7.6") == "whisper-bin-x64.zip"
    # 非 win-x64 平台 upstream 没预编译
    assert svc._whisper_asset_for("linux-x64", "v1.7.6") is None
    assert svc._whisper_asset_for("macos-arm64", "v1.7.6") is None
    assert svc._whisper_asset_for("win-arm64", "v1.7.6") is None


# ─────────────────────── 输入校验 ───────────────────────


def test_rejects_unknown_engine():
    mgr = ServiceInstallJobManager()
    with pytest.raises(ValueError, match="unsupported engine"):
        mgr.start(engine="ollama-server")


def test_rejects_whisper_on_unsupported_platform():
    mgr = ServiceInstallJobManager()
    with pytest.raises(ValueError, match="whisper-server"):
        mgr.start(engine="whisper-server", platform="linux-x64")


def test_rejects_llama_on_unmapped_platform():
    mgr = ServiceInstallJobManager()
    with pytest.raises(ValueError, match="无可下载"):
        mgr.start(engine="llama-server", platform="linux-arm64")


# ─────────────────────── 成功路径 ───────────────────────


def test_successful_download_extracts_binary(services_root):
    mgr = ServiceInstallJobManager()
    fake_zip = _make_zip("llama-server.exe", "ggml.dll", "LICENSE")

    with mock.patch.object(
        mgr, "_resolve_mirror", return_value=""
    ), mock.patch.object(
        mgr, "_download", return_value=fake_zip
    ):
        job = mgr.start(engine="llama-server", platform="win-x64")
        _wait_done(job)

    assert job.status == ServiceJobStatus.SUCCEEDED
    assert job.exit_code == 0
    assert job.step == "done"
    assert job.progress_pct == pytest.approx(100.0)
    # binary + dll 落地,VERSION 写好
    dest = services_root / "llama-server" / "win-x64"
    assert (dest / "llama-server.exe").is_file()
    assert (dest / "ggml.dll").is_file()
    assert (dest / "VERSION").is_file()


def test_to_dict_is_json_safe(services_root):
    import json

    mgr = ServiceInstallJobManager()
    with mock.patch.object(
        mgr, "_resolve_mirror", return_value="https://gh-proxy.com/"
    ), mock.patch.object(
        mgr, "_download", return_value=_make_zip("llama-server.exe")
    ):
        job = mgr.start(engine="llama-server", platform="win-x64")
        _wait_done(job)
    d = job.to_dict()
    for k in (
        "id", "engine", "platform", "mirror", "status", "step",
        "progress_pct", "progress_message", "target_dir", "log_tail",
    ):
        assert k in d, f"to_dict 缺字段 {k}"
    json.dumps(d)  # 不抛


# ─────────────────────── 失败路径 ───────────────────────


def test_zip_without_binary_fails(services_root):
    mgr = ServiceInstallJobManager()
    # zip 里只有 LICENSE,没有主 binary / 共享库
    with mock.patch.object(
        mgr, "_resolve_mirror", return_value=""
    ), mock.patch.object(
        mgr, "_download", return_value=_make_zip("LICENSE", "README.md")
    ):
        job = mgr.start(engine="llama-server", platform="win-x64")
        _wait_done(job)

    assert job.status == ServiceJobStatus.FAILED
    assert job.exit_code == 1
    assert any("未找到" in line for line in job.log_tail)


def test_download_error_fails(services_root):
    mgr = ServiceInstallJobManager()

    def _boom(*a: Any, **k: Any):
        raise RuntimeError("下载返回 HTTP 404")

    with mock.patch.object(
        mgr, "_resolve_mirror", return_value=""
    ), mock.patch.object(mgr, "_download", side_effect=_boom):
        job = mgr.start(engine="llama-server", platform="win-x64")
        _wait_done(job)

    assert job.status == ServiceJobStatus.FAILED
    assert any("404" in line for line in job.log_tail)


# ─────────────────────── 取消 ───────────────────────


def test_cancel_running_job(services_root):
    import threading

    mgr = ServiceInstallJobManager()
    blocker = threading.Event()

    def _blocking_download(*a: Any, **k: Any) -> bytes:
        blocker.wait(timeout=3.0)
        return _make_zip("llama-server.exe")

    with mock.patch.object(
        mgr, "_resolve_mirror", return_value=""
    ), mock.patch.object(mgr, "_download", side_effect=_blocking_download):
        job = mgr.start(engine="llama-server", platform="win-x64")
        deadline = time.time() + 1.0
        while job.status != ServiceJobStatus.RUNNING and time.time() < deadline:
            time.sleep(0.01)
        assert job.status == ServiceJobStatus.RUNNING
        mgr.cancel(job.id)
        blocker.set()
        _wait_done(job)

    assert job.status == ServiceJobStatus.CANCELLED
    assert job.exit_code == -1


def test_cancel_unknown_job_raises():
    mgr = ServiceInstallJobManager()
    with pytest.raises(KeyError):
        mgr.cancel("does-not-exist")


# ─────────────────────── 重复 / 单例 ───────────────────────


def test_rejects_duplicate_running_engine(services_root):
    import threading

    mgr = ServiceInstallJobManager()
    blocker = threading.Event()

    def _block(*a: Any, **k: Any) -> bytes:
        blocker.wait(timeout=3.0)
        return _make_zip("llama-server.exe")

    with mock.patch.object(
        mgr, "_resolve_mirror", return_value=""
    ), mock.patch.object(mgr, "_download", side_effect=_block):
        first = mgr.start(engine="llama-server", platform="win-x64")
        deadline = time.time() + 1.0
        while first.status != ServiceJobStatus.RUNNING and time.time() < deadline:
            time.sleep(0.01)
        with pytest.raises(RuntimeError, match="已有运行中任务"):
            mgr.start(engine="llama-server", platform="win-x64")
        blocker.set()
        _wait_done(first)


def test_get_install_service_manager_singleton():
    a = get_install_service_manager()
    b = get_install_service_manager()
    assert a is b


# ─────────────────────── service_status ───────────────────────


def test_service_status_reports_present(monkeypatch, tmp_path):
    """services 目录里有 llama-server binary → present=True。"""
    root = tmp_path / "services"
    (root / "llama-server" / "win-x64").mkdir(parents=True)
    (root / "llama-server" / "win-x64" / "llama-server.exe").write_bytes(b"x")
    (root / "llama-server" / "win-x64" / "VERSION").write_text("b9174\n2026-05-20\n")

    monkeypatch.setattr(svc, "_current_platform", lambda: "win-x64")
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime._default_install_services_dirs",
        lambda: [root],
    )
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime._platform_subdir_candidates",
        lambda: ["win-x64"],
    )

    status = svc.service_status()
    assert status["platform"] == "win-x64"
    llama = next(e for e in status["engines"] if e["engine"] == "llama-server")
    assert llama["present"] is True
    assert llama["version"] == "b9174"


def test_service_status_reports_missing_downloadable(monkeypatch, tmp_path):
    """缺 binary 但平台支持下载 → present=False, downloadable=True。"""
    root = tmp_path / "services"
    root.mkdir()
    monkeypatch.setattr(svc, "_current_platform", lambda: "win-x64")
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime._default_install_services_dirs",
        lambda: [root],
    )
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime._platform_subdir_candidates",
        lambda: ["win-x64"],
    )

    status = svc.service_status()
    llama = next(e for e in status["engines"] if e["engine"] == "llama-server")
    assert llama["present"] is False
    assert llama["downloadable"] is True
    assert llama["reason"]


def test_service_status_whisper_not_downloadable_on_linux(monkeypatch, tmp_path):
    """Linux 平台 whisper-server 无预编译 → downloadable=False。"""
    root = tmp_path / "services"
    root.mkdir()
    monkeypatch.setattr(svc, "_current_platform", lambda: "linux-x64")
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime._default_install_services_dirs",
        lambda: [root],
    )
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime._platform_subdir_candidates",
        lambda: ["linux-x64"],
    )

    status = svc.service_status()
    whisper = next(e for e in status["engines"] if e["engine"] == "whisper-server")
    assert whisper["present"] is False
    assert whisper["downloadable"] is False
    assert "docker" in (whisper["reason"] or "")
