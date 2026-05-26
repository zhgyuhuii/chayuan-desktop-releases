from __future__ import annotations

import shutil
import subprocess

import psutil

from chayuan_preflight.report import CheckResult


def run() -> list[CheckResult]:
    out: list[CheckResult] = []
    if shutil.which("getenforce"):
        try:
            r = subprocess.run(["getenforce"], capture_output=True, text=True, timeout=2, check=False)
            mode = r.stdout.strip() or "Unknown"
            out.append(CheckResult(
                name="linux.selinux",
                severity="warn" if mode == "Enforcing" else "ok",
                detail=f"SELinux: {mode}",
                fix="add chayuan binaries to allow-list or run `sudo setenforce 0` for the session",
            ))
        except Exception:
            pass
    if shutil.which("aa-status"):
        try:
            r = subprocess.run(["aa-status"], capture_output=True, text=True, timeout=2, check=False)
            on = "profiles are loaded" in r.stdout
            out.append(CheckResult(
                name="linux.apparmor",
                severity="warn" if on else "ok",
                detail="AppArmor active" if on else "AppArmor present but inactive",
            ))
        except Exception:
            pass
    try:
        soft = psutil.RLIMIT_NOFILE
        import resource
        s, h = resource.getrlimit(resource.RLIMIT_NOFILE)
        out.append(CheckResult(
            name="linux.ulimit_nofile",
            severity="warn" if s < 4096 else "ok",
            detail=f"open file limit: {s}/{h}",
            fix="raise via /etc/security/limits.conf or `ulimit -n 65536`",
        ))
    except Exception:
        pass
    return out
