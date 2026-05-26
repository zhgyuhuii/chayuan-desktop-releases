"""FastDFS RemoteSource 实现。

FastDFS 不是"目录文件系统",它是 file_id(group/M00/AB/CD/<hash>.<ext>)寻址的对象
存储 — 没有原生的"列目录"概念。我们通过两条路径解决浏览体验:

1) **清单 file**(推荐):用户在远端预先上传一份 manifest.txt(每行一个 file_id +
   可选 \t 友好名),把它当成"目录"。option `manifest_file_id` 给定后,browse 直接
   返回该清单里的全部条目(扁平,作为单层"目录")。
2) **本地目录映射**:option `local_root` + `mount_url_prefix` —— 老 FastDFS 客户
   端常配合 NFS / Nginx 共享一个本地路径(/var/fdfs/...)。给定 local_root 后,我
   们用 os.walk 浏览,下载时按 file_id 走 fastdfs 协议;两条腿走路兼容老部署。

任何一种模式,open_read 永远走 fdfs_client.download_to_buffer(file_id) — 拉的
是真实文件内容,不会跨 NFS 直读(NFS 缓存可能脏)。
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, Iterator, List, Optional

from .base import (
    BrowseResult,
    RemoteFile,
    RemoteSource,
    SourceConfig,
    SourceError,
    normalize_dir,
    parent_of,
)


class FastDFSSource(RemoteSource):
    kind = "fastdfs"

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        try:
            # 老 fdfs_client(py2 的)和新 py-fastdfs-client 不同名,试两次
            try:
                from fdfs_client.client import Fdfs_client  # type: ignore
                self._driver_name = "fdfs_client"
            except ImportError:
                from fastdfs_client.client import FastdfsClient as Fdfs_client  # type: ignore
                self._driver_name = "fastdfs_client"
        except ImportError as e:
            raise SourceError(
                "缺依赖:`pip install py-fdfs-client` 或 `pip install fastdfs-client-py`"
            ) from e

        opts = dict(config.options or {})
        trackers = opts.get("trackers") or []
        if isinstance(trackers, str):
            trackers = [t.strip() for t in trackers.split(",") if t.strip()]
        if not trackers:
            raise SourceError("FastDFS trackers 必填,形如 ['10.0.0.1:22122']")

        # fdfs_client 接受配置文件路径,我们用 NamedTemporaryFile 写一份临时 conf
        # 并保留路径供 client 复用 — 不在 / 上落文件,容器场景下无副作用。
        conf = "\n".join(f"tracker_server={t}" for t in trackers) + "\n"
        self._tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 - 生命周期 = self
            "w", suffix=".conf", delete=False, encoding="utf-8",
        )
        self._tmp.write(conf)
        self._tmp.flush()
        try:
            self._client = Fdfs_client(self._tmp.name)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"FastDFS client 初始化失败:{e}") from e

        self._manifest_file_id: Optional[str] = (opts.get("manifest_file_id") or "").strip() or None
        self._local_root: Optional[str] = opts.get("local_root") or None
        # 启动时拉一次 manifest 到内存(如果配置了),browse 直接走内存。
        self._manifest_cache: Optional[List[RemoteFile]] = None

    # —— 协议实现 ——

    def test(self) -> Dict[str, Any]:
        # 任何一种模式都尝试 ping 一下 tracker:用空 file_id query 做最小验证
        try:
            # py-fdfs-client 的 list_groups 是一次 tracker round-trip
            list_groups = getattr(self._client, "list_groups", None)
            if callable(list_groups):
                groups = list_groups()
            else:
                groups = None
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "msg": f"tracker 不可达:{e}"}
        info = {
            "ok": True,
            "msg": "tracker 已连通",
            "driver": self._driver_name,
            "manifest_file_id": self._manifest_file_id,
            "local_root": self._local_root,
        }
        if groups is not None:
            try:
                info["groups"] = list(groups) if not isinstance(groups, dict) else list(groups.keys())
            except Exception:  # noqa: BLE001
                pass
        if self._manifest_file_id and self._manifest_cache is None:
            self._reload_manifest()
        if self._manifest_cache is not None:
            info["manifest_count"] = len(self._manifest_cache)
        return info

    def browse(
        self,
        path: str = "",
        *,
        marker: Optional[str] = None,
        limit: int = 200,
    ) -> BrowseResult:
        # —— 模式 1:manifest ——
        if self._manifest_file_id:
            if self._manifest_cache is None:
                self._reload_manifest()
            entries = list(self._manifest_cache or [])
            return self._paginate(path, entries, marker, limit, root_label="(manifest)")

        # —— 模式 2:local_root ——
        if self._local_root:
            return self._browse_local(path, marker=marker, limit=limit)

        raise SourceError(
            "FastDFS 未启用浏览:请配置 manifest_file_id 或 local_root 至少一项"
        )

    @contextmanager
    def open_read(self, key: str) -> Iterator:
        # key 可能是 file_id(group1/M00/00/00/...) 或 local_root 内的相对路径
        if self._is_local_relative_key(key):
            full = os.path.join(self._local_root or "", key)
            f = open(full, "rb")  # noqa: SIM115 - context 由 caller 管
            try:
                yield f
            finally:
                f.close()
            return
        try:
            ret = self._client.download_to_buffer(key)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"FastDFS 下载 {key!r} 失败:{e}") from e
        # 不同 driver 返回值不一,统一抠出 bytes
        data = self._extract_buffer(ret)
        if data is None:
            raise SourceError(f"FastDFS 下载 {key!r} 返回空")
        yield BytesIO(data)

    def close(self) -> None:
        try:
            os.unlink(self._tmp.name)
        except Exception:  # noqa: BLE001
            pass

    # —— 内部 ——

    def _reload_manifest(self) -> None:
        if not self._manifest_file_id:
            self._manifest_cache = []
            return
        try:
            ret = self._client.download_to_buffer(self._manifest_file_id)
        except Exception as e:  # noqa: BLE001
            raise SourceError(f"manifest 拉取失败:{e}") from e
        raw = self._extract_buffer(ret) or b""
        text = raw.decode("utf-8", errors="ignore")
        rows: List[RemoteFile] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t", 1)
            file_id = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else file_id.rsplit("/", 1)[-1]
            rows.append(RemoteFile(
                key=file_id, name=name, size=0,
                modified=None, is_dir=False,
            ))
        self._manifest_cache = rows

    def _browse_local(self, path: str, *, marker: Optional[str], limit: int) -> BrowseResult:
        cwd = normalize_dir(path)
        root = os.path.abspath(self._local_root or "")
        target = os.path.abspath(os.path.join(root, cwd))
        # 防止 ../ 跳出 root
        if not target.startswith(root):
            raise SourceError("非法路径")
        if not os.path.isdir(target):
            raise SourceError(f"目录不存在:{cwd}")
        names = sorted(os.listdir(target))
        if marker:
            try:
                start = names.index(marker) + 1
                names = names[start:]
            except ValueError:
                pass
        entries: List[RemoteFile] = []
        truncated = False
        next_marker: Optional[str] = None
        for n in names:
            if len(entries) >= limit:
                truncated = True
                next_marker = n
                break
            full = os.path.join(target, n)
            is_dir = os.path.isdir(full)
            try:
                size = 0 if is_dir else os.path.getsize(full)
                mtime = datetime.fromtimestamp(os.path.getmtime(full))
            except OSError:
                continue
            rel_key = (cwd + n + ("/" if is_dir else "")).lstrip("/")
            entries.append(RemoteFile(
                key=rel_key, name=n, size=size, modified=mtime, is_dir=is_dir,
            ))
        entries.sort(key=lambda x: (0 if x.is_dir else 1, x.name.lower()))
        return BrowseResult(
            cwd=cwd, parent=parent_of(cwd),
            entries=entries, truncated=truncated, next_marker=next_marker,
        )

    def _paginate(
        self, path: str, entries: List[RemoteFile],
        marker: Optional[str], limit: int, root_label: str,
    ) -> BrowseResult:
        # manifest 模式没有目录树,所有文件都铺在根
        cwd = normalize_dir(path)
        if cwd:
            return BrowseResult(cwd=cwd, parent=parent_of(cwd), entries=[])
        start = 0
        if marker:
            for i, e in enumerate(entries):
                if e.key == marker:
                    start = i + 1
                    break
        page = entries[start:start + limit]
        truncated = (start + limit) < len(entries)
        next_marker = entries[start + limit - 1].key if truncated else None
        return BrowseResult(
            cwd="", parent=None, entries=list(page),
            truncated=truncated, next_marker=next_marker,
        )

    def _is_local_relative_key(self, key: str) -> bool:
        # FastDFS file_id 总是 group?/M??/... 形态;local 则是相对 local_root 的路径
        return bool(self._local_root) and ("/M" not in key.split("/")[1] if "/" in key else True)

    @staticmethod
    def _extract_buffer(ret: Any) -> Optional[bytes]:
        # 不同 driver 返回 dict / tuple / bytes;尽量抠出 bytes
        if ret is None:
            return None
        if isinstance(ret, (bytes, bytearray)):
            return bytes(ret)
        if isinstance(ret, dict):
            for k in ("Content", "content", "buffer", "data"):
                v = ret.get(k)
                if isinstance(v, (bytes, bytearray)):
                    return bytes(v)
        if isinstance(ret, (list, tuple)) and ret:
            for v in ret:
                if isinstance(v, (bytes, bytearray)):
                    return bytes(v)
        return None
