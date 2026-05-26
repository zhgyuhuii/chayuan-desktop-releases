"""平台特定的最终封装器（AppImage / dmg / NSIS）。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from chayuan_packaging.platform_info import PlatformKey


class TargetFinalizer(Protocol):
    name: str

    def finalize(
        self,
        *,
        staging: "Path",
        out_dir: "Path",
        platform: "PlatformKey",
        release: str,
        version: str,
    ) -> "Path": ...


def get_finalizer(platform: str) -> TargetFinalizer:
    if platform.startswith("linux"):
        from chayuan_packaging.targets.linux import LinuxFinalizer
        return LinuxFinalizer()
    if platform.startswith("mac"):
        from chayuan_packaging.targets.mac import MacFinalizer
        return MacFinalizer()
    if platform.startswith("win"):
        from chayuan_packaging.targets.windows import WindowsFinalizer
        return WindowsFinalizer()
    raise ValueError(f"unsupported platform: {platform!r}")


__all__ = ["TargetFinalizer", "get_finalizer"]
