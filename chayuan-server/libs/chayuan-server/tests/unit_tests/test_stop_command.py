"""`chayuan stop` 核心原语的单测。

覆盖：
1. ``register_child / list_children / unregister_child`` 原子读改写
2. ``_pid_alive`` 在当前进程 / 自身 / 不存在 pid 的表现
3. ``stop_role``：pid 已死 / 端口无监听时走 ``not_running`` 分支
4. ``stop_all``：无 runtime meta 时给出友好消息
5. ``status_snapshot``：汇总数据 shape

不会真的起 chayuan 服务（太重）；相反我们起一个**自有的短命子进程**
去验证 pid 层的杀进程路径，保证跨平台可靠。
"""
from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# register_child / list_children / unregister_child
# ---------------------------------------------------------------------------

def test_register_child_requires_existing_meta_file(isolated_runtime_meta):
    """文件不存在时，register_child 不应创建（会污染测试环境）。"""
    from chayuan.server.config_panel.restart import (
        list_children, register_child,
    )
    register_child("api", 12345, port=62581, name="test-api")
    # 未记录（因为没有 meta）
    assert list_children() == {}
    assert not isolated_runtime_meta.exists()


def test_register_child_roundtrip(isolated_runtime_meta):
    from chayuan.server.config_panel.restart import (
        list_children, record_runtime, register_child, unregister_child,
    )
    # 先 record_runtime 生成 meta
    record_runtime(argv=["chayuan", "start", "-a"])
    assert isolated_runtime_meta.is_file()

    register_child("api", 111, port=62581, name="API")
    register_child("config", 333, port=8502, name="Config")

    got = list_children()
    assert set(got.keys()) == {"api", "config"}
    assert got["api"]["pid"] == 111 and got["api"]["port"] == 62581
    assert got["config"]["pid"] == 333

    # meta 里的主 pid 不应被 children 扰动
    with open(isolated_runtime_meta, encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["pid"] == os.getpid()

    unregister_child("api")
    got2 = list_children()
    assert "api" not in got2 and "config" in got2


def test_register_child_invalid_pid_is_noop(isolated_runtime_meta):
    from chayuan.server.config_panel.restart import (
        list_children, record_runtime, register_child,
    )
    record_runtime(argv=["chayuan", "start", "-a"])
    register_child("api", 0, port=62581)      # pid=0 无效
    register_child("", 9999, port=62581)      # role 空
    assert list_children() == {}


# ---------------------------------------------------------------------------
# pid liveness
# ---------------------------------------------------------------------------

def test_pid_alive_current_process():
    from chayuan.server.config_panel.stop import _pid_alive
    assert _pid_alive(os.getpid()) is True
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False
    # 取一个大概率不存在的大 pid
    assert _pid_alive(999999) is False


# ---------------------------------------------------------------------------
# stop_role：各种已死 / 未启 / 端口空的分支
# ---------------------------------------------------------------------------

def test_stop_role_not_running(isolated_runtime_meta, monkeypatch):
    """无 meta、无端口占用 → not_running。"""
    # 让 Settings 取默认端口，不改动
    from chayuan.server.config_panel.stop import stop_role
    r = stop_role("api")
    assert r.role == "api"
    assert r.status in ("not_running", "stopped")  # 若本机真的没起 chayuan


def test_stop_role_unknown_role_fallback_label(isolated_runtime_meta):
    from chayuan.server.config_panel.stop import stop_role
    r = stop_role("ghost_role_that_doesnt_exist")
    # label 回落为 role 本身
    assert r.label == "ghost_role_that_doesnt_exist"
    assert r.status == "not_running"


def test_stop_role_pid_already_dead(isolated_runtime_meta):
    """meta 里写了一个已死 pid；stop 时应自清 + 返回 not_running。"""
    from chayuan.server.config_panel.restart import (
        list_children, record_runtime, register_child,
    )
    from chayuan.server.config_panel.stop import stop_role
    record_runtime(argv=["chayuan", "start"])
    register_child("api", 999999, port=65432, name="dead-api")

    r = stop_role("api")
    assert r.role == "api"
    # 端口应该也不在监听
    assert r.status == "not_running"
    # meta 里应被清
    assert "api" not in list_children()


# ---------------------------------------------------------------------------
# 真实进程：起一个可控子进程，验证 _kill_pid
# ---------------------------------------------------------------------------

def _spawn_dummy_child(sleep_sec: int = 30) -> subprocess.Popen:
    """起一个只 sleep 的 python 子进程，父进程可直接 kill。"""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({sleep_sec})"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def test_kill_pid_graceful_then_force():
    from chayuan.server.config_panel.stop import _kill_pid, _pid_alive
    proc = _spawn_dummy_child(30)
    try:
        assert _pid_alive(proc.pid) is True
        ok, detail = _kill_pid(proc.pid, force=False, term_timeout=4.0)
        assert ok, f"kill 失败：{detail}"
        # 给系统一点时间回收
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        assert proc.poll() is not None, "子进程应已退出"
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def test_kill_pid_force():
    from chayuan.server.config_panel.stop import _kill_pid, _pid_alive
    proc = _spawn_dummy_child(30)
    try:
        ok, _ = _kill_pid(proc.pid, force=True)
        assert ok
        time.sleep(0.3)
        assert not _pid_alive(proc.pid)
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def test_stop_role_with_live_registered_pid(isolated_runtime_meta):
    """完整路径：在 meta 里注册一个真实活着的 dummy pid，stop_role 应能杀它。"""
    from chayuan.server.config_panel.restart import (
        list_children, record_runtime, register_child,
    )
    from chayuan.server.config_panel.stop import _pid_alive, stop_role
    record_runtime(argv=["chayuan", "start", "-a"])
    proc = _spawn_dummy_child(30)
    try:
        register_child("api", proc.pid, port=None, name="dummy-api")
        r = stop_role("api")
        # 可能因 port 兜底走了 not_running——重点是 pid 真的死了
        time.sleep(0.2)
        assert _pid_alive(proc.pid) is False, f"pid 未杀死：{r}"
        # meta 应清
        assert "api" not in list_children()
    finally:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# status_snapshot / stop_all
# ---------------------------------------------------------------------------

def test_status_snapshot_shape(isolated_runtime_meta):
    from chayuan.server.config_panel.stop import KNOWN_ROLES, status_snapshot
    snap = status_snapshot()
    assert set(snap.keys()) == set(KNOWN_ROLES.keys())
    for role, info in snap.items():
        assert "label" in info
        assert "pid" in info and "pid_alive" in info
        assert "port" in info and "port_listen" in info
        assert "running" in info


def test_stop_all_without_runtime_meta(isolated_runtime_meta):
    """无 runtime meta 时，stop_all 不抛；parent_stopped=False + 描述。"""
    from chayuan.server.config_panel.stop import stop_all
    # 确保 meta 文件不存在
    if isolated_runtime_meta.exists():
        isolated_runtime_meta.unlink()
    out = stop_all()
    assert out.parent_stopped is False
    assert out.parent_pid is None
    assert "未找到" in out.parent_detail or "runtime" in out.parent_detail.lower()
    # 即便父进程没找到，仍会给出每个 role 的行（多数 not_running）
    assert len(out.roles) == 3
