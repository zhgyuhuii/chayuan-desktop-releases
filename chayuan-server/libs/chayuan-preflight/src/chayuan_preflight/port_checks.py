from __future__ import annotations

import socket

from chayuan_core import load_config
from chayuan_preflight.report import CheckResult


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def run() -> list[CheckResult]:
    cfg = load_config()
    out: list[CheckResult] = []
    p = cfg.gateway.port
    out.append(CheckResult(
        name=f"port.{p}",
        severity="ok" if _free(p) else "warn",
        detail=f"gateway default port {p}",
        fix=f"override with CHAYUAN_GATEWAY_PORT=<other> or stop the conflicting process",
    ))
    return out
