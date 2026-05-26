"""子进程管理：负责启动 / 停止 / 重启 ``chayuan start -a`` 后台服务。

当 chayuan 被以托盘壳形态启动时，真正的 API / WebUI / 配置面板仍然通过现有
``chayuan.startup.main`` 拉起（保持与 CLI 行为完全一致），托盘这一层只做
「进程管家」：

* 以独立 session 启动（``start_new_session=True``），避免托盘前台进程被
  子进程的 Ctrl+C 干扰；
* 退出时向整个进程组发 SIGTERM→等待→SIGKILL，保证没有游离的 uvicorn /
  streamlit 子孙进程；
* 重启时先 stop 再 start，复用同一实例。

只做最小封装，不在这里做健康检查（健康检查由菜单里的「打开 XXX」在点开时
再判断端口可达性即可）。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional


def _resolve_python_and_cli() -> tuple[str, list[str]]:
    """返回启动子进程用的 ``(python_path, cli_argv_prefix)``。

    - 在 portable / 安装包 场景下，``sys.executable`` 就是 bundle 里的 Python，
      直接 ``python -m chayuan.cli start -a`` 最稳妥（不依赖 ``bin/chayuan``
      shebang，跨机器搬运不会断）；
    - 在开发环境（源码 + venv）下同样工作。

    如果未来需要切换到 ``bin/chayuan`` 脚本，只需改这一处。
    """
    return sys.executable, ["-m", "chayuan.cli", "start", "-a"]


class Backend:
    """``chayuan start -a`` 子进程的最小管家。

    线程安全：所有修改 ``self.proc`` 的路径都持锁。
    """

    def __init__(self, log_path: Optional[Path] = None) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self._log_file = None
        self._log_path = log_path
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """启动子进程；若已在运行则无操作。

        企业版 embedded 模式：在拉起 ``chayuan start -a`` 之前，先保证内嵌
        Postgres / Redis 已经就绪，把解析到的 URI 回写到 settings yaml，
        这样后端进程启动时就能直接读到正确的地址（否则 apply_prod_profile
        留下的 ``postgres:5432`` 占位会指向不存在的 docker hostname）。

        非企业版 / 非 embedded 场景下 ``services.manager.ensure_up`` 是 no-op。
        """
        with self._lock:
            if self._alive_locked():
                return

            # 内嵌服务优先拉起（幂等）；失败就让 start() 抛，托盘会弹错误框
            try:
                from chayuan.tray.services import manager as _svc

                brought = _svc.ensure_up()
                if brought:
                    _svc.apply_to_settings_yaml()
                    sys.stderr.write(
                        f"[tray] embedded services ready: "
                        f"PG socket={brought.get('PG_SOCKET')}, "
                        f"Redis :{brought.get('REDIS_PORT')}\n"
                    )
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[tray] embedded services 启动失败：{e!r}\n")
                raise

            python, argv = _resolve_python_and_cli()
            cmd = [python, *argv]

            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")
            # 让子进程里的 logger.build_logger 输出不被 stdout buffer 吞。
            env.setdefault("PYTHONIOENCODING", "utf-8")

            log_fh = None
            stdout: int | None = subprocess.DEVNULL
            if self._log_path is not None:
                try:
                    self._log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_fh = open(self._log_path, "ab", buffering=0)
                    stdout = log_fh.fileno()
                except OSError as e:
                    sys.stderr.write(
                        f"[tray] 打开托盘日志失败（继续启动，stdout 丢弃）：{e!r}\n"
                    )
                    log_fh = None

            self._log_file = log_fh

            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdout=stdout,
                    stderr=subprocess.STDOUT,
                    env=env,
                    start_new_session=True,
                    close_fds=True,
                )
            except OSError as e:
                if log_fh is not None:
                    log_fh.close()
                    self._log_file = None
                raise RuntimeError(f"启动 chayuan 后端失败：{e!r}") from e

    def stop(self, term_timeout: float = 8.0, kill_timeout: float = 2.0) -> None:
        """停止子进程及其全部子孙。幂等。"""
        with self._lock:
            proc = self.proc
            log_fh = self._log_file
            self.proc = None
            self._log_file = None

        if proc is None:
            return
        if proc.poll() is not None:
            if log_fh is not None:
                log_fh.close()
            return

        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = None

        def _signal(sig: int) -> None:
            if pgid is not None:
                try:
                    os.killpg(pgid, sig)
                    return
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass

        _signal(signal.SIGTERM)

        deadline = time.monotonic() + term_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.1)

        if proc.poll() is None:
            _signal(signal.SIGKILL)
            deadline = time.monotonic() + kill_timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.1)

        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass

        # 业务后端停了之后，再把 embedded 服务也关掉——否则 tray 退出后会留
        # pg/redis 在后台运行。shutdown 是 no-op 在非企业版。
        try:
            from chayuan.tray.services import manager as _svc

            _svc.shutdown()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[tray] embedded services shutdown 异常：{e!r}\n")

    def restart(self) -> None:
        self.stop()
        self.start()

    def is_running(self) -> bool:
        with self._lock:
            return self._alive_locked()

    # ------------------------------------------------------------------ private

    def _alive_locked(self) -> bool:
        return self.proc is not None and self.proc.poll() is None
