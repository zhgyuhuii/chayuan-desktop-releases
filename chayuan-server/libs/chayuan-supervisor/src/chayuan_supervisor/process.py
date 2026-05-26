"""Subprocess wrapper with state machine + log streaming + restart loop."""
from __future__ import annotations

import enum
import os
import shlex
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from chayuan_core import ensure_dirs, get_logger
from chayuan_core.events import TOPIC_PROCESS_LOG, TOPIC_PROCESS_STATE, get_bus
from chayuan_supervisor.health import HealthProbe, check
from chayuan_supervisor.restart_policy import RestartPolicy

logger = get_logger("chayuan_supervisor.process")


class ProcessState(str, enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    BACKOFF = "backoff"
    FAILED = "failed"


@dataclass
class ManagedProcess:
    name: str
    binary: str
    args: list[str] = field(default_factory=list)
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | None = None
    init_cmds: list[list[str]] = field(default_factory=list)
    health: HealthProbe | None = None
    health_interval_sec: float = 5.0
    restart_policy: RestartPolicy = field(default_factory=RestartPolicy)

    state: ProcessState = ProcessState.STOPPED
    pid: int | None = None
    attempt: int = 0
    started_at: float = 0.0
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=200))

    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _supervisor_thread: threading.Thread | None = field(default=None, repr=False)
    _log_thread: threading.Thread | None = field(default=None, repr=False)

    # ---- public --------------------------------------------------------

    def start(self, dry_run: bool = False) -> None:
        if self.state in (ProcessState.RUNNING, ProcessState.STARTING):
            return
        if dry_run:
            logger.info("process.dry_run", name=self.name, binary=self.binary, args=self.args)
            self._set_state(ProcessState.RUNNING)
            return
        self._stop.clear()
        self._supervisor_thread = threading.Thread(
            target=self._run_loop, name=f"sup-{self.name}", daemon=True
        )
        self._supervisor_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                if os.name == "nt":
                    self._proc.terminate()
                else:
                    self._proc.send_signal(signal.SIGTERM)
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None
        self.pid = None
        self._set_state(ProcessState.STOPPED)

    def restart(self) -> None:
        self.stop()
        self.attempt = 0
        self.start()

    def status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "pid": self.pid,
            "attempt": self.attempt,
            "started_at": self.started_at,
            "uptime_sec": (time.time() - self.started_at) if self.started_at else 0,
        }

    # ---- internals -----------------------------------------------------

    def _set_state(self, s: ProcessState) -> None:
        if self.state != s:
            self.state = s
            try:
                get_bus().publish(TOPIC_PROCESS_STATE, self.status())
            except Exception:
                pass

    def _spawn(self) -> subprocess.Popen:
        cmd = [self.binary, *self.args]
        merged_env = {**os.environ, **dict(self.env)}
        log_path = ensure_dirs().logs / f"{self.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Open log in append mode, line-buffered
        self._log_path = log_path
        return subprocess.Popen(
            cmd,
            cwd=self.cwd,
            env=merged_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

    def _stream_logs(self, proc: subprocess.Popen) -> None:
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                for line in proc.stdout or []:
                    f.write(line)
                    self.log_tail.append(line.rstrip("\n"))
                    try:
                        get_bus().publish(TOPIC_PROCESS_LOG, {"name": self.name, "line": line.rstrip("\n")})
                    except Exception:
                        pass
        except Exception:
            pass

    def _run_init_cmds(self) -> None:
        for cmd in self.init_cmds:
            cmd_str = " ".join(shlex.quote(c) for c in cmd)
            logger.info("process.init", name=self.name, cmd=cmd_str)
            try:
                subprocess.run(cmd, env={**os.environ, **dict(self.env)}, cwd=self.cwd,
                               check=True, capture_output=True, timeout=120)
            except subprocess.CalledProcessError as e:
                logger.warning("process.init.failed", name=self.name, code=e.returncode)
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("process.init.error", name=self.name, err=str(e))
                raise

    def _run_loop(self) -> None:
        try:
            self._run_init_cmds()
        except Exception:
            self._set_state(ProcessState.FAILED)
            return

        while not self._stop.is_set():
            self.attempt += 1
            self._set_state(ProcessState.STARTING)
            try:
                self._proc = self._spawn()
                self.pid = self._proc.pid
                self.started_at = time.time()
                self._log_thread = threading.Thread(
                    target=self._stream_logs, args=(self._proc,), daemon=True
                )
                self._log_thread.start()
                self._set_state(ProcessState.RUNNING)
                self._wait_until_dead_or_unhealthy()
            except FileNotFoundError as e:
                logger.warning("process.binary_missing", name=self.name, binary=self.binary, err=str(e))
                self._set_state(ProcessState.FAILED)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("process.spawn_error", name=self.name, err=str(e))
                self._set_state(ProcessState.FAILED)
                return

            if self._stop.is_set():
                self._set_state(ProcessState.STOPPED)
                return

            if not self.restart_policy.should_restart(self.attempt):
                logger.warning("process.give_up", name=self.name, attempts=self.attempt)
                self._set_state(ProcessState.FAILED)
                return

            delay = self.restart_policy.delay_for(self.attempt)
            self._set_state(ProcessState.BACKOFF)
            self._stop.wait(delay)

    def _wait_until_dead_or_unhealthy(self) -> None:
        last_health = time.time()
        while not self._stop.is_set() and self._proc is not None:
            rc = self._proc.poll()
            if rc is not None:
                logger.info("process.exited", name=self.name, code=rc)
                return
            now = time.time()
            if self.health and now - last_health >= self.health_interval_sec:
                last_health = now
                ok = check(self.health)
                if not ok and self.state == ProcessState.RUNNING:
                    self._set_state(ProcessState.UNHEALTHY)
                elif ok and self.state == ProcessState.UNHEALTHY:
                    self._set_state(ProcessState.RUNNING)
            time.sleep(0.5)
