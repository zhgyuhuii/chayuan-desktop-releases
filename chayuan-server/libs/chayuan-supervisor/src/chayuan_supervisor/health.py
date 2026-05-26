"""Health probes: tcp / http / cmd."""
from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class HealthProbe:
    raw: str       # "tcp://127.0.0.1:5432" | "http://127.0.0.1:8080/healthz" | "cmd:psql -c '\\l'"
    timeout_sec: float = 3.0


def _check_tcp(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check_http(url: str, timeout: float) -> bool:
    try:
        import httpx

        r = httpx.get(url, timeout=timeout)
        return 200 <= r.status_code < 500
    except Exception:
        return False


def _check_cmd(cmd: str, timeout: float) -> bool:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout, check=False)
        return r.returncode == 0
    except Exception:
        return False


def check(probe: HealthProbe | str | None) -> bool:
    if probe is None:
        return True
    p = probe if isinstance(probe, HealthProbe) else HealthProbe(raw=probe)
    raw = p.raw
    if raw.startswith("tcp://"):
        u = urlparse(raw)
        return _check_tcp(u.hostname or "127.0.0.1", u.port or 0, p.timeout_sec)
    if raw.startswith(("http://", "https://")):
        return _check_http(raw, p.timeout_sec)
    if raw.startswith("cmd:"):
        return _check_cmd(raw[4:], p.timeout_sec)
    return False
