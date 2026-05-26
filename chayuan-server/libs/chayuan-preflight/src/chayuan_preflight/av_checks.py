from __future__ import annotations

import shutil
import subprocess

from chayuan_preflight.report import CheckResult


def run_windows() -> list[CheckResult]:
    out: list[CheckResult] = []
    pwsh = shutil.which("powershell.exe")
    if pwsh:
        try:
            r = subprocess.run(
                [pwsh, "-NoProfile", "-Command", "Get-MpComputerStatus | Select RealTimeProtectionEnabled,AntivirusEnabled"],
                capture_output=True, text=True, timeout=8, check=False,
            )
            if r.returncode == 0 and "True" in r.stdout:
                out.append(CheckResult(
                    name="av.windows_defender",
                    severity="warn",
                    detail="Windows Defender realtime protection is ON",
                    fix="add an exclusion for <CHAYUAN_HOME> via scripts/fixers/add-mp-exclusion.ps1",
                ))
                return out
        except Exception:
            pass
    out.append(CheckResult(name="av.windows_defender", severity="ok", detail="status unknown / disabled"))
    return out


def run_linux() -> list[CheckResult]:
    out: list[CheckResult] = []
    candidates = (("clamd", "ClamAV"), ("freshclam", "ClamAV"))
    for bin_, label in candidates:
        if shutil.which(bin_):
            out.append(CheckResult(
                name=f"av.{label}", severity="warn",
                detail=f"{label} present — may scan model files",
                fix=f"add <CHAYUAN_HOME>/models to {label} exclusion list",
            ))
    if not out:
        out.append(CheckResult(name="av.linux", severity="ok", detail="no common AV detected"))
    return out
