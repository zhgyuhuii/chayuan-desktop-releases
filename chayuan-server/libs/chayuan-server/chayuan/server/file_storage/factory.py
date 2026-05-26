"""FileStorage 工厂 — 按 Settings 选后端，单例返回。

两级 API：
- ``get_storage()``：全局默认后端，覆盖大多数写入（chat_temp / image_files / misc）。
- ``get_storage_for_kb(kb_name)``：按 KB 解析——先查 ``knowledge_base.storage_backend``，
  为空则回落全局。每种后端仅建一个实例并缓存。

失败路径：
- backend=minio 但 minio-py 未装 / 凭据错 / 网络不通 → 自动降级 LocalStorage + 打 warn
  （保证业务不中断；运维可在 /storage/status 看到告警）

热重置：``reset_cache()`` 在 Settings 热更新后调用，会清掉全局与所有按 KB 的缓存。
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

from chayuan.server.file_storage.base import FileStorage

logger = logging.getLogger("chayuan.file_storage.factory")

_LOCK = threading.Lock()
_CACHE: Optional[FileStorage] = None
# 按 backend 名字缓存实例（避免一个 KB 一份 MinioStorage，浪费连接池）
_BY_BACKEND: Dict[str, FileStorage] = {}


def get_storage() -> FileStorage:
    """全局默认存储后端单例（由 Settings.FILE_STORAGE_BACKEND 驱动）。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        _CACHE = _build()
    return _CACHE


def get_storage_for_kb(kb_name: str) -> FileStorage:
    """按 KB 选后端：``knowledge_base.storage_backend`` 优先，否则全局默认。

    ``kb_name`` 为空 / 查询异常时回落全局 ``get_storage()``。
    """
    if not kb_name:
        return get_storage()
    try:
        from chayuan.server.db.repository.knowledge_base_repository import (
            get_kb_storage_backend,
        )
        override = (get_kb_storage_backend(kb_name) or "").strip().lower()
    except Exception as e:  # noqa: BLE001
        logger.debug("get_kb_storage_backend(%r) 失败，回落全局：%r", kb_name, e)
        return get_storage()
    if not override or override == "global":
        return get_storage()
    return _get_by_backend(override)


def _get_by_backend(backend: str) -> FileStorage:
    cached = _BY_BACKEND.get(backend)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _BY_BACKEND.get(backend)
        if cached is not None:
            return cached
        instance = _build(force_backend=backend)
        _BY_BACKEND[backend] = instance
        return instance


def reset_cache() -> None:
    """Settings 或 KB.storage_backend 变更后调用，清所有缓存。"""
    global _CACHE
    with _LOCK:
        _CACHE = None
        _BY_BACKEND.clear()


def _build(force_backend: Optional[str] = None) -> FileStorage:
    try:
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        if force_backend:
            backend = force_backend.strip().lower()
        else:
            backend = str(
                getattr(bs, "FILE_STORAGE_BACKEND", "local") or "local"
            ).strip().lower()
    except Exception as e:  # noqa: BLE001
        logger.warning("读 Settings 失败，降级 LocalStorage：%r", e)
        from chayuan.server.file_storage.local import LocalStorage
        return LocalStorage()

    if backend in ("", "local", "disk", "file"):
        from chayuan.server.file_storage.local import LocalStorage
        return LocalStorage()

    if backend in ("minio", "s3"):
        try:
            from chayuan.server.file_storage.minio import MinioStorage
            return MinioStorage(
                endpoint=str(getattr(bs, "MINIO_ENDPOINT", "") or ""),
                access_key=str(getattr(bs, "MINIO_ACCESS_KEY", "") or ""),
                secret_key=str(getattr(bs, "MINIO_SECRET_KEY", "") or ""),
                secure=bool(getattr(bs, "MINIO_SECURE", False)),
                region=str(getattr(bs, "MINIO_REGION", "us-east-1") or "us-east-1"),
                bucket_prefix=str(getattr(bs, "MINIO_BUCKET_PREFIX", "chayuan") or "chayuan"),
                bucket_overrides=dict(getattr(bs, "MINIO_BUCKETS", {}) or {}),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "MinIO 后端初始化失败，回退 LocalStorage：%r；"
                "请检查 MINIO_ENDPOINT / 凭据 / `pip install minio`", e,
            )
            from chayuan.server.file_storage.local import LocalStorage
            return LocalStorage()

    logger.warning("未知 FILE_STORAGE_BACKEND=%r，默认 LocalStorage", backend)
    from chayuan.server.file_storage.local import LocalStorage
    return LocalStorage()
