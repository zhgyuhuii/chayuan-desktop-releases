"""Model downloader.

Backend strategy:

  * If `huggingface_hub` is importable and reachable → use `snapshot_download`
    with HF_ENDPOINT pointed at the chosen mirror (hf-mirror.com by default).
    Resume + sha verification come for free.
  * Otherwise, fall back to a plain httpx-based downloader that fetches files
    listed by the Hub REST API (`/api/models/<repo>`). This means the desktop
    installer doesn't have to ship `huggingface_hub`'s heavy dependency tree
    if disk-space is tight (e.g. the `lite` release).

Cancellation:

  Pass an `asyncio.Event` (or `threading.Event`) via `DownloadOptions.cancel`.
  The downloader checks it between files.
"""
from __future__ import annotations

import shutil
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from chayuan_core import ensure_dirs, model_dir_for
from chayuan_core.events import TOPIC_MODEL_DOWNLOAD, get_bus
from chayuan_identify import identify
from chayuan_modelmgr.mirrors import resolve_mirror
from chayuan_modelmgr.progress import ProgressEvent, ProgressSink
from chayuan_modelmgr.verifier import write_manifest


@dataclass
class DownloadOptions:
    repo: str
    category: str | None = None        # if None, auto-detect post-download
    mirror: str | None = None
    revision: str | None = None
    allow_patterns: list[str] | None = None
    ignore_patterns: list[str] | None = None
    cancel: threading.Event | None = None
    progress_cb: Callable[[ProgressEvent], None] | None = None
    write_manifest: bool = True


@dataclass
class DownloadResult:
    repo: str
    dest: Path
    bytes_total: int = 0
    files: list[str] = field(default_factory=list)
    manifest_path: Path | None = None


