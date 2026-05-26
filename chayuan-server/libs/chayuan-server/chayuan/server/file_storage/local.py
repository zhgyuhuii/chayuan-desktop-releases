"""LocalStorage — 本地磁盘后端（默认，零依赖）。

布局：
    {root_dir}/
    ├── kb_content/
    │   └── {kb_name}/{file}
    ├── chat_temp/
    │   └── {temp_id}/{file}
    ├── image_files/
    │   └── {source_id}/{image_id}
    └── misc/

预签名 URL：本地无 S3；返回察元自身的 ``/storage/stream/{ns}/{key}?token=...`` 内部代理路由。
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import shutil
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import IO, List, Optional, Union

from chayuan.server.file_storage.base import (
    FileStorage, StorageError, StorageObject,
)

logger = logging.getLogger("chayuan.file_storage.local")


def _default_root() -> str:
    try:
        from chayuan.settings import Settings
        cfg = (getattr(Settings.basic_settings, "FILE_STORAGE_LOCAL_ROOT", "") or "").strip()
        if cfg:
            return cfg
        # 默认和 KB_ROOT_PATH 同父级，名 storage
        kb_root = getattr(Settings.basic_settings, "KB_ROOT_PATH", "") or ""
        if kb_root:
            return str(Path(kb_root).parent / "storage")
    except Exception:  # noqa: BLE001
        pass
    return str(Path.home() / "chayuan_data" / "storage")


def _safe_key(key: str) -> str:
    """避免路径穿越：去掉所有 .. 段。"""
    parts = [p for p in key.replace("\\", "/").split("/") if p and p != ".."]
    return "/".join(parts)


class LocalStorage(FileStorage):
    name = "local"

    def __init__(self, root_dir: Optional[str] = None):
        self._root = Path(root_dir or _default_root())
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        return self._root / namespace / _safe_key(key)

    # ----- 基础方法 -----

    def put(self, namespace, key, data, *, content_type="", metadata=None):
        key = _safe_key(key)
        if not key:
            raise StorageError("key 不能为空")
        p = self._path(namespace, key)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, (bytes, bytearray)):
                p.write_bytes(data)
                size = len(data)
            else:
                # 流 / file-like
                size = 0
                with open(p, "wb") as f:
                    for chunk in iter(lambda: data.read(65536), b""):
                        if not chunk:
                            break
                        f.write(chunk)
                        size += len(chunk)
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"LocalStorage.put failed: {e}") from e
        return StorageObject(
            key=key, size=size,
            last_modified=datetime.fromtimestamp(p.stat().st_mtime),
            content_type=content_type or "",
        )

    def get(self, namespace, key):
        key = _safe_key(key)
        p = self._path(namespace, key)
        if not p.exists():
            raise StorageError(f"not found: {namespace}/{key}")
        try:
            return p.read_bytes()
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"LocalStorage.get failed: {e}") from e

    def open_read(self, namespace, key):
        key = _safe_key(key)
        p = self._path(namespace, key)
        if not p.exists():
            raise StorageError(f"not found: {namespace}/{key}")
        return open(p, "rb")

    def exists(self, namespace, key):
        return self._path(namespace, _safe_key(key)).exists()

    def stat(self, namespace, key):
        p = self._path(namespace, _safe_key(key))
        if not p.exists():
            return None
        try:
            s = p.stat()
            return StorageObject(
                key=_safe_key(key), size=int(s.st_size),
                last_modified=datetime.fromtimestamp(s.st_mtime),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("stat failed: %r", e)
            return None

    def delete(self, namespace, key):
        p = self._path(namespace, _safe_key(key))
        if not p.exists():
            return False
        try:
            if p.is_file():
                p.unlink()
            else:
                shutil.rmtree(p)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("LocalStorage.delete failed: %r", e)
            return False

    def list(self, namespace, *, prefix="", limit=1000):
        base = (self._root / namespace / _safe_key(prefix)) if prefix else self._root / namespace
        out: List[StorageObject] = []
        if not base.exists():
            return out
        # 支持目录遍历
        ns_root = self._root / namespace
        try:
            if base.is_file():
                rel = base.relative_to(ns_root).as_posix()
                s = base.stat()
                out.append(StorageObject(
                    key=rel, size=int(s.st_size),
                    last_modified=datetime.fromtimestamp(s.st_mtime),
                ))
                return out
            for p in base.rglob("*"):
                if not p.is_file():
                    continue
                rel = p.relative_to(ns_root).as_posix()
                s = p.stat()
                out.append(StorageObject(
                    key=rel, size=int(s.st_size),
                    last_modified=datetime.fromtimestamp(s.st_mtime),
                ))
                if len(out) >= int(limit):
                    break
        except Exception as e:  # noqa: BLE001
            logger.debug("list failed: %r", e)
        return out

    def presigned_url(self, namespace, key, *, expires_sec=3600):
        """LocalStorage 没有原生预签名；发放一个内部代理 URL 带短期 HMAC 令牌。

        真正的"不可伪造"在 ``storage_routes.stream`` 端点验证；这里只负责构造。
        """
        key = _safe_key(key)
        token = _local_sign(namespace, key, expires_sec)
        qs = urllib.parse.urlencode({
            "ns": namespace, "key": key, "token": token,
            "exp": int(time.time()) + int(expires_sec),
        })
        return f"/storage/stream?{qs}"

    # ----- 内省 -----

    def backend_info(self):
        info = {
            "type": "local",
            "root": str(self._root),
            "healthy": self._root.exists(),
            "namespaces": {},
        }
        try:
            for ns in (self._root.iterdir() if self._root.exists() else []):
                if not ns.is_dir():
                    continue
                count = 0
                total = 0
                for p in ns.rglob("*"):
                    if p.is_file():
                        count += 1
                        try:
                            total += p.stat().st_size
                        except Exception:  # noqa: BLE001
                            pass
                info["namespaces"][ns.name] = {
                    "objects": count,
                    "size_mb": round(total / (1024 * 1024), 2),
                }
        except Exception as e:  # noqa: BLE001
            info["error"] = str(e)
        return info


# ---------------------------------------------------------------------------
# 本地签名（给 LocalStorage 的预签名 URL 用）
# ---------------------------------------------------------------------------

def _local_sign(namespace: str, key: str, expires_sec: int) -> str:
    import hmac
    secret = _sign_secret()
    exp = int(time.time()) + int(expires_sec)
    msg = f"{namespace}|{key}|{exp}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def _sign_secret() -> bytes:
    try:
        from chayuan.settings import Settings
        sec = (getattr(Settings.basic_settings, "JWT_SECRET", "") or "").encode("utf-8")
        if sec:
            return sec
    except Exception:  # noqa: BLE001
        pass
    # 兜底：进程级常量，保证签名可验证
    return b"chayuan-local-storage-fallback-secret"


def verify_local_signature(namespace: str, key: str, exp: int, token: str) -> bool:
    import hmac
    if int(exp) < int(time.time()):
        return False
    secret = _sign_secret()
    msg = f"{namespace}|{key}|{int(exp)}".encode("utf-8")
    expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, token)
