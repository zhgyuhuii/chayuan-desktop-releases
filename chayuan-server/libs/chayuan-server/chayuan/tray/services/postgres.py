"""内嵌 Postgres supervisor（企业版 embedded 模式）。

**策略：用 unix domain socket 起，不占 TCP 端口**。这避免了用户机上 5432
被其它服务（自己装的 PG / Docker Desktop 里的 pg / Postgres.app 等）占用
时的冲突，也避免了公司防火墙 / 企业 MDM 偶尔会过滤本地监听端口的怪问题。

生命周期：

    ensure_initdb()    # 幂等，首次会跑 initdb 并写 superuser 密码
    start()            # pg_ctl start（若未 running）
    stop()             # pg_ctl stop
    is_running()       # 轮询 postmaster.pid 判断

对外只暴露一个 URI（``sqlalchemy_uri()``），业务代码拿到直接塞 SQLAlchemy
即可。
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from chayuan.tray.services import _bundled_bin_dir

# Postgres 里超级账号名；企业版 apply_prod_profile 默认 user="chayuan"，这里
# 和它保持一致。密码在 initdb 时现随机，和 pwfile 二合一写入，随后持久化到
# ~/.chayuan/services/postgres/.pgpass（仅 600 权限）。
PG_SUPERUSER = "chayuan"
PG_DATABASE = "chayuan"


def services_root() -> Path:
    return Path.home() / ".chayuan" / "services"


def data_dir() -> Path:
    return services_root() / "postgres" / "data"


def socket_dir() -> Path:
    # 注意：socket 路径会被拼进 SQLAlchemy URL 做 host；路径长度不能超过
    # Linux/macOS kernel 的 UNIX_PATH_MAX=104/108，所以放到 services/
    # 直接子目录，避免多嵌几级。
    return services_root() / "postgres" / "socket"


def _pwfile_path() -> Path:
    return services_root() / "postgres" / ".pgpass"


def _pg_bin(name: str) -> Optional[Path]:
    bd = _bundled_bin_dir()
    if bd is None:
        return None
    p = bd / name
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# 初始化 / 启停
# ---------------------------------------------------------------------------


def ensure_initdb() -> bool:
    """若 ``data/`` 不存在，跑一次 initdb；已存在则无操作。

    返回是否进行了初始化（首次 True，后续 False）。
    """
    if (data_dir() / "PG_VERSION").is_file():
        return False

    initdb = _pg_bin("initdb")
    if initdb is None:
        raise FileNotFoundError("找不到 initdb 二进制；企业版安装包不完整")

    data_dir().mkdir(parents=True, exist_ok=True)
    socket_dir().mkdir(parents=True, exist_ok=True)

    # 随机密码 —— 写入 pwfile 让 initdb 消费，再留档到 .pgpass
    pwfile = _pwfile_path()
    pw = secrets.token_urlsafe(24)
    pwfile.parent.mkdir(parents=True, exist_ok=True)
    pwfile.write_text(pw, encoding="utf-8")
    os.chmod(pwfile, 0o600)

    cmd = [
        str(initdb),
        "-D", str(data_dir()),
        "-U", PG_SUPERUSER,
        "--pwfile", str(pwfile),
        "-E", "UTF8",
        "--no-locale",
        # -A scram-sha-256：local 连接也强制密码；防止谁在用户机上裸跑 psql 进来。
        "-A", "scram-sha-256",
    ]
    # 子进程环境 —— 避免继承 tray 的 PATH 里的 pg 工具干扰
    env = os.environ.copy()
    env.setdefault("LC_ALL", "C")

    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"initdb 失败 rc={result.returncode}\nstdout:{result.stdout}\nstderr:{result.stderr}"
        )

    # 把 listen_addresses 关掉、只开 unix socket；减少攻击面。
    with open(data_dir() / "postgresql.conf", "a", encoding="utf-8") as fp:
        fp.write(
            "\n# injected by chayuan tray.services.postgres\n"
            f"unix_socket_directories = '{socket_dir()}'\n"
            "listen_addresses = ''  # 只走 unix socket，不开 TCP 监听\n"
            "log_destination = 'stderr'\n"
            "logging_collector = off\n"
            "max_connections = 100\n"
            "shared_buffers = 128MB\n"
        )
    return True


def is_running() -> bool:
    pid_file = data_dir() / "postmaster.pid"
    if not pid_file.is_file():
        return False
    try:
        pid = int(pid_file.read_text().splitlines()[0])
    except (ValueError, IndexError, OSError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start(timeout: float = 30.0) -> None:
    """起 postmaster；已 running 则返回。失败抛异常。"""
    if is_running():
        return

    pg_ctl = _pg_bin("pg_ctl")
    if pg_ctl is None:
        raise FileNotFoundError("找不到 pg_ctl 二进制")

    log_file = services_root() / "postgres" / "postgres.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(pg_ctl),
        "-D", str(data_dir()),
        "-l", str(log_file),
        # -w 等就绪；-t 等待秒数
        "-w", "-t", str(int(timeout)),
        "start",
    ]
    env = os.environ.copy()
    env.setdefault("LC_ALL", "C")

    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout + 10)
    if result.returncode != 0:
        raise RuntimeError(
            f"pg_ctl start 失败 rc={result.returncode}\n"
            f"stdout:{result.stdout}\nstderr:{result.stderr}\n"
            f"最近日志：{_tail(log_file, 30)}"
        )

    # 二次确认 postmaster.pid 真的创建了
    for _ in range(int(timeout * 2)):
        if is_running():
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("pg_ctl 返回 0 但 postmaster.pid 未出现；查看 postgres.log")


def stop(timeout: float = 15.0) -> None:
    """stop fast；未 running 则 no-op。"""
    if not is_running():
        return
    pg_ctl = _pg_bin("pg_ctl")
    if pg_ctl is None:
        return
    subprocess.run(
        [str(pg_ctl), "-D", str(data_dir()), "-m", "fast", "-w", "-t", str(int(timeout)), "stop"],
        capture_output=True, text=True, timeout=timeout + 10,
    )


# ---------------------------------------------------------------------------
# 自检 / 建库
# ---------------------------------------------------------------------------


def ensure_database() -> None:
    """确保业务库 ``chayuan`` 存在。"""
    psql = _pg_bin("psql")
    if psql is None:
        raise FileNotFoundError("找不到 psql")

    env = os.environ.copy()
    env["PGPASSWORD"] = _pwfile_path().read_text(encoding="utf-8").strip()
    env.setdefault("LC_ALL", "C")

    # 用 postgres（超级库）查是否已存在
    check = subprocess.run(
        [str(psql),
         "-h", str(socket_dir()),
         "-U", PG_SUPERUSER,
         "-d", "postgres",
         "-tAc", f"SELECT 1 FROM pg_database WHERE datname='{PG_DATABASE}'"],
        env=env, capture_output=True, text=True, timeout=10,
    )
    if check.returncode != 0:
        raise RuntimeError(f"psql check 失败：{check.stderr}")
    if check.stdout.strip() == "1":
        return

    # CREATE DATABASE OWNER chayuan
    subprocess.run(
        [str(psql),
         "-h", str(socket_dir()),
         "-U", PG_SUPERUSER,
         "-d", "postgres",
         "-c", f'CREATE DATABASE "{PG_DATABASE}" OWNER "{PG_SUPERUSER}"'],
        env=env, check=True, timeout=30,
    )


def sqlalchemy_uri() -> str:
    """返回 SQLAlchemy 连接 URI（走 unix socket）。

    psycopg2 的 URI 语法：host 字段填 socket 目录的 **绝对路径** 即可（不需要
    特殊前缀）；port 用默认 5432 即可（socket 文件名 ``.s.PGSQL.5432``）。
    """
    pw = _pwfile_path().read_text(encoding="utf-8").strip()
    # URL-encode 路径里的 /（psycopg2 宽容，实际不需要）
    from urllib.parse import quote_plus
    sock = quote_plus(str(socket_dir()))
    return (
        f"postgresql+psycopg2://{PG_SUPERUSER}:{quote_plus(pw)}@/"
        f"{PG_DATABASE}?host={str(socket_dir())}"
    )


def _tail(path: Path, n: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            lines = fp.readlines()
        return "".join(lines[-n:])
    except OSError:
        return "(no log)"


if __name__ == "__main__":  # quick smoke test
    ensure_initdb()
    start()
    try:
        ensure_database()
        print("OK uri:", sqlalchemy_uri())
    finally:
        stop()
