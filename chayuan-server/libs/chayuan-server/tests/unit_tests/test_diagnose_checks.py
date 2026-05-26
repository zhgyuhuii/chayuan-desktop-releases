"""diagnose.checks 单元测试。

每个 check 是纯函数,外部依赖通过 monkeypatch 模拟。
"""
from __future__ import annotations

import pytest

from chayuan.server.diagnose.types import DiagnoseCheck, DiagnoseReport


def test_check_sidecar_healthz_returns_ok():
    """sidecar.healthz check:模块跑起来就 ok"""
    from chayuan.server.diagnose.checks import check_sidecar_healthz

    c = check_sidecar_healthz()
    assert isinstance(c, DiagnoseCheck)
    assert c.name == "sidecar.healthz"
    assert c.ok is True
    assert c.severity == "ok"


def test_diagnose_check_dataclass_defaults():
    """DiagnoseCheck context 默认 None,severity 必填"""
    c = DiagnoseCheck(name="x", ok=True, severity="ok", detail="d")
    assert c.context is None


def test_check_vendor_llama_server_binary_present(tmp_path, monkeypatch):
    """vendor 二进制存在时 → ok,detail 含路径 + version"""
    from chayuan.server.model_registry import local_runtime
    from chayuan.server.diagnose.checks import check_vendor_llama_server_binary

    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    exe = services / "llama-server.exe"
    exe.write_bytes(b"x" * 1024)
    (services / "VERSION").write_text("b4404\n2026-05-15\n")
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    c = check_vendor_llama_server_binary()
    assert c.name == "vendor.llama-server.binary"
    assert c.ok is True
    assert c.severity == "ok"
    assert "b4404" in c.detail
    assert "llama-server.exe" in c.detail


def test_check_vendor_llama_server_binary_missing(tmp_path, monkeypatch):
    """vendor 二进制缺失时 → fail"""
    from chayuan.server.model_registry import local_runtime
    from chayuan.server.diagnose.checks import check_vendor_llama_server_binary

    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "nothing"])
    c = check_vendor_llama_server_binary()
    assert c.ok is False
    assert c.severity == "fail"
    assert "未找到" in c.detail or "not found" in c.detail.lower()


def test_check_bundled_models_chat_present(tmp_path, monkeypatch):
    """bundled_models 至少 1 个 chat .gguf → ok"""
    from chayuan.server.diagnose.checks import check_bundled_models_chat

    fake_entries = [
        type("Entry", (), {"capability": "chat", "model_id": "qwen3-4b", "path": str(tmp_path / "a.gguf")})()
    ]
    fake_index = type("Idx", (), {"by_capability": lambda self, cap: fake_entries if cap == "chat" else []})()
    monkeypatch.setattr(
        "chayuan.server.diagnose.checks.get_local_index",
        lambda **kw: fake_index,
    )

    c = check_bundled_models_chat()
    assert c.name == "vendor.bundled_models.chat"
    assert c.ok is True
    assert "1" in c.detail or "qwen3" in c.detail.lower()


def test_check_bundled_models_chat_empty(monkeypatch):
    """bundled_models 0 个 chat 模型 → fail"""
    from chayuan.server.diagnose.checks import check_bundled_models_chat

    fake_index = type("Idx", (), {"by_capability": lambda self, cap: []})()
    monkeypatch.setattr(
        "chayuan.server.diagnose.checks.get_local_index",
        lambda **kw: fake_index,
    )

    c = check_bundled_models_chat()
    assert c.ok is False
    assert c.severity == "fail"


def test_check_chayuan_root_writable_ok(tmp_path):
    from chayuan.server.diagnose.checks import check_chayuan_root_writable
    c = check_chayuan_root_writable(tmp_path)
    assert c.name == "chayuan_root.writable"
    assert c.ok is True
    assert c.severity == "ok"
    # 测试文件应被清理
    assert not (tmp_path / ".diagnose-test").exists()


def test_check_chayuan_root_writable_not_dir(tmp_path):
    from chayuan.server.diagnose.checks import check_chayuan_root_writable
    c = check_chayuan_root_writable(tmp_path / "does-not-exist")
    assert c.ok is False
    assert c.severity == "fail"


def test_check_runtime_json_writable_ok(tmp_path):
    from chayuan.server.diagnose.checks import check_runtime_json_writable
    c = check_runtime_json_writable(tmp_path / "runtime.json")
    assert c.ok is True


def test_check_local_runtime_yaml_readable_missing_is_ok(tmp_path):
    """文件不存在等于"用默认值",不算 fail"""
    from chayuan.server.diagnose.checks import check_local_runtime_yaml_readable
    c = check_local_runtime_yaml_readable(tmp_path / "absent.yaml")
    assert c.ok is True
    assert c.severity == "ok"
    assert "不存在" in c.detail or "默认" in c.detail


