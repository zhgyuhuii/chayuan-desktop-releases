from __future__ import annotations

import subprocess

from chayuan_preflight.report import CheckResult


def run() -> list[CheckResult]:
    out: list[CheckResult] = []
    try:
        r = subprocess.run(["spctl", "--status"], capture_output=True, text=True, timeout=3, check=False)
        out.append(CheckResult(
            name="mac.gatekeeper",
            severity="warn" if "enabled" in r.stdout else "ok",
            detail=r.stdout.strip() or r.stderr.strip(),
            fix="`sudo spctl --master-disable` (NOT recommended) — instead, sign + notarize installer",
        ))
    except FileNotFoundError:
        out.append(CheckResult(name="mac.gatekeeper", severity="ok", detail="spctl not installed"))
    out.append(CheckResult(
        name="mac.tcc",
        severity="warn",
        detail="microphone / disk-access prompts may appear on first run",
        fix="grant access via System Settings → Privacy & Security",
    ))
    return out
