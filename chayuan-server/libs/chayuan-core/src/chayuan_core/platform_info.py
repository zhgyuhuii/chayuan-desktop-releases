"""Platform / hardware introspection.

Used by:
  * preflight to decide which checks to run
  * packager to pick correct release matrix
  * runtime adapters to choose the optimal backend (CUDA / Metal / DirectML / CPU)
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field

import psutil


def _detect_gpu() -> dict:
    """Best-effort GPU detection. Never raises."""
    out: dict = {"vendor": "none", "name": "", "memory_mb": 0, "count": 0}
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            r = subprocess.run(
                [nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if r.returncode == 0 and r.stdout.strip():
                lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
                names = [ln.split(",")[0].strip() for ln in lines]
                mems = [int(ln.split(",")[1].strip()) for ln in lines]
                out.update(vendor="nvidia", name=names[0], memory_mb=mems[0], count=len(lines))
                return out
        except Exception:
            pass
    if sys.platform == "darwin" and platform.machine() == "arm64":
        out.update(vendor="apple", name="Apple Silicon (Metal)", count=1)
        return out
    if sys.platform == "win32":
        # Optional: probe DirectML / WMI; left as none for now.
        pass
    return out


@dataclass
class PlatformInfo:
    os: str = ""
    os_release: str = ""
    arch: str = ""
    python_version: str = ""
    python_executable: str = ""
    cpu_count: int = 0
    cpu_freq_mhz: int = 0
    memory_total_mb: int = 0
    memory_avail_mb: int = 0
    gpu: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def get_platform_info() -> PlatformInfo:
    vm = psutil.virtual_memory()
    freq = psutil.cpu_freq()
    return PlatformInfo(
        os={"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform, sys.platform),
        os_release=platform.release(),
        arch=platform.machine(),
        python_version=sys.version.split()[0],
        python_executable=sys.executable,
        cpu_count=os.cpu_count() or 0,
        cpu_freq_mhz=int(freq.max if freq else 0),
        memory_total_mb=int(vm.total / 1024 / 1024),
        memory_avail_mb=int(vm.available / 1024 / 1024),
        gpu=_detect_gpu(),
    )


def main() -> None:
    import json

    print(json.dumps(get_platform_info().to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    main()
