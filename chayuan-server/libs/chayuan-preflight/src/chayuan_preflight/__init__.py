"""Pre-flight safety checks (OS / AV / ports / GPU / Mac TCC / Linux SELinux)."""
from chayuan_preflight.report import CheckResult, PreflightReport, run_all

__all__ = ["CheckResult", "PreflightReport", "run_all"]