class ModelDownloader:
    def __init__(self, options: DownloadOptions) -> None:
        self.opt = options
        self.mirror = resolve_mirror(options.mirror)
        self.sink = ProgressSink(options.progress_cb)

    # ---- public --------------------------------------------------------

    def run(self) -> DownloadResult:
        ensure_dirs()
        category = self.opt.category or "_pending"
        dest = model_dir_for(category, self.opt.repo)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            self._announce("running", "starting download")
            self._download_with_hub(dest)
        except _HubMissingError:
            self._announce("running", "huggingface_hub unavailable, using REST fallback")
            self._download_with_rest(dest)
        except Exception as e:
            self._announce("error", f"download failed: {e}")
            raise

        if self.opt.category is None:
            meta = identify(dest)
            if meta is not None and meta.category != category:
                final = model_dir_for(meta.category, self.opt.repo)
                final.parent.mkdir(parents=True, exist_ok=True)
                if final.exists():
                    shutil.rmtree(final)
                shutil.move(str(dest), str(final))
                dest = final

        files = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
        total_bytes = sum((dest / f).stat().st_size for f in files)
        manifest_path = None
        if self.opt.write_manifest:
            try:
                manifest_path = write_manifest(dest, source="downloaded")
            except Exception:
                manifest_path = None

        self._announce("done", "finished", bytes_done=total_bytes, bytes_total=total_bytes)
        return DownloadResult(
            repo=self.opt.repo, dest=dest, bytes_total=total_bytes,
            files=files, manifest_path=manifest_path,
        )

    # ---- backends ------------------------------------------------------

    def _download_with_hub(self, dest: Path) -> None:
        try:
            from huggingface_hub import snapshot_download
        except Exception as e:  # ImportError or transient
            raise _HubMissingError(str(e)) from e

        kwargs: dict = {
            "repo_id": self.opt.repo,
            "local_dir": str(dest),
            "local_dir_use_symlinks": False,
            "endpoint": self.mirror.endpoint,
        }
        if self.opt.revision:
            kwargs["revision"] = self.opt.revision
        if self.opt.allow_patterns:
            kwargs["allow_patterns"] = self.opt.allow_patterns
        if self.opt.ignore_patterns:
            kwargs["ignore_patterns"] = self.opt.ignore_patterns

        if self._cancelled():
            raise InterruptedError("download cancelled before start")
        snapshot_download(**kwargs)

    def _download_with_rest(self, dest: Path) -> None:
        """REST fallback；按 mirror.kind 分流到 HF / modelscope。"""
        if self.mirror.kind == "modelscope":
            return self._download_with_rest_modelscope(dest)
        return self._download_with_rest_hf(dest)

    def _download_with_rest_hf(self, dest: Path) -> None:
        import httpx

        api = f"{self.mirror.endpoint.rstrip('/')}/api/models/{self.opt.repo}"
        with httpx.Client(timeout=httpx.Timeout(60.0, read=60.0), follow_redirects=True) as client:
            r = client.get(api)
            r.raise_for_status()
            siblings = r.json().get("siblings") or []
            files = self._filter_patterns([s["rfilename"] for s in siblings])
            for fname in files:
                if self._cancelled():
                    raise InterruptedError("cancelled")
                url = f"{self.mirror.endpoint.rstrip('/')}/{self.opt.repo}/resolve/{self.opt.revision or 'main'}/{fname}"
                self._fetch_one(client, url, fname, dest)

    def _download_with_rest_modelscope(self, dest: Path) -> None:
        """ModelScope REST：

        * 列文件: ``GET /api/v1/models/{owner}/{name}/repo/files?Recursive=True&Revision=<rev>``
          -> 返回 ``{"Data":{"Files":[{"Path": "...", "Type": "blob", ...}, ...]}}``
        * 拉文件: ``GET /api/v1/models/{owner}/{name}/repo?Revision=<rev>&FilePath=<file>``
          (302 → OSS 签名 URL)
        """
        import httpx

        from chayuan_modelmgr.mirrors import modelscope_repo_id

        rev = self.opt.revision or "master"
        # HF repo id (Owner/Name) → ModelScope (owner/Name); ModelScope owner 段
        # 多数模型小写,这里复用 mirrors.modelscope_repo_id 的覆盖表。
        owner_name = modelscope_repo_id(self.opt.repo)
        list_url = (
            f"{self.mirror.endpoint.rstrip('/')}/api/v1/models/{owner_name}"
            f"/repo/files?Recursive=True&Revision={rev}"
        )
        with httpx.Client(timeout=httpx.Timeout(60.0, read=60.0), follow_redirects=True) as client:
            r = client.get(list_url)
            r.raise_for_status()
            data = r.json().get("Data", {}) or {}
            entries = data.get("Files", []) or []
            paths = [e.get("Path") for e in entries if e.get("Type") == "blob" and e.get("Path")]
            files = self._filter_patterns(paths)
            for fname in files:
                if self._cancelled():
                    raise InterruptedError("cancelled")
                url = (
                    f"{self.mirror.endpoint.rstrip('/')}/api/v1/models/{owner_name}/repo"
                    f"?Revision={rev}&FilePath={fname}"
                )
                self._fetch_one(client, url, fname, dest)

    def _fetch_one(self, client, url: str, fname: str, dest: Path) -> None:
        target = dest / fname
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        done = tmp.stat().st_size if tmp.exists() else 0
        with client.stream(
            "GET", url, headers={"Range": f"bytes={done}-"} if done else None
        ) as resp:
            resp.raise_for_status()
            total = done + int(resp.headers.get("content-length", "0") or 0)
            with tmp.open("ab") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                    if self._cancelled():
                        raise InterruptedError("cancelled mid-file")
                    f.write(chunk)
                    done += len(chunk)
                    self._announce("running", f"downloading {fname}",
                                   filename=fname, bytes_done=done, bytes_total=total)
        tmp.replace(target)

    # ---- helpers -------------------------------------------------------

    def _filter_patterns(self, names: Iterable[str]) -> list[str]:
        import fnmatch

        names = list(names)
        if self.opt.allow_patterns:
            names = [n for n in names if any(fnmatch.fnmatch(n, p) for p in self.opt.allow_patterns)]
        if self.opt.ignore_patterns:
            names = [n for n in names if not any(fnmatch.fnmatch(n, p) for p in self.opt.ignore_patterns)]
        return names

    def _cancelled(self) -> bool:
        return self.opt.cancel is not None and self.opt.cancel.is_set()

    def _announce(self, state: str, message: str, **extra) -> None:
        ev = ProgressEvent(repo=self.opt.repo, state=state, message=message, **extra)
        self.sink.update(ev)


class _HubMissingError(RuntimeError):
    """Internal flag used to switch backends — never raised to callers."""


def pull(
    repo: str,
    *,
    category: str | None = None,
    mirror: str | None = None,
    revision: str | None = None,
    allow_patterns: list[str] | None = None,
    ignore_patterns: list[str] | None = None,
    progress_cb: Callable[[ProgressEvent], None] | None = None,
    cancel: threading.Event | None = None,
) -> DownloadResult:
    """Convenience wrapper. Equivalent to instantiating ModelDownloader."""
    opt = DownloadOptions(
        repo=repo,
        category=category,
        mirror=mirror,
        revision=revision,
        allow_patterns=allow_patterns,
        ignore_patterns=ignore_patterns,
        progress_cb=progress_cb,
        cancel=cancel,
    )
    res = ModelDownloader(opt).run()
    get_bus().publish(TOPIC_MODEL_DOWNLOAD,
                      {"repo": repo, "state": "done", "dest": str(res.dest), "bytes_total": res.bytes_total})
    return res
