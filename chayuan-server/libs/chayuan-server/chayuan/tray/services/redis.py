"""内嵌 Redis supervisor（企业版 embedded 模式）。

与 Postgres 相比简单很多：只 fork 出 ``redis-server`` 进程，用 127.0.0.1 +
固定端口（若冲突走 +10000 的备用段）。没有数据库初始化之类的概念，用户
目录空则第一次启动 Redis 自己会建 RDB。
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from chayuan.tray.services import _bundled_bin_dir

DEFAULT_PORT = 6379
FALLBACK_PORT_BASE = 16379  # 冲突时 6379 -> 16379 -> 26379...


def services_root() -> Path:
    return Path.home() / ".chayuan" / "services"


def _redis_dir() -> Path:
    return services_root() / "redis"


def _pidfile() -> Path:
    return _redis_dir() / "redis.pid"


def _conffile() -> Path:
    return _redis_dir() / "redis.conf"


def _logfile() -> Path:
    return _redis_dir() / "redis.log"


def _data_dir() -> Path:
    return _redis_dir() / "data"


def _redis_bin(name: str) -> Optional[Path]:
    bd = _bundled_bin_dir()
    if bd is None:
        return None
    p = bd / name
    return p if p.is_file() else None


def _port_free(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def pick_port() -> int:
    """依次尝试 6379 / 16379 / 26379 / 36379；都不行抛异常。"""
    if _port_free(DEFAULT_PORT):
        return DEFAULT_PORT
    for i in range(1, 5):
        p = FALLBACK_PORT_BASE * i + (DEFAULT_PORT if i == 1 else 0)
        if _port_free(p):
            return p
    raise RuntimeError("找不到可用的 Redis 端口（6379 / 16379 / 26379 / 36379 全被占）")


def ensure_config(port: int) -> None:
    _redis_dir().mkdir(parents=True, exist_ok=True)
    _data_dir().mkdir(parents=True, exist_ok=True)

    # 最小化 redis.conf：本地绑定 + 关掉 protected-mode 下的 warning，
    # 追加日志与 dir；企业用户可以自己后续覆盖这个文件。
    conf = [
        "# chayuan embedded redis config",
        f"port {port}",
        "bind 127.0.0.1",
        "protected-mode yes",
        "daemonize no",  # 我们自己接管进程，不要 fork 到后台
        f"pidfile {_pidfile()}",
        f"dir {_data_dir()}",
        f"logfile {_logfile()}",
        "loglevel notice",
        # 关掉集群、AOF；企业版 MVP 不需要
        "appendonly no",
        # save 空 = 关掉 RDB 定期保存；内存任务挂掉重启重建即可
        "save \"\"",
    ]
    _conffile().write_text("\n".join(conf) + "\n", encoding="utf-8")


def is_running() -> bool:
    pf = _pidfile()
    if not pf.is_file():
        return False
    try:
        pid = int(pf.read_text().strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# 我们不用 daemonize，因此需要自己记住子进程。放模块级单例。
_proc: Optional[subprocess.Popen] = None


def start(port: Optional[int] = None, timeout: float = 10.0) -> int:
    """起 redis-server，阻塞等到端口可连；返回实际用的 port。"""
    global _proc
    if is_running():
        # 从 pid 文件反查 port 不方便，交给 caller 记录
        raise RuntimeError("redis 已在运行，请先 stop() 再 start()")

    if port is None:
        port = pick_port()
    ensure_config(port)

    redis_server = _redis_bin("redis-server")
    if redis_server is None:
        raise FileNotFoundError("找不到 redis-server 二进制")

    env = os.environ.copy()
    env.setdefault("LC_ALL", "C")

    # 子进程 stdout/stderr 都丢进 log 文件（redis 自己还会写 logfile，冗余也无所谓）
    _logfile().parent.mkdir(parents=True, exist_ok=True)
    logfh = open(_logfile(), "ab", buffering=0)
    _proc = subprocess.Popen(
        [str(redis_server), str(_conffile())],
        stdout=logfh, stderr=logfh, env=env,
        start_new_session=True,
    )

    # 写自己的 pidfile（redis.conf 里 pidfile 用于 redis 自己；这里多一层保险）
    _pidfile().write_text(str(_proc.pid), encoding="utf-8")

    # 等端口可连
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_free(port):  # 端口有人监听 == redis 已起
            return port
        time.sleep(0.2)
    # 超时：stop 并报错
    stop()
    raise RuntimeError("redis-server 未在超时内起来；查看 redis.log")


def stop(timeout: float = 10.0) -> None:
    global _proc
    if _proc is not None:
        try:
            _proc.terminate()
            try:
                _proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _proc.kill()
        except ProcessLookupError:
            pass
        _proc = None

    pf = _pidfile()
    if pf.is_file():
        try:
            pid = int(pf.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
        except (ValueError, ProcessLookupError, OSError):
            pass
        try:
            pf.unlink()
        except OSError:
            pass


def url(port: int, db: int = 0) -> str:
    return f"redis://127.0.0.1:{port}/{db}"


if __name__ == "__main__":
    port = start()
    print("redis up on", port)
    time.sleep(1)
    stop()
    print("stopped")