def test_check_local_runtime_yaml_readable_present_ok(tmp_path):
    from chayuan.server.diagnose.checks import check_local_runtime_yaml_readable
    yaml_path = tmp_path / "lr.yaml"
    yaml_path.write_text("port: 62582\n", encoding="utf-8")
    c = check_local_runtime_yaml_readable(yaml_path)
    assert c.ok is True


def test_check_local_runtime_yaml_readable_corrupt_warn(tmp_path):
    from chayuan.server.diagnose.checks import check_local_runtime_yaml_readable
    yaml_path = tmp_path / "lr.yaml"
    yaml_path.write_bytes(b"\x00\x01\xff garbage")
    c = check_local_runtime_yaml_readable(yaml_path)
    assert c.ok is False or c.severity == "warn"


def test_check_disk_free_gb(tmp_path, monkeypatch):
    """正常磁盘 → ok;< 2 GB → warn;< 500 MB → fail"""
    from chayuan.server.diagnose import checks as _checks

    # 用 monkeypatch 替 shutil.disk_usage
    def fake_du(_path):
        return type("DU", (), {"total": 100 * 2**30, "used": 0, "free": 50 * 2**30})()

    monkeypatch.setattr(_checks.shutil, "disk_usage", fake_du)
    c = _checks.check_disk_free_gb(tmp_path)
    assert c.ok is True

    def low_du(_path):
        return type("DU", (), {"total": 100 * 2**30, "used": 0, "free": 1 * 2**30})()
    monkeypatch.setattr(_checks.shutil, "disk_usage", low_du)
    c = _checks.check_disk_free_gb(tmp_path)
    assert c.severity == "warn"

    def crit_du(_path):
        return type("DU", (), {"total": 100 * 2**30, "used": 0, "free": 200 * 2**20})()
    monkeypatch.setattr(_checks.shutil, "disk_usage", crit_du)
    c = _checks.check_disk_free_gb(tmp_path)
    assert c.severity == "fail"


def test_check_port_free(monkeypatch):
    """62582 没被占 → ok"""
    from chayuan.server.diagnose import checks as _checks

    # 模拟 psutil.net_connections 返回空 (无监听)
    fake_conns: list = []
    monkeypatch.setattr(_checks.psutil, "net_connections", lambda kind: fake_conns)

    c = _checks.check_port_62582()
    assert c.ok is True
    assert "空闲" in c.detail


def test_check_port_occupied_other_process(monkeypatch):
    """62582 被其它进程占 → warn"""
    from chayuan.server.diagnose import checks as _checks

    # 模拟一个 LISTEN 在 62582 的连接,pid 不是当前进程
    laddr = type("Addr", (), {"ip": "127.0.0.1", "port": 62582})()
    conn = type("Conn", (), {"status": "LISTEN", "laddr": laddr, "pid": 99999})()
    monkeypatch.setattr(_checks.psutil, "net_connections", lambda kind: [conn])

    # 模拟 Process(99999).name() = 'chrome.exe'
    proc = type("Proc", (), {"name": lambda self: "chrome.exe"})()
    monkeypatch.setattr(_checks.psutil, "Process", lambda pid: proc)

    c = _checks.check_port_62582()
    assert c.ok is True  # warn 算 ok
    assert c.severity == "warn"
    assert "chrome" in c.detail.lower() or "99999" in c.detail


def test_check_chayuan_server_process():
    """当前进程信息总能拿到 → ok"""
    from chayuan.server.diagnose.checks import check_chayuan_server_process

    c = check_chayuan_server_process()
    assert c.name == "chayuan_server.process"
    assert c.ok is True
    assert "pid" in c.detail.lower() or str(__import__("os").getpid()) in c.detail


def _patch_registry_status(monkeypatch, statuses_by_cap):
    """工具:让 get_registry().get(cap).status 返回指定值"""
    from chayuan.server.model_registry import local_runtime_registry as lrr
    fake_managers = {cap: type("Mgr", (), {"status": st})() for cap, st in statuses_by_cap.items()}
    fake_registry = type("Reg", (), {"get": lambda self, cap: fake_managers[cap]})()
    monkeypatch.setattr(lrr, "_registry_singleton", fake_registry)


def test_check_runtime_llama_status_for_chat_ready(monkeypatch):
    from chayuan.server.diagnose.checks import check_runtime_llama_status_for
    from chayuan.server.model_registry import local_runtime as lr
    _patch_registry_status(monkeypatch, {
        "chat": lr.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62582", pid=1, model_id="qwen"),
    })
    c = check_runtime_llama_status_for("chat")
    assert c.name == "runtime.llama.chat.status"
    assert c.severity == "ok"


def test_check_runtime_llama_status_for_embedding_failed(monkeypatch):
    from chayuan.server.diagnose.checks import check_runtime_llama_status_for
    from chayuan.server.model_registry import local_runtime as lr
    _patch_registry_status(monkeypatch, {
        "embedding": lr.RuntimeStatus(state="failed", last_error="model missing"),
    })
    c = check_runtime_llama_status_for("embedding")
    assert c.name == "runtime.llama.embedding.status"
    assert c.severity == "fail"
    assert "model missing" in c.detail


