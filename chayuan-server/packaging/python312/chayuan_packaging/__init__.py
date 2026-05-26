"""察元跨平台打包器（Python 3.12）。

模块导航
========

* :mod:`platform_info` — 检测当前 / 标准化目标 (os, arch)；6 种平台 key
* :mod:`manifest`      — 解析 ``layout.yaml`` → 结构化数据类
* :mod:`fetchers`      — Github / HTTP / hf-mirror 三类 Fetcher
* :mod:`targets`       — Linux/Mac/Windows 平台特定的最终封装（AppImage/dmg/NSIS）
* :mod:`pack`          — Click CLI：``fetch / build / audit / clean / sbom``

使用方式
========

::

    # 1) 安装（开发机一次性）
    cd chayuan-server/packaging/python312
    pip install -e .

    # 2) 准备 vendor（联网，下载到 packaging/.cache 并解压到 vendor/）
    chayuan-pack fetch --target linux --arch x86_64 --release lite

    # 3) 检查清单
    chayuan-pack audit --target linux --arch x86_64 --release lite

    # 4) 生成安装包
    chayuan-pack build --target linux --arch x86_64 --release lite

设计原则
========

1. **平台无关核心**：fetcher / manifest / sbom 全部是纯 Python，不依赖平台
   特定工具；下载 + 解压 + 校验都靠标准库 + httpx；
2. **平台特定 finalizer**：AppImage 需要 appimagetool；NSIS 需要 makensis；
   dmg 需要 hdiutil；这些只在对应 target 模块里调用，缺失时降级为 tar.gz；
3. **幂等**：fetch 反复跑只下不缺；build 反复跑只重建变化的 layer；
4. **离线友好**：``--offline`` 跳过所有网络访问，仅打包已经在 vendor/ 下的内容；
5. **架构统一**：所有模块通过 ``PlatformKey``（``Literal["linux-x86_64",
   "linux-arm64", "mac-x86_64", "mac-arm64", "win-x86_64", "win-arm64"]``）传递
   平台信息；不要传裸字符串 / Tuple。
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = [
    "Layout",
    "Resource",
    "ReleaseSpec",
    "PlatformKey",
    "current_platform",
    "load_layout",
]

from chayuan_packaging.manifest import (
    Layout,
    ReleaseSpec,
    Resource,
    load_layout,
)
from chayuan_packaging.platform_info import PlatformKey, current_platform
