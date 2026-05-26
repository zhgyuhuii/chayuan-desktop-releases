"""配置面板「重启服务」的守护脚本。

入参：
- ``--meta``：由 ``restart.record_runtime`` 写出的 json 路径；
- ``--delay``：收到调用后先等待多少秒再开始操作（默认 1s，留给 UI 吐 toast）。

执行流程：
1. 读 meta，拿到 pid / argv / cwd / env；
2. sleep(delay)；
3. 向 pid 发 SIGTERM；轮询等最多 20s；
4. 若仍存活，发 SIGKILL；
5. 以原始 argv / cwd / env 调 ``Popen`` 重新拉起 chayuan（脱离本脚本终端）。

本脚本被 ``subprocess.Popen(..., start_new_session=True)`` 启动，脱离 chayuan 进程组，
因此即使 chayuan 被全部杀死也会继续执行。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# 跨平台：Windows 没有 SIGKILL，也不支持 start_new_session；统一用 process_utils。
# restart_helper 是 Popen 出的独立脚本，import 链要尽量浅——直接 import 不过工程树。
try:
    from chayuan.server.shared.process_utils import (
        detached_popen_kwargs, is_pid_alive, terminate_pid,
    )
except Exception:  # noqa: BLE001
    # 极端情况（例如 PYTHONPATH 没带 chayuan）：退化为 POSIX 最简逻辑。
    # 注意这里不会在 Windows 上运行（Windows 正常走 chayuan CLI 路径，PYTHONPATH 有）。
    import signal as _sig

    def is_pid_alive(pid):  # type: ignore[no-redef]
        try:
            os.kill(int(pid), 0)
            return True
        except (ProcessLookupError, OSError):
            return False
        except PermissionError:
            return True

    def terminate_pid(pid, *, force=False, term_timeout=20.0, kill_timeout=10.0):  # type: ignore[no-redef]
        try:
            os.kill(int(pid),
                      _sig.SIGKILL if force else _sig.SIGTERM)
        except ProcessLookupError:
            return True, "already dead"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
        deadline = time.time() + term_timeout
        while time.time() < deadline:
            if not is_pid_alive(pid):
                return True, "ok"
            time.sleep(0.2)
        if not force:
            try:
                os.kill(int(pid), _sig.SIGKILL)
            except ProcessLookupError:
                return True, "dead"
            except Exception:
                pass
            time.sleep(kill_timeout)
        return (not is_pid_alive(pid)), "force"

    def detached_popen_kwargs():  # type: ignore[no-redef]
        if os.name == "nt":
            return {
                "creationflags": 0x00000008 | 0x00000200,  # DETACHED + NEW_GROUP
                "close_fds": True,
            }
        return {"start_new_session": True, "close_fds": True}


def _log(msg: str) -> None:
    try:
        sys.stderr.write(f"[restart helper] {msg}\n")
        sys.stderr.flush()
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", required=True)
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args(argv)

    meta_path = Path(args.meta)
    if not meta_path.is_file():
        _log(f"meta file missing: {meta_path}")
        return 2
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        _log(f"meta load failed: {e!r}")
        return 2

    pid = int(meta.get("pid") or 0)
    raw_argv = list(meta.get("argv") or [])
    cwd = meta.get("cwd") or str(Path.cwd())
    inherit_env = meta.get("env") or {}
    executable = meta.get("executable") or sys.executable

    if not raw_argv:
        _log("argv empty, abort.")
        return 2

    time.sleep(max(0.0, float(args.delay)))

    if pid and is_pid_alive(pid):
        _log(f"terminating pid={pid}")
        ok, detail = terminate_pid(pid, term_timeout=20.0, kill_timeout=10.0)
        _log(f"terminate result: ok={ok} detail={detail}")
    else:
        _log(f"target pid={pid} not alive, skip kill")

    cmd = _rebuild_cmd(executable, raw_argv)
    env = os.environ.copy()
    env.update({k: v for k, v in inherit_env.items() if isinstance(v, str)})

    _log(f"launching: {cmd}")
    try:
        subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **detached_popen_kwargs(),
        )
    except Exception as e:
        _log(f"relaunch failed: {e!r}")
        return 3
    return 0


def _rebuild_cmd(executable: str, argv: list[str]) -> list[str]:
    """把 ``argv`` 还原成可直接执行的命令行。

    - 如果 ``argv[0]`` 是存在于磁盘的 python 脚本（例如 poetry 注入的 chayuan 脚本），
      直接走 ``[executable, argv[0], *argv[1:]]``；
    - 否则（比如是裸 ``chayuan`` 别名）：退回到 ``argv`` 原样（让 shell PATH 找）。
    """
    if not argv:
        return [executable]

    first = argv[0]
    if first and Path(first).is_file():
        return [executable, first, *argv[1:]]
    return list(argv)


if __name__ == "__main__":
    raise SystemExit(main())