def test_check_runtime_llama_status_for_rerank_stopped(monkeypatch):
    from chayuan.server.diagnose.checks import check_runtime_llama_status_for
    from chayuan.server.model_registry import local_runtime as lr
    _patch_registry_status(monkeypatch, {
        "rerank": lr.RuntimeStatus(state="stopped"),
    })
    c = check_runtime_llama_status_for("rerank")
    assert c.name == "runtime.llama.rerank.status"
    assert c.severity == "warn"


def test_run_all_checks_returns_report_with_summary(tmp_path, monkeypatch):
    """聚合应返 DiagnoseReport + summary 计数正确"""
    from chayuan.server.diagnose import run_all_checks, DiagnoseReport
    from chayuan.server.model_registry import local_runtime as lr

    # 让 chayuan_root 指向 tmp_path,所有 check 都不会因找不到路径炸
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", str(tmp_path))

    # 让 5 个 capability 的 registry status 均为 stopped (warn)
    fake_status = lr.RuntimeStatus(state="stopped")
    _patch_registry_status(monkeypatch, {
        "chat": fake_status,
        "embedding": fake_status,
        "rerank": fake_status,
        "asr": fake_status,
        "image-embedding": fake_status,
    })

    # vendor 二进制肯定找不到 → fail
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "nothing"])

    report = run_all_checks()
    assert isinstance(report, DiagnoseReport)
    assert len(report.checks) == 15  # Plan 3D: + image-embedding;+ vendor.platform.detected
    # summary 计数加和等于 15
    s = report.summary
    assert s["ok"] + s["warn"] + s["fail"] == 15


def test_check_runtime_llama_status_for_asr_ready(monkeypatch):
    from chayuan.server.diagnose.checks import check_runtime_llama_status_for
    from chayuan.server.model_registry import local_runtime as lr
    _patch_registry_status(monkeypatch, {
        "asr": lr.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62585", pid=10, model_id="whisper-tiny"),
    })
    c = check_runtime_llama_status_for("asr")
    assert c.name == "runtime.llama.asr.status"
    assert c.severity == "ok"
    assert "whisper-tiny" in c.detail


def test_check_runtime_llama_status_for_image_embedding_ready(monkeypatch):
    from chayuan.server.diagnose.checks import check_runtime_llama_status_for
    from chayuan.server.model_registry import local_runtime as lr
    _patch_registry_status(monkeypatch, {
        "image-embedding": lr.RuntimeStatus(
            state="ready", endpoint="http://127.0.0.1:62586", pid=88, model_id="siglip2-base"
        ),
    })
    c = check_runtime_llama_status_for("image-embedding")
    assert c.name == "runtime.llama.image-embedding.status"
    assert c.severity == "ok"
    assert "siglip2-base" in c.detail


def test_check_vendor_platform_detected_includes_candidates_and_override(monkeypatch):
    """vendor.platform.detected 应含 candidates / override / resolved 三块 context。"""
    from chayuan.server.diagnose import checks as _checks

    monkeypatch.setenv("CHAYUAN_VENDOR_PLATFORM", "win-x64-noavx")
    c = _checks.check_vendor_platform_detected()
    assert c.name == "vendor.platform.detected"
    assert c.context["override"] == "win-x64-noavx"
    assert c.context["candidates"] == ["win-x64-noavx"]
    assert "llama" in c.context["resolved"]
    assert "whisper" in c.context["resolved"]


def test_check_vendor_platform_detected_no_override(monkeypatch):
    """没 env 时 candidates 跟 OS 自动检测一致。"""
    from chayuan.server.diagnose import checks as _checks
    from chayuan.server.model_registry.local_runtime import _platform_subdir_candidates

    monkeypatch.delenv("CHAYUAN_VENDOR_PLATFORM", raising=False)
    c = _checks.check_vendor_platform_detected()
    assert c.context["override"] is None
    assert c.context["candidates"] == _platform_subdir_candidates()


def test_run_all_checks_does_not_raise_when_check_raises(tmp_path, monkeypatch):
    """单个 check 抛异常不能影响其它"""
    from chayuan.server.diagnose import run_all_checks
    from chayuan.server.diagnose import checks as _checks

    def boom():
        raise RuntimeError("explode")

    monkeypatch.setattr(_checks, "check_sidecar_healthz", boom)
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", str(tmp_path))

    report = run_all_checks()
    # 还能跑出来,sidecar.healthz 那项变成 fail
    names = [c.name for c in report.checks]
    assert "sidecar.healthz" in names
    sidecar_c = next(c for c in report.checks if c.name == "sidecar.healthz")
    assert sidecar_c.severity == "fail"
    assert "explode" in sidecar_c.detail.lower() or "runtimeerror" in sidecar_c.detail.lower()
