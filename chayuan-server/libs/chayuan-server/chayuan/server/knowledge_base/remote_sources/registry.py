"""可插拔的 RemoteSource 注册表。

注册流程:
- 工厂表 SOURCE_KINDS 是显式映射 — 不用 entry_points,部署不踩坑。
- 子模块 import 失败(SDK 未装)直接捕获,保留 kind 名以便前端能列出"已支持但
  缺依赖" — 比直接消失更友好。

新增后端:写一个 RemoteSource 子类 + 在这里挂一行,前后端都不用动。
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from .base import RemoteSource, SourceConfig, SourceError

logger = logging.getLogger("chayuan.kb.remote_sources.registry")


def _load_minio() -> Optional[Callable[[SourceConfig], RemoteSource]]:
    try:
        from .minio_source import MinioSource
        return MinioSource
    except Exception as e:  # noqa: BLE001
        logger.warning("minio source not available: %r", e)
        return None


def _load_fastdfs() -> Optional[Callable[[SourceConfig], RemoteSource]]:
    try:
        from .fastdfs_source import FastDFSSource
        return FastDFSSource
    except Exception as e:  # noqa: BLE001
        logger.warning("fastdfs source not available: %r", e)
        return None


# 不在 import 时立刻加载,避免 minio / fdfs 的副作用阻塞启动;
# 第一次调用时 lazy build。
_FACTORIES: Dict[str, Callable[[], Optional[Callable[[SourceConfig], RemoteSource]]]] = {
    "minio": _load_minio,
    "fastdfs": _load_fastdfs,
}

_CACHE: Dict[str, Optional[Callable[[SourceConfig], RemoteSource]]] = {}


def list_source_kinds() -> List[Dict[str, object]]:
    """列出全部已知 source kind + 是否 available(给前端"灰掉"用)。"""
    out: List[Dict[str, object]] = []
    for kind in _FACTORIES:
        cls = _resolve(kind)
        out.append({
            "kind": kind,
            "available": cls is not None,
            "label": _LABELS.get(kind, kind),
        })
    return out


def _resolve(kind: str) -> Optional[Callable[[SourceConfig], RemoteSource]]:
    if kind in _CACHE:
        return _CACHE[kind]
    fac = _FACTORIES.get(kind)
    cls = fac() if fac else None
    _CACHE[kind] = cls
    return cls


def build_source(config: SourceConfig) -> RemoteSource:
    """按 config.kind 实例化 RemoteSource;不可用 → SourceError(供路由层 4xx)。"""
    cls = _resolve(config.kind)
    if cls is None:
        raise SourceError(
            f"远端源 {config.kind!r} 不可用 — 可能未安装对应 SDK,请检查依赖"
        )
    try:
        return cls(config)
    except SourceError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"{config.kind} 源初始化失败:{e}") from e


_LABELS = {
    "minio": "MinIO / S3 兼容",
    "fastdfs": "FastDFS",
}
