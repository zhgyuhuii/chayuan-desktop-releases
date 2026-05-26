"""Aggregator: runs all OS-appropriate checks and returns a structured report."""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field


@dataclass
class CheckResult:
    name: str
    severity: str          # "ok" | "warn" | "fatal"
    detail: str = ""
    fix: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PreflightReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def fatal_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "fatal")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "warn")

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.checks if c.severity == "ok")

    def add(self, *cs: CheckResult) -> None:
        self.checks.extend(cs)

    def to_dict(self) -> dict:
        return {
            "summary": {"fatal": self.fatal_count, "warn": self.warn_count, "ok": self.ok_count},
            "checks": [c.to_dict() for c in self.checks],
        }


def run_all() -> PreflightReport:
    from chayuan_preflight import (
        av_checks,
        gpu_checks,
        linux_checks,
        mac_checks,
        os_checks,
        port_checks,
    )

    rep = PreflightReport()
    rep.add(*os_checks.run())
    rep.add(*port_checks.run())
    rep.add(*gpu_checks.run())
    if sys.platform == "win32":
        rep.add(*av_checks.run_windows())
    if sys.platform == "darwin":
        rep.add(*mac_checks.run())
    if sys.platform.startswith("linux"):
        rep.add(*linux_checks.run())
        rep.add(*av_checks.run_linux())
    return rep
