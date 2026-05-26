"""`shared/process_utils.py` 跨平台兼容性测试。

覆盖：
- ``is_pid_alive``：当前进程 / 不存在的 pid
- ``terminate_pid``：真实起一个 Python 子进程，graceful + force 两条路径
- ``detached_popen_kwargs``：Windows / POSIX 分别返回对的 kwargs
- ``find_pids_listening_on``：真实起一个 TCP listener，按端口找回 pid

这些测试都会在 Linux / macOS / Windows 的 CI 上运行；属于真实进程测试（不 mock），
所以对各平台的**实际行为**都有约束。
"""
from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import threading
import time

import pytest


_IS_WINDOWS = (os.name == "nt")


# ---------------------------------------------------------------------------
# is_pid_alive
# ---------------------------------------------------------------------------

def test_is_pid_alive_current_process():
    from chayuan.server.shared.process_utils import is_pid_alive
    assert is_pid_alive(os.getpid()) is True


def test_is_pid_alive_nonexistent_pid():
    from chayuan.server.shared.process_utils import is_pid_alive
    # 非法 / 不存在
    assert is_pid_alive(0) is False
    assert is_pid_alive(-1) is False
    assert is_pid_alive(None) is False  # type: ignore[arg-type]
    # 大概率不存在的 pid（>99 万）
    assert is_pid_alive(999999) is False


# ---------------------------------------------------------------------------
# terminate_pid
# ---------------------------------------------------------------------------

def _spawn_sleeper(sec: int = 30) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({sec})"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def test_terminate_pid_graceful():
    """graceful 路径：POSIX 下是 SIGTERM→超时 SIGKILL；Windows 下是 taskkill→/F。"""
    from chayuan.server.shared.process_utils import (
        is_pid_alive, terminate_pid,
    )
    proc = _spawn_sleeper(30)
    try:
        ok, detail = terminate_pid(proc.pid, force=False, term_timeout=5.0)
        assert ok, f"terminate 失败：{detail}"
        for _ in range(30):
            if not is_pid_alive(proc.pid):
                break
            time.sleep(0.1)
        assert not is_pid_alive(proc.pid)
    finally:
        try: proc.kill()
        except Exception: pass


def test_terminate_pid_force():
    """force 路径：POSIX SIGKILL / Windows taskkill /F，必须快速完成。"""
    from chayuan.server.shared.process_utils import (
        is_pid_alive, terminate_pid,
    )
    proc = _spawn_sleeper(30)
    try:
        t0 = time.monotonic()
        ok, _ = terminate_pid(proc.pid, force=True)
        elapsed = time.monotonic() - t0
        assert ok
        assert elapsed < 3.0, f"force 太慢：{elapsed:.2f}s"
        time.sleep(0.3)
        assert not is_pid_alive(proc.pid)
    finally:
        try: proc.kill()
        except Exception: pass


def test_terminate_pid_already_dead():
    from chayuan.server.shared.process_utils import terminate_pid
    # 一个早就退出的 pid
    proc = _spawn_sleeper(1)
    proc.wait(timeout=5)
    ok, detail = terminate_pid(proc.pid)
    assert ok
    assert "dead" in detail.lower() or "ok" in detail.lower()


def test_terminate_pid_invalid():
    from chayuan.server.shared.process_utils import terminate_pid
    ok, detail = terminate_pid(0)
    assert ok is False
    assert "invalid" in detail.lower()


# ---------------------------------------------------------------------------
# detached_popen_kwargs
# ---------------------------------------------------------------------------

def test_detached_popen_kwargs_shape():
    from chayuan.server.shared.process_utils import detached_popen_kwargs
    kw = detached_popen_kwargs()
    assert isinstance(kw, dict)
    assert kw.get("close_fds") is True
    if _IS_WINDOWS:
        assert "creationflags" in kw
        assert "start_new_session" not in kw
        # DETACHED_PROCESS (0x8) + CREATE_NEW_PROCESS_GROUP (0x200)
        assert int(kw["creationflags"]) & 0x00000008
        assert int(kw["creationflags"]) & 0x00000200
    else:
        assert kw.get("start_new_session") is True
        assert "creationflags" not in kw


def test_detached_popen_kwargs_actually_works():
    """真起一个 detached subprocess，验证 Popen 不抛平台相关错。"""
    from chayuan.server.shared.process_utils import (
        detached_popen_kwargs, is_pid_alive, terminate_pid,
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **detached_popen_kwargs(),
    )
    try:
        time.sleep(0.2)
        assert is_pid_alive(proc.pid)
    finally:
        terminate_pid(proc.pid, force=True)


# ---------------------------------------------------------------------------
# find_pids_listening_on
# ---------------------------------------------------------------------------

def _open_listener(ready: threading.Event, port_holder: dict,
                     stop_flag: threading.Event) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))  # 让 OS 选端口
    s.listen(5)
    port_holder["port"] = s.getsockname()[1]
    ready.set()
    try:
        while not stop_flag.is_set():
            s.settimeout(0.2)
            try:
                conn, _ = s.accept()
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break
    finally:
        try: s.close()
        except Exception: pass


def test_find_pids_listening_on_local_port():
    """在当前进程内起一个 listener，端口查占用应返回当前 pid（或至少非空）。

    注意：psutil 能识别 listener 所在线程的 owner pid；netstat/ss/lsof 也能。
    但 **平台 + 是否装 psutil** 会影响——当缺 psutil 又缺系统工具时允许空结果。
    """
    from chayuan.server.shared.process_utils import find_pids_listening_on

    ready = threading.Event()
    stop_flag = threading.Event()
    holder: dict = {}
    t = threading.Thread(target=_open_listener, args=(ready, holder, stop_flag),
                             daemon=True)
    t.start()
    assert ready.wait(3.0), "listener thread 没起来"
    port = int(holder["port"])
    try:
        pids = find_pids_listening_on(port)
        # 容忍"查不到"（psutil 没装 + 系统命令也缺/权限不足）；一旦查到就应含当前 pid
        if pids:
            assert os.getpid() in pids, (
                f"期望能识别出当前 pid={os.getpid()}；实际 {pids}"
            )
    finally:
        stop_flag.set()
        t.join(timeout=2.0)


def test_find_pids_listening_on_zero_port():
    from chayuan.server.shared.process_utils import find_pids_listening_on
    assert find_pids_listening_on(0) == []


# ---------------------------------------------------------------------------
# 系统信息：保证测试知道自己在哪跑
# ---------------------------------------------------------------------------

def test_platform_detection_sanity():
    """基本平台识别：跑在什么系统上 process_utils 就按什么分支走。"""
    from chayuan.server.shared import process_utils as pu
    system = platform.system().lower()
    if system == "linux":
        assert not pu._IS_WINDOWS
        assert pu._IS_LINUX
    elif system == "darwin":
        assert not pu._IS_WINDOWS
        assert pu._IS_DARWIN
    elif system == "windows":
        assert pu._IS_WINDOWS
    else:  # 其他 Unix（freebsd 等）
        assert not pu._IS_WINDOWS
