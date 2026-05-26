"""``local://relative/path`` —— 资源已经在仓库里的占位 fetcher。"""
from __future__ import annotations

from pathlib import Path

from chayuan_packaging.fetchers.base import (
    FetchOptions, FetchResult, Fetcher, _file_sha256,
)


class LocalFetcher(Fetcher):
    name = "local"

    def fetch(self, opt: FetchOptions) -> FetchResult:
        rel = opt.source[len("local://"):]
        # 仓库根 = packaging/python312/ 的上两层
        repo_root = (Path(__file__).resolve().parents[3])
        path = repo_root / rel
        if not path.exists():
            return FetchResult(
                resource_name=opt.resource.name, platform=opt.platform,
                local_path=path, skipped=True,
                note=f"local source not found: {path}",
            )
        is_dir = path.is_dir()
        return FetchResult(
            resource_name=opt.resource.name, platform=opt.platform,
            local_path=path, is_dir=is_dir,
            sha256=_file_sha256(path) if not is_dir else "",
            url=f"local://{rel}",
        )


__all__ = ["LocalFetcher"]
