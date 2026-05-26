"""``github://OWNER/REPO`` 取 latest release 中匹配的 asset。"""
from __future__ import annotations

import fnmatch
import logging
import re
from typing import Optional

from chayuan_packaging.fetchers.base import FetchOptions, FetchResult, Fetcher

logger = logging.getLogger("chayuan_packaging.fetchers.github")


class GithubFetcher(Fetcher):
    name = "github"

    def fetch(self, opt: FetchOptions) -> FetchResult:
        owner_repo = opt.source[len("github://"):]
        if "/" not in owner_repo:
            raise ValueError(f"github source 必须 'github://owner/repo'，得到 {opt.source!r}")

        version = opt.resource.version or _resolve_latest(owner_repo, offline=self.offline)
        # asset_pattern：先看 matrix 里 ``asset`` / ``asset_pattern`` / ``raw``
        m = opt.matrix
        asset_pattern: Optional[str] = (
            m.get("asset") or m.get("asset_pattern") or m.get("raw") or ""
        )
        if not asset_pattern:
            raise ValueError(
                f"resource {opt.resource.name} platform={opt.platform} 没有定义 asset 模式"
            )

        # 替换 {version} {os} {arch} {ext} 等占位符
        var_map = dict(m)
        var_map.setdefault("version", version)
        asset_pattern = self._interp(asset_pattern, var_map)

        # 解析 latest release manifest
        url = _find_asset_url(owner_repo, version, asset_pattern, offline=self.offline)
        filename = url.rsplit("/", 1)[-1] or asset_pattern.replace("/", "_")
        dest = self._cache_path(opt, filename)

        if dest.is_file() and dest.stat().st_size > 0:
            logger.info("[github] 命中缓存 %s", dest)
            from chayuan_packaging.fetchers.base import _file_sha256
            return FetchResult(
                resource_name=opt.resource.name, platform=opt.platform,
                local_path=dest, url=url, sha256=_file_sha256(dest),
            )

        sha = self._download(url, dest, timeout=opt.timeout_sec,
                             progress_label=f"{opt.resource.name}/{filename}")
        return FetchResult(
            resource_name=opt.resource.name, platform=opt.platform,
            local_path=dest, url=url, sha256=sha,
        )


def _resolve_latest(owner_repo: str, *, offline: bool = False) -> str:
    """github API 取最新 release tag。

    offline 时回退到字符串 ``"latest"``，让 caller 自己组合下载链接（多数 release
    asset URL 含 ``/releases/latest/download/...`` 形式可用）。
    """
    if offline:
        return "latest"
    import httpx

    api = f"https://api.github.com/repos/{owner_repo}/releases/latest"
    try:
        resp = httpx.get(api, timeout=20.0, follow_redirects=True,
                         headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        return str(resp.json().get("tag_name") or "latest")
    except Exception as e:  # noqa: BLE001
        logger.warning("[github] 解析 latest 失败：%r；走 'latest' 兜底", e)
        return "latest"


def _find_asset_url(owner_repo: str, version: str, pattern: str,
                    *, offline: bool = False) -> str:
    """在 release 的 assets 列表里 fnmatch 找一个 URL。

    pattern 可包含通配符（``*``）。匹配多个时取第一个；找不到时尝试拼
    ``https://github.com/owner/repo/releases/download/<tag>/<pattern>``
    （把通配符当字面量），让用户自己在 yaml 里把 pattern 写成完整文件名以避免歧义。
    """
    if offline:
        return f"https://github.com/{owner_repo}/releases/download/{version}/{pattern}"

    import httpx
    api = f"https://api.github.com/repos/{owner_repo}/releases/tags/{version}"
    if version == "latest":
        api = f"https://api.github.com/repos/{owner_repo}/releases/latest"

    try:
        resp = httpx.get(api, timeout=30.0, follow_redirects=True,
                         headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        for asset in resp.json().get("assets", []) or []:
            name = str(asset.get("name", ""))
            if fnmatch.fnmatch(name, pattern) or _semver_glob(pattern, name):
                return str(asset["browser_download_url"])
    except Exception as e:  # noqa: BLE001
        logger.warning("[github] 列出 %s assets 失败：%r；走 fallback URL",
                       owner_repo, e)

    # 兜底：纯静态拼接（要求 pattern 已经是完整文件名）
    return f"https://github.com/{owner_repo}/releases/download/{version}/{pattern}"


_SEMVER = re.compile(r"\{(?:semver|version|x)\}")


def _semver_glob(pattern: str, name: str) -> bool:
    """把 ``foo-{semver}-bin.zip`` 当成 ``foo-*-bin.zip`` 再 fnmatch。"""
    return bool(_SEMVER.search(pattern)) and fnmatch.fnmatch(
        name, _SEMVER.sub("*", pattern)
    )


__all__ = ["GithubFetcher"]
