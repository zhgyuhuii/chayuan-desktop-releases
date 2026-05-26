"""平台 (os, arch) 标准化。

为什么要单独一个模块？
======================

* `platform.system()` 给出 ``Linux/Darwin/Windows``，但常见配置文件用
  ``linux/mac/win``；
* `platform.machine()` 在 mac arm 上是 ``arm64``，linux 上是 ``aarch64``，
  windows 上是 ``ARM64``；这里统一成 ``arm64``；
* docker compose 之类的 asset 名又用 ``aarch64`` / ``arm64`` / ``arm`` 各种风格，
  packager 内部一律用 :class:`PlatformKey` 中转。

公开 API：

* :data:`PlatformKey` — 6 个合法 key 的字面量类型
* :func:`current_platform` — 探测当前主机
* :func:`parse_platform` — 字符串 → PlatformKey
* :func:`is_supported` — 校验是否合法
"""
from __future__ import annotations

import platform as _platform
import sys
from typing import Literal, Tuple

PlatformKey = Literal[
    "linux-x86_64", "linux-arm64",
    "mac-x86_64", "mac-arm64",
    "win-x86_64", "win-arm64",
]

ALL_PLATFORMS: Tuple[PlatformKey, ...] = (
    "linux-x86_64", "linux-arm64",
    "mac-x86_64", "mac-arm64",
    "win-x86_64", "win-arm64",
)

_OS_MAP = {
    "linux": "linux",
    "darwin": "mac", "macos": "mac", "mac": "mac",
    "windows": "win", "win32": "win", "win": "win",
}

_ARCH_MAP = {
    "x86_64": "x86_64", "amd64": "x86_64", "x64": "x86_64",
    "aarch64": "arm64", "arm64": "arm64",
}


def current_platform() -> PlatformKey:
    """探测当前主机平台。

    >>> current_platform()  # 在 Apple Silicon mac 上
    'mac-arm64'
    """
    sys_str = sys.platform.lower()
    machine = (_platform.machine() or "").lower()

    if sys_str.startswith("linux"):
        os_key = "linux"
    elif sys_str == "darwin":
        os_key = "mac"
    elif sys_str.startswith("win"):
        os_key = "win"
    else:
        raise RuntimeError(f"unsupported os: {sys_str!r}")

    arch_key = _ARCH_MAP.get(machine)
    if arch_key is None:
        # x86 32 位 / 其它 — 先按 x86_64 兜底（大多机器都能跑 emulation）
        arch_key = "x86_64"
    return f"{os_key}-{arch_key}"  # type: ignore[return-value]


def parse_platform(target: str | None, arch: str | None) -> PlatformKey:
    """合法字符串 → PlatformKey；缺省走 :func:`current_platform`。

    Args:
        target: 平台前缀（``linux`` / ``mac`` / ``win``）；空时取当前。
        arch:   架构（``x86_64`` / ``amd64`` / ``arm64`` / ``aarch64``）；
                空时取当前。

    >>> parse_platform("linux", "amd64")
    'linux-x86_64'
    >>> parse_platform("mac", "aarch64")
    'mac-arm64'
    """
    cur = current_platform()
    cur_os, cur_arch = cur.split("-", 1)

    os_key = _OS_MAP.get((target or "").lower(), cur_os)
    arch_key = _ARCH_MAP.get((arch or "").lower(), cur_arch)
    key = f"{os_key}-{arch_key}"
    if key not in ALL_PLATFORMS:
        raise ValueError(f"invalid platform key: {key!r}")
    return key  # type: ignore[return-value]


def is_supported(key: str) -> bool:
    return key in ALL_PLATFORMS


__all__ = [
    "PlatformKey",
    "ALL_PLATFORMS",
    "current_platform",
    "parse_platform",
    "is_supported",
]
