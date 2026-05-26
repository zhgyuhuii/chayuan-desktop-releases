"""diagnose 报告数据结构。"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, List, Literal, Optional


Severity = Literal["ok", "warn", "fail"]


@dataclasses.dataclass
class DiagnoseCheck:
    name: str
    ok: bool
    severity: Severity
    detail: str
    context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class DiagnoseReport:
    timestamp: str
    platform: str
    python_version: str
    chayuan_root: str
    chayuan_server_version: str
    checks: List[DiagnoseCheck]
    summary: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "platform": self.platform,
            "python_version": self.python_version,
            "chayuan_root": self.chayuan_root,
            "chayuan_server_version": self.chayuan_server_version,
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary,
        }
