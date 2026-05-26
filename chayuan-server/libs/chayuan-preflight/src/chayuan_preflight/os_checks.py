from __future__ import annotations

import platform
import sys

from chayuan_preflight.report import CheckResult


def run() -> list[CheckResult]:
    out: list[CheckResult] = []
    out.append(CheckResult(
        name="os.platform",
        severity="ok",
        detail=f"{sys.platform} ({platform.release()})",
    ))
    py = sys.version_info
    sev = "ok" if py >= (3, 10) else "fatal"
    out.append(CheckResult(
        name="os.python",
        severity=sev,
        detail=f"Python {py.major}.{py.minor}.{py.micro}",
        fix="install Python 3.10+ via the embedded runtime under vendor/runtimes/python/" if sev != "ok" else None,
    ))
    arch = platform.machine().lower()
    sev = "ok" if arch in ("x86_64", "amd64", "arm64", "aarch64") else "warn"
    out.append(CheckResult(name="os.arch", severity=sev, detail=arch))
    return out
