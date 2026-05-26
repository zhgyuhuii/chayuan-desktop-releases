from __future__ import annotations

from chayuan_core import get_platform_info
from chayuan_preflight.report import CheckResult


def run() -> list[CheckResult]:
    g = get_platform_info().gpu
    if g.get("vendor") == "none":
        return [CheckResult(name="gpu", severity="warn",
                            detail="no GPU detected — large models will run on CPU only",
                            fix="install NVIDIA driver / CUDA, or use Mac/Apple Silicon")]
    return [CheckResult(
        name="gpu",
        severity="ok",
        detail=f"{g.get('vendor')} {g.get('name', '')} (memory={g.get('memory_mb')} MB, count={g.get('count')})",
    )]
