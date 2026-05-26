"""95-3:把扫描 diff 按 mime 分发到 doc_kb / image_src。

target 字符串协议(与 94 ``coll:*`` 对齐):
* ``coll:<id>``       — 集合(自动按 mime 路由到子 KB)
* ``doc:<kb_name>``   — 文档 KB
* ``src:<id>``        — image source

mime 判定:按扩展名简单 map(覆盖 90% 场景);后续可加 python-magic。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from chayuan.server.folder_sync.scanner import FileRecord, ScanDiff

logger = logging.getLogger("chayuan.folder_sync.uploader")


# 扩展名 → 类别(document / image)。其它返 None,跳过该文件。
_EXT_TO_KIND: Dict[str, str] = {
    # documents
    ".pdf": "document", ".doc": "document", ".docx": "document",
    ".xls": "document", ".xlsx": "document",
    ".ppt": "document", ".pptx": "document",
    ".txt": "document", ".md": "document", ".markdown": "document",
    ".html": "document", ".htm": "document",
    ".csv": "document", ".tsv": "document", ".json": "document",
    # images
    ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".webp": "image", ".gif": "image", ".bmp": "image",
    ".tiff": "image", ".tif": "image", ".heic": "image",
}


def kind_for_path(path: str) -> Optional[str]:
    ext = Path(path).suffix.lower()
    return _EXT_TO_KIND.get(ext)


@dataclass
class UploadResult:
    successful_paths: set = field(default_factory=set)
    summary: Dict[str, int] = field(default_factory=lambda: {
        "added_indexed": 0, "modified_reindexed": 0,
        "removed_purged": 0, "skipped_unsupported": 0,
        "errors": 0,
    })
    errors: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class _ResolvedTarget:
    """target 解析后的派发表。"""
    doc_kb: str = ""    # 文档库名
    src_id: int = 0     # image source id
    via_collection: bool = False


def resolve_target(target: str) -> _ResolvedTarget:
    """target 字符串解析。

    * ``coll:<id|name>``  → 反查集合,取它的 doc / image 子 KB
    * ``doc:<name>``      → doc_kb=name
    * ``src:<id>``        → src_id=id
    """
    target = (target or "").strip()
    if not target:
        return _ResolvedTarget()
    if target.startswith("coll:"):
        ident = target[5:].strip()
        try:
            from chayuan.server.db.repository import (
                kb_collection_repository as _coll_repo,
            )
            coll = None
            if ident.isdigit():
                coll = _coll_repo.get_collection(int(ident))
            else:
                coll = _coll_repo.get_collection_by_name(ident)
                if coll:
                    coll = _coll_repo.get_collection(coll["id"])
            if not coll:
                return _ResolvedTarget()
            r = _ResolvedTarget(via_collection=True)
            for m in coll.get("members") or []:
                if m["kind"] == "document" and not r.doc_kb:
                    r.doc_kb = m["ku_id"]
                elif m["kind"] == "image" and r.src_id == 0:
                    ku = m["ku_id"]
                    if isinstance(ku, str) and ku.startswith("src:"):
                        try:
                            r.src_id = int(ku[4:])
                        except ValueError:
                            pass
            return r
        except Exception as e:  # noqa: BLE001
            logger.warning("[uploader] resolve coll target failed: %r", e)
            return _ResolvedTarget()
    if target.startswith("doc:"):
        return _ResolvedTarget(doc_kb=target[4:].strip())
    if target.startswith("src:"):
        try:
            return _ResolvedTarget(src_id=int(target[4:]))
        except ValueError:
            return _ResolvedTarget()
    # 裸名 → 当文档 KB
    return _ResolvedTarget(doc_kb=target)


# ---------------------------------------------------------------------------
# 上传 hooks(测试可注入,默认是占位 stub — 真实 ingestion 留待 95-4 的
# API 接入时连到 file_rag / image upload)
# ---------------------------------------------------------------------------

DocUploadFn = Callable[[str, str], Any]    # (kb_name, file_path) → ingest
DocDeleteFn = Callable[[str, str], Any]    # (kb_name, file_path) → 移除
ImgUploadFn = Callable[[int, str], Any]    # (src_id, file_path) → 入索引
ImgDeleteFn = Callable[[int, str], Any]


def _default_doc_upload(kb_name: str, file_path: str) -> None:  # pragma: no cover - 占位
    raise NotImplementedError("doc upload hook not wired")


def _default_doc_delete(kb_name: str, file_path: str) -> None:  # pragma: no cover
    raise NotImplementedError("doc delete hook not wired")


def _default_img_upload(src_id: int, file_path: str) -> None:  # pragma: no cover
    raise NotImplementedError("img upload hook not wired")


def _default_img_delete(src_id: int, file_path: str) -> None:  # pragma: no cover
    raise NotImplementedError("img delete hook not wired")


def apply_diff(
    diff: ScanDiff, *, target: str,
    doc_upload: DocUploadFn = _default_doc_upload,
    doc_delete: DocDeleteFn = _default_doc_delete,
    img_upload: ImgUploadFn = _default_img_upload,
    img_delete: ImgDeleteFn = _default_img_delete,
) -> UploadResult:
    """把 diff 应用到 target;每个文件独立 try/except,单失败不阻塞其它。

    Args:
        target: ``coll:N`` / ``doc:NAME`` / ``src:ID`` / 裸名
        *_upload / *_delete: 测试用 hook,真实环境由 95-4 的 API 注入
    """
    result = UploadResult()
    rt = resolve_target(target)

    def _route_upload(rec: FileRecord, action: str) -> bool:
        kind = kind_for_path(rec.path)
        if kind is None:
            result.summary["skipped_unsupported"] += 1
            return False
        try:
            if kind == "document":
                if not rt.doc_kb:
                    raise RuntimeError(
                        f"target {target!r} 没有文档 KB,无法 ingest 文档")
                doc_upload(rt.doc_kb, rec.path)
            elif kind == "image":
                if rt.src_id <= 0:
                    raise RuntimeError(
                        f"target {target!r} 没有 image source,无法 ingest 图像")
                img_upload(rt.src_id, rec.path)
            result.successful_paths.add(rec.path)
            if action == "added":
                result.summary["added_indexed"] += 1
            else:
                result.summary["modified_reindexed"] += 1
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[uploader] %s %s failed: %r", action, rec.path, e,
            )
            result.errors.append({
                "path": rec.path, "action": action,
                "error": f"{type(e).__name__}: {e}",
            })
            result.summary["errors"] += 1
            return False

    for rec in diff.added:
        _route_upload(rec, "added")
    for rec in diff.modified:
        _route_upload(rec, "modified")

    for rec in diff.removed:
        kind = kind_for_path(rec.path)
        if kind is None:
            continue
        try:
            if kind == "document" and rt.doc_kb:
                doc_delete(rt.doc_kb, rec.path)
            elif kind == "image" and rt.src_id > 0:
                img_delete(rt.src_id, rec.path)
            result.successful_paths.add(rec.path)
            result.summary["removed_purged"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[uploader] removed %s failed: %r", rec.path, e,
            )
            result.errors.append({
                "path": rec.path, "action": "removed",
                "error": f"{type(e).__name__}: {e}",
            })
            result.summary["errors"] += 1

    return result
