"""Chayuan unified CLI.

Top-level command groups:
    model     pull / import / ls / rm / switch / enable / disable
    service   start / stop / restart / status / logs / plan
    doctor    pre-flight + report
    info      paths + platform
"""
from chayuan_cli.__main__ import main

__all__ = ["main"]
