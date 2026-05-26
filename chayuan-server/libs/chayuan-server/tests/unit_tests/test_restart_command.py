"""`chayuan restart` 命令相关的核心原语单测。

覆盖：
1. `restart_role`：stop 一个活着的 dummy 子进程后，spawn 新进程
2. `restart_role`：role 合法性校验
3. `_spawn_service_detached`：能拿到新 pid；Popen 不阻塞当前进程
4. `restart_all`：无 runtime meta 时回落到逐 role（通过打桩 restart_role 验证）
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def isolated_runtime_meta(tmp_path, monkeypatch):
    meta = tmp_path / ".chayuan_runtime.json"
    monkeypatch.setenv("CHAYUAN_RUNTIME_META", str(meta))
    yield meta


def _spawn_dummy(sleep_sec: int = 30) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({sleep_sec})"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# restart_role 参数校验
# ---------------------------------------------------------------------------

def test_restart_role_rejects_unknown_role(isolated_runtime_meta):
    from chayuan.server.config_panel.restart import restart_role
    with pytest.raises(ValueError) as exc:
        restart_role("not_a_role")
    assert "role" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# _spawn_service_detached：不依赖 API server 本身，用 monkeypatch 替换 target
# ---------------------------------------------------------------------------

def test_spawn_service_detached_returns_pid(isolated_runtime_meta, monkeypatch, tmp_path):
    """把 run_api_server 替换成 short-lived sleep，验证 spawn 能 detach 并返回 pid。"""
    from chayuan.server.config_panel import restart as r

    # 用一个短命 shim 函数替换 role→target 映射，避免真起 uvicorn
    monkeypatch.setattr(
        r, "_ROLE_TO_TARGET",
        {"api": "_no_such__will_be_replaced"},
    )
    # 由于 _spawn_service_detached 是用 `python -c "from chayuan.startup import X; X()"`
    # 而我们替换的 target 不存在，子进程会**启动失败立即退出**。这仍然能验证：
    #   - Popen 本身是非阻塞的；
    #   - 返回的 pid 在短时间内可见；
    # 这对集成测试来说足够安全，而且不会卡测试。
    start = time.monotonic()
    pid = r._spawn_service_detached("api")
    elapsed = time.monotonic() - start
    assert pid > 0
    # 非阻塞：很快返回（<3s 就够；实际 <100ms）
    assert elapsed < 3.0
    # 等它退出
    for _ in range(30):
        from chayuan.server.config_panel.stop import _pid_alive
        if not _pid_alive(pid):
            break
        time.sleep(0.1)


def test_spawn_service_detached_rejects_bad_role(isolated_runtime_meta):
    from chayuan.server.config_panel.restart import _spawn_service_detached
    with pytest.raises(ValueError):
        _spawn_service_detached("nope")


# ---------------------------------------------------------------------------
# restart_role 完整路径：用 monkeypatch 把 target 换成一个慢 sleep
#
# 这样既能验证 stop 杀旧、又能验证 spawn 新，不起真 API server。
# ---------------------------------------------------------------------------

def test_restart_role_stops_old_then_spawns_new(
    isolated_runtime_meta, monkeypatch,
):
    from chayuan.server.config_panel import restart as r
    from chayuan.server.config_panel import stop as s
    from chayuan.server.config_panel.restart import (
        record_runtime, register_child,
    )

    # 在 meta 里注册一个活着的"旧 api pid"（实际只是个 dummy python sleep）
    record_runtime(argv=["chayuan", "start", "-a"])
    old_proc = _spawn_dummy(60)
    try:
        register_child("api", old_proc.pid, port=None, name="dummy-api")

        # 把 _ROLE_TO_TARGET 映射到一个"活得久"的 target shim；
        # 我们通过 monkeypatch 直接替换 _spawn_service_detached 来避免
        # 真去构造一个可 import 的函数。
        spawned_holder = {}

        def _fake_spawn(role):
            p = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            spawned_holder["pid"] = p.pid
            return p.pid

        monkeypatch.setattr(r, "_spawn_service_detached", _fake_spawn)

        # 端口相关函数替换：不检查端口（无业务端口可查）
        monkeypatch.setattr(s, "_port_in_use", lambda *a, **kw: False)
        monkeypatch.setattr(s, "_resolve_port_for_role", lambda role: None)

        # 调 restart_role
        result = r.restart_role("api", stop_wait_sec=0.5) if False else \
                   r.restart_role("api")
        # 旧 pid 应已死
        time.sleep(0.3)
        assert not s._pid_alive(old_proc.pid), "旧 pid 应被 stop_role 杀死"
        # 新 pid 是我们 fake spawn 的
        assert result["new_pid"] == spawned_holder["pid"]
        # children meta 被刷新
        from chayuan.server.config_panel.restart import list_children
        assert list_children().get("api", {}).get("pid") == spawned_holder["pid"]

        # 清理 fake spawn 的 dummy
        try:
            os.kill(spawned_holder["pid"], 9)
        except Exception:
            pass
    finally:
        try:
            old_proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# restart_all：无 runtime meta 时回落到逐 role
# ---------------------------------------------------------------------------

def test_restart_all_falls_back_when_no_meta(
    isolated_runtime_meta, monkeypatch,
):
    """meta 文件不存在 → trigger_restart raise → 走 per_role 分支。"""
    from chayuan.server.config_panel import restart as r

    # 确认 meta 不存在（fixture 是路径，非文件）
    if isolated_runtime_meta.exists():
        isolated_runtime_meta.unlink()

    called_roles = []

    def _fake_restart_role(role, *, force=False):
        called_roles.append(role)
        return {
            "role": role, "new_pid": 99999, "port": None,
            "listening": True, "detail": "ok",
            "stopped": {"status": "not_running", "pid": None, "detail": ""},
        }

    monkeypatch.setattr(r, "restart_role", _fake_restart_role)

    out = r.restart_all()
    assert out["mode"] == "per_role"
    assert [x["role"] for x in out["roles"]] == ["api", "webui", "config"]
    assert called_roles == ["api", "webui", "config"]


def test_restart_all_uses_trigger_when_explicit_helper(
    isolated_runtime_meta, monkeypatch,
):
    """显式 ``use_helper=True`` 时走 trigger_restart 守护脚本路径（打桩）。

    默认 use_helper=False（走逐 role，下一个测试覆盖）。
    """
    from chayuan.server.config_panel import restart as r
    from chayuan.server.config_panel.restart import record_runtime

    record_runtime(argv=["chayuan", "start", "-a"])

    monkeypatch.setattr(
        r, "trigger_restart",
        lambda delay=1.0: {
            "helper_pid": 111, "target_pid": os.getpid(),
            "argv": ["chayuan", "start", "-a"],
        },
    )
    # pre-stop：打桩避免真杀当前进程
    monkeypatch.setattr(
        "chayuan.server.config_panel.stop.stop_all",
        lambda *a, **kw: {"mocked": True},
    )

    out = r.restart_all(delay=0.5, use_helper=True)
    assert out["mode"] == "helper"
    assert out["helper_pid"] == 111
    assert out["target_pid"] == os.getpid()
    assert out["argv"] == ["chayuan", "start", "-a"]
    assert "pre_stop" in out


def test_restart_all_default_goes_per_role(
    isolated_runtime_meta, monkeypatch,
):
    """默认（use_helper=False）走逐 role；根据 argv 推断 role。"""
    from chayuan.server.config_panel import restart as r
    from chayuan.server.config_panel.restart import record_runtime

    record_runtime(argv=["cli.py", "start", "--api"])

    called = []
    monkeypatch.setattr(
        r, "restart_role",
        lambda role, *, force=False: (called.append(role) or {
            "role": role, "new_pid": 0, "port": None, "listening": True,
            "detail": "ok",
            "stopped": {"status": "not_running", "pid": None, "detail": ""},
        }),
    )
    monkeypatch.setattr(
        "chayuan.server.config_panel.stop.stop_all",
        lambda *a, **kw: {"mocked": True},
    )

    out = r.restart_all()
    assert out["mode"] == "per_role"
    assert called == ["api"]  # argv 只含 --api
    assert out["roles_planned"] == ["api"]


def test_restart_all_full_roles_without_meta(
    isolated_runtime_meta, monkeypatch,
):
    """无 meta 时起全部三个 role。"""
    from chayuan.server.config_panel import restart as r

    if isolated_runtime_meta.exists():
        isolated_runtime_meta.unlink()

    called = []
    monkeypatch.setattr(
        r, "restart_role",
        lambda role, *, force=False: (called.append(role) or {
            "role": role, "new_pid": 0, "port": None, "listening": True,
            "detail": "ok",
            "stopped": {"status": "not_running", "pid": None, "detail": ""},
        }),
    )
    monkeypatch.setattr(
        "chayuan.server.config_panel.stop.stop_all",
        lambda *a, **kw: {"mocked": True},
    )

    out = r.restart_all()
    assert out["mode"] == "per_role"
    assert called == ["api", "webui", "config"]


def test_roles_from_argv_heuristics():
    from chayuan.server.config_panel.restart import _roles_from_argv
    assert _roles_from_argv(["cli.py", "start", "-a"]) == ["api", "webui", "config"]
    assert _roles_from_argv(["cli.py", "start", "--all"]) == ["api", "webui", "config"]
    assert _roles_from_argv(["cli.py", "start", "--api"]) == ["api"]
    assert _roles_from_argv(["cli.py", "start", "-w"]) == ["webui"]
    assert _roles_from_argv(["cli.py", "start", "-c"]) == ["config"]
    assert _roles_from_argv(["cli.py", "start", "--api", "-w"]) == ["api", "webui"]
    assert _roles_from_argv([]) == ["api", "webui", "config"]
