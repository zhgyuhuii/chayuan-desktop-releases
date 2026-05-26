"""MinioStorage — MinIO / S3-compatible 后端。

依赖：``pip install minio``（~200KB，无 boto3 全家桶的开销）。

- **bucket 策略**：每个 namespace 一个 bucket；名字 = ``{MINIO_BUCKET_PREFIX}-{namespace}``
  （可在 ``MINIO_BUCKETS`` 里覆盖单个）
- **自动建桶**：首次 put 时若 bucket 不存在自动创建（``make_bucket``）
- **预签名 URL**：直接用 MinIO SDK 的 ``presigned_get_object``
- **大文件流式**：``open_read`` 返回 urllib3 response stream，供 StreamingResponse 消费

没装 minio-py → 抛 ImportError；factory 捕获后回退 LocalStorage（fail-open）。
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from chayuan.server.file_storage.base import (
    FileStorage, StorageError, StorageObject, NS,
)

logger = logging.getLogger("chayuan.file_storage.minio")


def _ns_to_bucket(namespace: str, prefix: str, override_map: Optional[dict] = None) -> str:
    if override_map:
        v = override_map.get(namespace) or ""
        if v:
            return v.lower()
    # S3 bucket 命名规则：小写 + 连字符
    p = (prefix or "chayuan").lower()
    ns = namespace.replace("_", "-").lower()
    return f"{p}-{ns}"


class MinioStorage(FileStorage):
    name = "minio"

    def __init__(
        self, *,
        endpoint: str, access_key: str, secret_key: str,
        secure: bool = False, region: str = "us-east-1",
        bucket_prefix: str = "chayuan",
        bucket_overrides: Optional[Dict[str, str]] = None,
    ):
        try:
            from minio import Minio  # type: ignore
        except ImportError as e:
            raise StorageError(
                "未安装 minio 客户端：`pip install minio>=7.2`"
            ) from e

        # MinIO 客户端要求 endpoint 不带 scheme
        ep = endpoint.strip()
        if ep.startswith("https://"):
            ep = ep[len("https://"):]
            secure = True
        elif ep.startswith("http://"):
            ep = ep[len("http://"):]
            secure = False

        self._client = Minio(
            ep, access_key=access_key, secret_key=secret_key,
            secure=secure, region=region,
        )
        self._endpoint = ep
        self._secure = secure
        self._prefix = bucket_prefix or "chayuan"
        self._overrides = dict(bucket_overrides or {})
        self._bucket_cache: Dict[str, bool] = {}

    # ----- 工具 -----

    def _bucket(self, namespace: str) -> str:
        return _ns_to_bucket(namespace, self._prefix, self._overrides)

    def _ensure_bucket(self, namespace: str) -> str:
        name = self._bucket(namespace)
        if self._bucket_cache.get(name):
            return name
        try:
            if not self._client.bucket_exists(name):
                self._client.make_bucket(name)
            self._bucket_cache[name] = True
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"MinIO bucket {name} 就绪失败：{e}") from e
        return name

    # ----- 核心 -----

    def put(self, namespace, key, data, *, content_type="", metadata=None):
        bucket = self._ensure_bucket(namespace)
        try:
            if isinstance(data, (bytes, bytearray)):
                body = io.BytesIO(data)
                length = len(data)
            else:
                # 未知长度 → 读进内存；大文件应用方把 chunk 写到临时文件再传
                buf = data.read()
                body = io.BytesIO(buf)
                length = len(buf)
            result = self._client.put_object(
                bucket, key, body, length=length,
                content_type=content_type or "application/octet-stream",
                metadata=metadata or None,
            )
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"MinIO put failed: {e}") from e
        return StorageObject(
            key=key, size=length,
            last_modified=datetime.utcnow(),
            etag=getattr(result, "etag", "") or "",
            content_type=content_type,
        )

    def get(self, namespace, key):
        bucket = self._bucket(namespace)
        try:
            resp = self._client.get_object(bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"MinIO get failed: {e}") from e

    def open_read(self, namespace, key):
        bucket = self._bucket(namespace)
        try:
            resp = self._client.get_object(bucket, key)
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"MinIO open_read failed: {e}") from e
        # 返回的 urllib3 response 本身支持 .read()，但 context manager 语义
        # 不太标准；包一层使其可作 ``with`` 用。
        return _MinioReadCtx(resp)

    def exists(self, namespace, key):
        try:
            self._client.stat_object(self._bucket(namespace), key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def stat(self, namespace, key):
        try:
            st = self._client.stat_object(self._bucket(namespace), key)
            return StorageObject(
                key=key, size=int(getattr(st, "size", 0) or 0),
                last_modified=getattr(st, "last_modified", None),
                etag=getattr(st, "etag", "") or "",
                content_type=getattr(st, "content_type", "") or "",
            )
        except Exception:  # noqa: BLE001
            return None

    def delete(self, namespace, key):
        try:
            self._client.remove_object(self._bucket(namespace), key)
            return True
        except Exception as e:  # noqa: BLE001
            logger.debug("MinIO delete failed: %r", e)
            return False

    def list(self, namespace, *, prefix="", limit=1000):
        bucket = self._bucket(namespace)
        out: List[StorageObject] = []
        try:
            it = self._client.list_objects(bucket, prefix=prefix or None, recursive=True)
            for obj in it:
                out.append(StorageObject(
                    key=getattr(obj, "object_name", ""),
                    size=int(getattr(obj, "size", 0) or 0),
                    last_modified=getattr(obj, "last_modified", None),
                    etag=getattr(obj, "etag", "") or "",
                ))
                if len(out) >= int(limit):
                    break
        except Exception as e:  # noqa: BLE001
            logger.debug("MinIO list failed: %r", e)
        return out

    def presigned_url(self, namespace, key, *, expires_sec=3600):
        try:
            return self._client.presigned_get_object(
                self._bucket(namespace), key,
                expires=timedelta(seconds=int(expires_sec)),
            )
        except Exception as e:  # noqa: BLE001
            raise StorageError(f"MinIO presigned_url failed: {e}") from e

    # ----- 内省 -----

    def backend_info(self):
        info = {
            "type": "minio",
            "endpoint": self._endpoint,
            "secure": self._secure,
            "bucket_prefix": self._prefix,
            "healthy": False,
            "namespaces": {},
        }
        try:
            # 探活：列出已有 bucket（不抛出 = 连接 OK）
            buckets = list(self._client.list_buckets())
            info["healthy"] = True
            info["existing_buckets"] = [b.name for b in buckets]
        except Exception as e:  # noqa: BLE001
            info["error"] = str(e)
            return info
        # 每 namespace 概览
        for ns in NS.all():
            bucket = self._bucket(ns)
            count = 0
            total = 0
            try:
                if self._client.bucket_exists(bucket):
                    for obj in self._client.list_objects(bucket, recursive=True):
                        count += 1
                        total += int(getattr(obj, "size", 0) or 0)
            except Exception:  # noqa: BLE001
                pass
            info["namespaces"][ns] = {
                "bucket": bucket, "objects": count,
                "size_mb": round(total / (1024 * 1024), 2),
            }
        return info


class _MinioReadCtx:
    """minio get_object 结果适配为 ``with`` + ``.read()`` 用。"""
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, exc_type, exc, tb):
        try:
            self._resp.close()
            self._resp.release_conn()
        except Exception:  # noqa: BLE001
            pass
        return False

    # 让外部可以直接 stream.read() 而不 with
    def read(self, *a, **kw):
        return self._resp.read(*a, **kw)

    def close(self):
        try:
            self._resp.close()
            self._resp.release_conn()
        except Exception:  # noqa: BLE001
            pass
