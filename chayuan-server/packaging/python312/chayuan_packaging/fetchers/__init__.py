"""Fetcher 装配与统一入口。

调度逻辑
========

1. 从 ``Resource.source`` 前缀决定走哪个 fetcher：
   - ``github://OWNER/REPO``    → :mod:`.github`
   - ``hf-mirror://REPO``        → :mod:`.hf_mirror`
   - ``local://PATH``            → :mod:`.local`（无网络）
   - 其它（``http://`` / ``https://`` / 模板带 {var}） → :mod:`.http`
2. fetcher 返回 :class:`FetchResult`，packager 把产物从 cache 解压到 ``Resource.dest``。
3. 单元测试 / 离线模式：注入 ``offline=True`` 时 fetcher 仅查 cache，不发任何 HTTP。

为什么不直接调 ``urllib.urlretrieve``？
* 进度条 / 大文件分段下载（断点续传 ``Range`` 头）；
* sha256 校验；
* 401/429/5xx 重试 + 指数退避；
* httpx 复用连接池，对多 asset 仓库（如 vendor 一次拉 10 个）显著提速。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from chayuan_packaging.fetchers.base import FetchOptions, FetchResult, Fetcher

if TYPE_CHECKING:  # pragma: no cover
    from chayuan_packaging.manifest import Resource
    from chayuan_packaging.platform_info import PlatformKey

logger = logging.getLogger("chayuan_packaging.fetchers")


def get_fetcher(source: str, *, offline: bool = False) -> Fetcher:
    """根据 ``source`` 协议前缀挑 fetcher。"""
    s = (source or "").strip()
    if s.startswith("github://"):
        from chayuan_packaging.fetchers.github import GithubFetcher
        return GithubFetcher(offline=offline)
    if s.startswith("hf-mirror://"):
        from chayuan_packaging.fetchers.hf_mirror import HFMirrorFetcher
        return HFMirrorFetcher(offline=offline)
    if s.startswith("local://"):
        from chayuan_packaging.fetchers.local import LocalFetcher
        return LocalFetcher(offline=offline)
    # 默认走 HTTP，让 yaml 里直接放 URL 模板（含 {version} {os} {arch} {ext}）
    from chayuan_packaging.fetchers.http import HttpFetcher
    return HttpFetcher(offline=offline)


def fetch_resource(
    resource: "Resource",
    *,
    platform: "PlatformKey",
    cache_dir,
    offline: bool = False,
) -> FetchResult:
    """统一入口：拿一个 :class:`Resource` + platform → 下载到 cache。

    返回 :class:`FetchResult`（含本地路径、sha256、最终下载 url）。
    """
    matrix = resource.matrix_for(platform) or {}
    # matrix 可以覆盖 source（例如 redis 在 win 上换成 github 仓库）
    source = str(matrix.get("source") or resource.source)
    fetcher = get_fetcher(source, offline=offline)
    opt = FetchOptions(
        resource=resource,
        platform=platform,
        cache_dir=cache_dir,
        source=source,
        matrix=matrix,
    )
    return fetcher.fetch(opt)


__all__ = ["get_fetcher", "fetch_resource", "FetchOptions", "FetchResult", "Fetcher"]
