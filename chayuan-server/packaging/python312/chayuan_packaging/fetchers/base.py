"""Fetcher ABC + 通用下载工具。"""
from __future__ import annotations

import abc
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("chayuan_packaging.fetchers.base")

CHUNK = 1024 * 1024  # 1 MiB


@dataclass
class FetchOptions:
    resource: Any                       # chayuan_packaging.manifest.Resource (forward)
    platform: str                       # PlatformKey
    cache_dir: Path
    source: str                         # 实际 source (matrix 覆盖后的)
    matrix: Dict[str, Any] = field(default_factory=dict)
    timeout_sec: float = 600.0


@dataclass
class FetchResult:
    """下载完成后的产物描述。"""

    resource_name: str
    platform: str
    local_path: Path                    # cache 内的实际文件 / 目录
    url: str = ""                       # 真正下载用的 URL（github 走 redirect 后）
    sha256: str = ""
    is_dir: bool = False                # 资源是否已经是目录（如 hf snapshot）
    skipped: bool = False               # offline 时未命中 cache 的占位
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "resource": self.resource_name, "platform": self.platform,
            "local_path": str(self.local_path), "url": self.url,
            "sha256": self.sha256, "is_dir": self.is_dir,
            "skipped": self.skipped, "note": self.note,
        }


class Fetcher(abc.ABC):
    """下载器 ABC。

    子类只需实现 :meth:`fetch`；下载与校验工具方法在基类提供。
    """

    name: str = "base"

    def __init__(self, *, offline: bool = False) -> None:
        self.offline = offline

    @abc.abstractmethod
    def fetch(self, opt: FetchOptions) -> FetchResult:  # pragma: no cover - abstract
        raise NotImplementedError

    # ---- 通用工具 ------------------------------------------------------

    def _interp(self, template: str, mapping: Dict[str, Any]) -> str:
        """简单 ``{var}`` 替换；缺 key 抛错避免悄悄生成错误 URL。"""
        def _sub(m: re.Match) -> str:
            key = m.group(1)
            if key not in mapping:
                raise KeyError(f"template var {{{key}}} missing in mapping {mapping!r}")
            return str(mapping[key])
        return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", _sub, template)

    def _cache_path(self, opt: FetchOptions, filename: str) -> Path:
        d = opt.cache_dir / opt.resource.kind / opt.resource.name / opt.platform
        d.mkdir(parents=True, exist_ok=True)
        return d / filename

    def _download(
        self,
        url: str,
        dest: Path,
        *,
        timeout: float = 600.0,
        progress_label: str | None = None,
    ) -> str:
        """断点续传下载；返回 sha256。"""
        import httpx

        if self.offline:
            if dest.is_file():
                logger.info("[fetcher] offline 命中缓存 %s", dest)
                return _file_sha256(dest)
            raise RuntimeError(f"offline 模式但缓存缺失：{dest}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        done = tmp.stat().st_size if tmp.exists() else 0

        headers = {"Range": f"bytes={done}-"} if done else {}
        timeout_obj = httpx.Timeout(timeout, read=timeout)
        h = hashlib.sha256()

        # 先把已经写到 .part 的部分喂进 hash
        if done:
            with tmp.open("rb") as f:
                while chunk := f.read(CHUNK):
                    h.update(chunk)

        with httpx.Client(timeout=timeout_obj, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 416 and dest.exists():
                    # 已经下完
                    return _file_sha256(dest)
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", "0") or 0) + done
                label = progress_label or dest.name
                logger.info("[fetcher] 下载 %s %s (%.1fMB)", label, url,
                            total / 1024 / 1024 if total else 0)
                with tmp.open("ab") as f:
                    for chunk in resp.iter_bytes(chunk_size=CHUNK):
                        f.write(chunk)
                        h.update(chunk)
                        done += len(chunk)
        tmp.replace(dest)
        return h.hexdigest()


def _file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


__all__ = ["FetchOptions", "FetchResult", "Fetcher"]
