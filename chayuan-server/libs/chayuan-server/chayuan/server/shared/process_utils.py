"""跨平台进程管理工具（Linux / macOS / Windows）。

为什么单独一个模块：``startup.py`` / ``restart.py`` / ``stop.py`` /
``restart_helper.py`` 都需要做"发信号 / 探活 / 杀进程 / detach spawn / 查端口占用"
等操作，各平台实现细节差异很大（Windows 没 SIGKILL；Linux 常无 lsof；macOS 没 ss）。
集中在这里避免各处都写一遍分支。

三个核心能力：
- ``is_pid_alive(pid)``
- ``terminate_pid(pid, *, force, term_timeout)``
- ``find_pids_listening_on(port)``
- ``detached_popen_kwargs()``：返回 ``subprocess.Popen`` 用的跨平台 detach 参数
"""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import time
from typing import Dict, List, Optional

logger = logging.getLogger("chayuan.process_utils")

_IS_WINDOWS = (os.name == "nt")
_IS_DARWIN = (platform.system().lower() == "darwin")
_IS_LINUX = (platform.system().lower() == "linux")


# ---------------------------------------------------------------------------
# 活性检测
# ---------------------------------------------------------------------------

def is_pid_alive(pid: int) -> bool:
    """跨平台的"pid 是否在运行"。

    - Windows：``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` + ``GetExitCodeProcess``。
      ``PermissionError`` 等同"在跑但拒我"——视为 alive。
    - POSIX：``os.kill(pid, 0)``；``ProcessLookupError`` 表示不在；``PermissionError``
      表示在跑（根进程 / 其他 UID）——视为 alive。
    """
    if pid is None or int(pid) <= 0:
        return False
    pid = int(pid)

    if _IS_WINDOWS:
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            h = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
            )
            if not h:
                # 拿不到句柄：最可能是"不存在"；也可能权限不足（仍视为在跑）
                err = ctypes.get_last_error()
                # 5 = ERROR_ACCESS_DENIED（进程存在，访问受限）
                return err == 5
            try:
                code = ctypes.c_ulong(0)
                ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                if not ok:
                    return False
                return int(code.value) == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            return False

    # POSIX
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 停进程
# ---------------------------------------------------------------------------

def terminate_pid(pid: int, *,
                    force: bool = False,
                    term_timeout: float = 5.0,
                    kill_timeout: float = 2.0) -> tuple[bool, str]:
    """跨平台"停进程"：优先优雅退出，超时强杀。

    - Windows：``taskkill`` → 超时 ``taskkill /F``。``force=True`` 直接 /F。
    - POSIX：``SIGTERM`` → 超时 ``SIGKILL``。``force=True`` 直接 SIGKILL。

    **永不抛异常**；返回 ``(ok, detail)``。``ok=True`` 当且仅当到 `kill_timeout` 后
    pid 已不存在。
    """
    if pid is None or int(pid) <= 0:
        return False, "invalid pid"
    pid = int(pid)
    if not is_pid_alive(pid):
        return True, "already dead"

    if _IS_WINDOWS:
        return _terminate_windows(pid, force=force,
                                    term_timeout=term_timeout,
                                    kill_timeout=kill_timeout)
    return _terminate_posix(pid, force=force,
                              term_timeout=term_timeout,
                              kill_timeout=kill_timeout)


def _terminate_windows(pid: int, *, force: bool,
                         term_timeout: float,
                         kill_timeout: float) -> tuple[bool, str]:
    def _run_taskkill(args: List[str]) -> tuple[int, str]:
        try:
            r = subprocess.run(
                ["taskkill", *args, "/PID", str(pid)],
                check=False, capture_output=True, timeout=8,
            )
            out = (r.stdout or b"").decode("utf-8", errors="ignore") + \
                  (r.stderr or b"").decode("utf-8", errors="ignore")
            return int(r.returncode), out.strip()
        except Exception as e:  # noqa: BLE001
            return -1, f"{type(e).__name__}: {e}"

    # /T 会一并带走子进程树（和 chayuan start 启动的 mp 子进程匹配）
    flags = ["/T", "/F"] if force else ["/T"]
    rc, out = _run_taskkill(flags)
    if rc != 0 and not force:
        # 有些 non-UI 进程对非强杀无反应，rc != 0 是正常的（会说 reason=没有响应）
        logger.debug("taskkill graceful rc=%d out=%s", rc, out)

    deadline = time.monotonic() + term_timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True, "ok"
        time.sleep(0.2)

    if force:
        return False, f"force taskkill 后 {term_timeout}s 仍存活"

    # 优雅超时 → 强杀
    rc2, out2 = _run_taskkill(["/T", "/F"])
    deadline = time.monotonic() + kill_timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True, "force killed"
        time.sleep(0.15)
    return False, f"taskkill /F 后仍存活: rc={rc2} {out2}"[:200]


