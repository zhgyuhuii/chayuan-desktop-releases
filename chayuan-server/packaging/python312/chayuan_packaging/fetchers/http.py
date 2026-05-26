"""通用 HTTP / HTTPS fetcher（layout.yaml 里直接写带 ``{var}`` 的 URL 模板）。

支持的占位符：``{version}`` / ``{os}`` / ``{arch}`` / ``{ext}`` /
任何在 ``matrix[platform]`` 字典里给出的 key。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from chayuan_packaging.fetchers.base import (
    FetchOptions, FetchResult, Fetcher, _file_sha256,
)

logger = logging.getLogger("chayuan_packaging.fetchers.http")


class HttpFetcher(Fetcher):
    name = "http"

    def fetch(self, opt: FetchOptions) -> FetchResult:
        if not opt.source:
            raise ValueError(f"resource {opt.resource.name} 没有 source")

        # 模板替换变量来源（按优先级）
        mapping: Dict[str, Any] = {
            "version": opt.resource.version,
            "name": opt.resource.name,
        }
        # matrix 里的字典字段直接合并（如 {ext: zip, platform_tag: linux-x64}）
        for k, v in (opt.matrix or {}).items():
            if isinstance(v, (str, int, float, bool)):
                mapping[k] = v
        # platform key 拆开
        os_, arch = opt.platform.split("-", 1)
        mapping.setdefault("os", os_)
        mapping.setdefault("arch", arch)

        url = self._interp(opt.source, mapping)
        filename = url.rsplit("/", 1)[-1] or f"{opt.resource.name}.bin"
        dest = self._cache_path(opt, filename)

        if dest.is_file() and dest.stat().st_size > 0:
            logger.info("[http] 命中缓存 %s", dest)
            return FetchResult(
                resource_name=opt.resource.name, platform=opt.platform,
                local_path=dest, url=url, sha256=_file_sha256(dest),
            )
        sha = self._download(url, dest, timeout=opt.timeout_sec,
                             progress_label=opt.resource.name)
        return FetchResult(
            resource_name=opt.resource.name, platform=opt.platform,
            local_path=dest, url=url, sha256=sha,
        )


__all__ = ["HttpFetcher"]
