"""MinIO / S3 兼容存储 RemoteSource 实现。

bucket + prefix 双轴定位:
- options.bucket:必填,目标 bucket 名。
- 浏览路径相对 bucket 根 — 即 path='docs/' 表示列出 bucket/docs/ 下的对象。

为什么直接用 minio SDK 而不是复用 file_storage/minio.py:
- file_storage 是"namespace → bucket"的写入抽象,不暴露 list_objects(prefix=, delimiter=)
  这种伪文件夹枚举,前端目录浏览体验会很差;
- 这里就老老实实拿 SDK 用 delimiter='/' + CommonPrefixes 做单层 list,与浏览器
  里看 MinIO Console 一致。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, Optional

from .base import (
    BrowseResult,
    RemoteFile,
    RemoteSource,
    SourceConfig,
    SourceError,
    normalize_dir,
    parent_of,
)


class MinioSource(RemoteSource):
    kind = "minio"

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        try:
            from minio import Minio  # type: ignore
        except ImportError as e:
            raise SourceError("缺依赖:`pip install minio>=7.2`") from e

        opts = dict(config.options or {})
        endpoint = (opts.get("endpoint") or "").strip()
        if not endpoint:
            raise SourceError("MinIO endpoint 必填,如 'minio:9000' 或 'https://s3.example.com'")
        secure = bool(opts.get("secure", False))
        if endpoint.startswith("https://"):
            endpoint = endpoint[len("https://"):]
            secure = True
        elif endpoint.startswith("http://"):
            endpoint = endpoint[len("http://"):]
            secure = False

        self._bucket = (opts.get("bucket") or "").strip()
        if not self._bucket:
            raise SourceError("MinIO bucket 必填")

        self._client = Minio(
            endpoint,
            access_key=opts.get("access_key") or None,
            secret_key=opts.get("secret_key") or None,
            secure=secure,
            region=opts.get("region") or "us-east-1",
        )
        self._endpoint = endpoint
        self._secure = secure

    # —— 协议实现 ——

    def test(self) -> Dict[str, Any]:
        try:
            exists = self._client.bucket_exists(self._bucket)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "msg": f"连接失败:{e}"}
        if not exists:
            return {"ok": False, "msg": f"bucket '{self._bucket}' 不存在"}
        # 顺手数一下根目录条目数,UI 可以预告"≈N 个对象"
        approx = 0
        try:
            for _ in zip(range(20), self._client.list_objects(self._bucket, recursive=False)):
                approx += 1
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": True,
            "msg": f"已连接 {self._endpoint}/{self._bucket}",
            "endpoint": self._endpoint,
            "bucket": self._bucket,
            "secure": self._secure,
            "root_entries_sample": approx,
        }

    def browse(
        self,
        path: str = "",
        *,
        marker: Optional[str] = None,
        limit: int = 200,
    ) -> BrowseResult:
        cwd = normalize_dir(path)
        entries: list[RemoteFile] = []
        truncated = False
        next_marker: Optional[str] = None

        try:
            it = self._client.list_objects(
                self._bucket,
                prefix=cwd or None,
                recursive=False,
                start_after=marker or None,
            )
            count = 0
            for obj in it:
                if count >= limit:
                    truncated = True
                    next_marker = getattr(obj, "object_name", None)
                    break
                count += 1
                # MinIO 把"目录"用 object_name='a/b/' + is_dir=True 表示
                name = (getattr(obj, "object_name", "") or "")
                if not name:
                    continue
                if getattr(obj, "is_dir", False):
                    base = name[len(cwd):].rstrip("/")
                    entries.append(RemoteFile(
                        key=name, name=base, size=0,
                        modified=None, is_dir=True,
                    ))
                else:
                    base = name[len(cwd):]
                    entries.append(RemoteFile(
                        key=name,
                        name=base,
                        size=int(getattr(obj, "size", 0) or 0),
                        modified=getattr(obj, "last_modified", None),
                        is_dir=False,
                        etag=str(getattr(obj, "etag", "") or ""),
                    ))
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"列目录失败:{e}") from e

        # 目录在前,文件在后;同类按名称升序
        entries.sort(key=lambda x: (0 if x.is_dir else 1, x.name.lower()))
        return BrowseResult(
            cwd=cwd, parent=parent_of(cwd),
            entries=entries, truncated=truncated, next_marker=next_marker,
        )

    def stat(self, key: str) -> Optional[RemoteFile]:
        try:
            st = self._client.stat_object(self._bucket, key)
        except Exception:  # noqa: BLE001
            return None
        return RemoteFile(
            key=key,
            name=key.rsplit("/", 1)[-1],
            size=int(getattr(st, "size", 0) or 0),
            modified=getattr(st, "last_modified", None),
            is_dir=False,
            etag=str(getattr(st, "etag", "") or ""),
        )

    @contextmanager
    def open_read(self, key: str) -> Iterator:
        try:
            resp = self._client.get_object(self._bucket, key)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"读对象 {key!r} 失败:{e}") from e
        try:
            yield resp
        finally:
            try:
                resp.close()
                resp.release_conn()
            except Exception:  # noqa: BLE001
                pass

    def walk(self, path: str = "", *, page_size: int = 500):
        """覆盖默认 walk,改用 list_objects(recursive=True),一次拉穿目录树。

        Quad: 100 万对象时,默认实现要 N=深度 次往返;recursive 一次搞定,吞吐
        是数量级差距。
        """
        cwd = normalize_dir(path)
        try:
            for obj in self._client.list_objects(self._bucket, prefix=cwd or None, recursive=True):
                name = getattr(obj, "object_name", "") or ""
                if not name or name.endswith("/"):
                    continue
                base = name.rsplit("/", 1)[-1]
                yield RemoteFile(
                    key=name,
                    name=base,
                    size=int(getattr(obj, "size", 0) or 0),
                    modified=getattr(obj, "last_modified", None),
                    is_dir=False,
                    etag=str(getattr(obj, "etag", "") or ""),
                )
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"递归列目录失败:{e}") from e