def _terminate_posix(pid: int, *, force: bool,
                       term_timeout: float,
                       kill_timeout: float) -> tuple[bool, str]:
    import signal as _sig
    # Windows 的 signal 模块也定义了 SIGKILL=9（Python 3.11+），但 os.kill 不支持；
    # POSIX 这里都可用。
    first_sig = _sig.SIGKILL if force else _sig.SIGTERM
    try:
        os.kill(pid, first_sig)
    except ProcessLookupError:
        return True, "already dead"
    except PermissionError as e:
        return False, f"PermissionError: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"

    if force:
        # SIGKILL 不可捕获；稍等然后判断
        time.sleep(0.2)
        return (not is_pid_alive(pid)), "force killed"

    deadline = time.monotonic() + term_timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True, "ok"
        time.sleep(0.15)

    # SIGTERM 超时 → SIGKILL
    try:
        os.kill(pid, _sig.SIGKILL)
    except ProcessLookupError:
        return True, "already dead"
    except Exception as e:  # noqa: BLE001
        return False, f"sigkill failed: {type(e).__name__}: {e}"

    deadline = time.monotonic() + kill_timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True, "force killed"
        time.sleep(0.1)
    return False, f"SIGKILL 后 {kill_timeout}s 仍存活"


# ---------------------------------------------------------------------------
# 按端口查占用进程
# ---------------------------------------------------------------------------

def find_pids_listening_on(port: int) -> List[int]:
    """跨平台"谁在监听这个 TCP 端口"。

    顺序：
    1. **psutil**（快，任何平台；已装时走这条）
    2. Windows：``netstat -ano``
    3. Linux / macOS：``ss -tlnp`` → ``lsof -ti:PORT``（任一可用即返回）
    """
    if not port:
        return []
    port = int(port)

    # 1) psutil
    try:
        import psutil  # type: ignore
        pids: List[int] = []
        for c in psutil.net_connections(kind="inet"):
            try:
                if c.laddr and int(c.laddr.port) == port \
                        and str(c.status or "").upper() == "LISTEN":
                    if c.pid:
                        pids.append(int(c.pid))
            except Exception:
                continue
        return _dedup_preserve(pids)
    except Exception:  # noqa: BLE001
        pass

    if _IS_WINDOWS:
        return _find_pids_windows(port)
    return _find_pids_posix(port)


def _find_pids_windows(port: int) -> List[int]:
    try:
        r = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=5,
        )
        pids: List[int] = []
        pat = f":{port}"
        for line in (r.stdout or "").splitlines():
            if pat not in line:
                continue
            if "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.append(int(parts[-1]))
        return _dedup_preserve(pids)
    except Exception:  # noqa: BLE001
        return []


def _find_pids_posix(port: int) -> List[int]:
    """Linux / macOS 回落链：``ss`` → ``lsof``。

    ``ss`` 在现代 Linux 默认装（iproute2），macOS 没有；
    ``lsof`` 在 macOS 默认装，在 Linux 最小镜像里通常缺；
    两者至少有一个可用。
    """
    # ss (Linux)
    for cmd in (
        ["ss", "-tlnp", f"sport = :{port}"],
        ["ss", "-Hlnt", f"sport = :{port}"],
    ):
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=4,
            )
            if r.returncode != 0:
                continue
            pids: List[int] = []
            for line in (r.stdout or "").splitlines():
                # 形如: LISTEN 0 128 ... users:(("python",pid=1234,fd=3))
                # 提取 pid=<num>
                if f":{port}" not in line:
                    continue
                for tok in line.split():
                    if "pid=" in tok:
                        # pid=1234,fd=3
                        for kv in tok.split(","):
                            kv = kv.strip().strip(")")
                            if kv.startswith("pid="):
                                try:
                                    pids.append(int(kv[4:]))
                                except ValueError:
                                    pass
            if pids:
                return _dedup_preserve(pids)
        except FileNotFoundError:
            break  # ss 不存在，尝试 lsof
        except Exception:  # noqa: BLE001
            break

    # lsof (macOS + 部分 Linux)
    try:
        r = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=4,
        )
        return _dedup_preserve(
            [int(x) for x in (r.stdout or "").split() if x.strip().isdigit()]
        )
    except Exception:  # noqa: BLE001
        return []


def _dedup_preserve(items: List[int]) -> List[int]:
    seen = set()
    out: List[int] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------------------
# detach spawn：跨平台"脱离当前进程组"启动子进程的 Popen 参数
# ---------------------------------------------------------------------------

def detached_popen_kwargs() -> Dict[str, object]:
    """返回给 ``subprocess.Popen`` 的 kwargs，让子进程**完全 detach**。

    - POSIX：``start_new_session=True``（新 session，父进程 exit 不会波及）
    - Windows：``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP``；前者让子进程没有
      controlling console，后者让 Ctrl+C 不会传到子进程。
    """
    if _IS_WINDOWS:
        return {
            "creationflags": (
                getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            ),
            "close_fds": True,
        }
    return {
        "start_new_session": True,
        "close_fds": True,
    }
