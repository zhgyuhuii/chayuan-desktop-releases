"""``hf-mirror://OWNER/REPO`` 模型下载（hf-mirror.com 镜像 + REST 兜底）。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from chayuan_packaging.fetchers.base import (
    FetchOptions, FetchResult, Fetcher, _file_sha256,
)

logger = logging.getLogger("chayuan_packaging.fetchers.hf_mirror")

DEFAULT_ENDPOINT = "https://hf-mirror.com"


class HFMirrorFetcher(Fetcher):
    """优先用 ``huggingface_hub.snapshot_download``；失败回退到自写的 REST 调用。"""

    name = "hf-mirror"

    def fetch(self, opt: FetchOptions) -> FetchResult:
        repo = opt.source[len("hf-mirror://"):]
        single_file: Optional[str] = (
            opt.resource.file or opt.matrix.get("file") or None
        )
        target_dir = opt.cache_dir / "models" / opt.resource.name
        target_dir.mkdir(parents=True, exist_ok=True)

        if self.offline:
            if list(target_dir.rglob("*")):
                return FetchResult(
                    resource_name=opt.resource.name, platform=opt.platform,
                    local_path=target_dir, is_dir=True, skipped=False,
                    note="offline cache hit",
                )
            return FetchResult(
                resource_name=opt.resource.name, platform=opt.platform,
                local_path=target_dir, is_dir=True, skipped=True,
                note="offline & cache miss",
            )

        # 1) huggingface_hub
        try:
            return self._fetch_with_hub(repo, target_dir, single_file=single_file, opt=opt)
        except _HubMissing:
            logger.info("[hf-mirror] huggingface_hub 不可用，走 REST 兜底")
        except Exception as e:  # noqa: BLE001
            logger.warning("[hf-mirror] hub 失败：%r；走 REST 兜底", e)

        # 2) REST 兜底
        return self._fetch_with_rest(repo, target_dir, single_file=single_file, opt=opt)

    # ---- backends ------------------------------------------------------

    def _fetch_with_hub(self, repo: str, target_dir: Path,
                        *, single_file: Optional[str], opt: FetchOptions) -> FetchResult:
        try:
            from huggingface_hub import hf_hub_download, snapshot_download
        except ImportError as e:
            raise _HubMissing(str(e))

        if single_file:
            local = hf_hub_download(
                repo_id=repo, filename=single_file,
                local_dir=str(target_dir), local_dir_use_symlinks=False,
                endpoint=DEFAULT_ENDPOINT,
            )
            return FetchResult(
                resource_name=opt.resource.name, platform=opt.platform,
                local_path=Path(local), is_dir=False,
                sha256=_file_sha256(Path(local)),
                url=f"{DEFAULT_ENDPOINT}/{repo}/resolve/main/{single_file}",
            )
        local_dir = snapshot_download(
            repo_id=repo, local_dir=str(target_dir),
            local_dir_use_symlinks=False,
            endpoint=DEFAULT_ENDPOINT,
        )
        return FetchResult(
            resource_name=opt.resource.name, platform=opt.platform,
            local_path=Path(local_dir), is_dir=True,
            url=f"{DEFAULT_ENDPOINT}/{repo}",
        )

    def _fetch_with_rest(self, repo: str, target_dir: Path,
                         *, single_file: Optional[str], opt: FetchOptions) -> FetchResult:
        import httpx

        if single_file:
            url = f"{DEFAULT_ENDPOINT}/{repo}/resolve/main/{single_file}"
            dest = target_dir / single_file
            sha = self._download(url, dest, timeout=opt.timeout_sec,
                                 progress_label=opt.resource.name)
            return FetchResult(
                resource_name=opt.resource.name, platform=opt.platform,
                local_path=dest, is_dir=False, sha256=sha, url=url,
            )

        # 列文件再逐个下
        api = f"{DEFAULT_ENDPOINT}/api/models/{repo}"
        with httpx.Client(timeout=httpx.Timeout(60.0, read=60.0),
                          follow_redirects=True) as client:
            r = client.get(api)
            r.raise_for_status()
            siblings = r.json().get("siblings") or []
        for s in siblings:
            fname = s.get("rfilename")
            if not fname:
                continue
            url = f"{DEFAULT_ENDPOINT}/{repo}/resolve/main/{fname}"
            dest = target_dir / fname
            dest.parent.mkdir(parents=True, exist_ok=True)
            self._download(url, dest, timeout=opt.timeout_sec,
                           progress_label=f"{opt.resource.name}/{fname}")
        return FetchResult(
            resource_name=opt.resource.name, platform=opt.platform,
            local_path=target_dir, is_dir=True,
            url=f"{DEFAULT_ENDPOINT}/{repo}",
        )


class _HubMissing(RuntimeError):
    pass


__all__ = ["HFMirrorFetcher"]
