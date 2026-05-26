"""95-2 + 95-3:scanner + uploader 端到端测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def chayuan_root_tmp(tmp_path, monkeypatch):
    """切 CHAYUAN_ROOT 到 tmp,让 state file 写到这里。"""
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# scanner.scan
# ---------------------------------------------------------------------------

def test_scan_added_files_first_run(chayuan_root_tmp, tmp_path):
    """首次扫(无 state)→ 所有文件标 added。"""
    from chayuan.server.folder_sync.scanner import scan

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"pdfbytes")
    (folder / "b.jpg").write_bytes(b"jpgbytes")

    diff = scan(
        job_id=1, folder_path=str(folder), recursive=False,
        include_globs=["*.pdf", "*.jpg"], exclude_globs=[],
    )
    assert len(diff.added) == 2
    assert diff.removed == []


def test_scan_unchanged_after_save_state(chayuan_root_tmp, tmp_path):
    """扫一次 + save_state + 再扫 → 全部 unchanged。"""
    from chayuan.server.folder_sync.scanner import (
        apply_state_after_diff, scan,
    )

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"x")

    d1 = scan(
        job_id=2, folder_path=str(folder), recursive=False,
        include_globs=["*.pdf"], exclude_globs=[],
    )
    apply_state_after_diff(2, d1)

    d2 = scan(
        job_id=2, folder_path=str(folder), recursive=False,
        include_globs=["*.pdf"], exclude_globs=[],
    )
    assert d2.added == []
    assert d2.removed == []
    assert d2.unchanged == 1


def test_scan_detects_added_modified_removed(chayuan_root_tmp, tmp_path):
    import time
    from chayuan.server.folder_sync.scanner import (
        apply_state_after_diff, scan,
    )

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"v1")
    (folder / "b.jpg").write_bytes(b"keep")

    # 第一次扫 + 落 state
    d1 = scan(
        job_id=3, folder_path=str(folder), recursive=False,
        include_globs=["*.pdf", "*.jpg"], exclude_globs=[],
    )
    apply_state_after_diff(3, d1)

    # 改 a.pdf,加 c.pdf,删 b.jpg
    time.sleep(0.05)
    (folder / "a.pdf").write_bytes(b"v2_longer")
    (folder / "c.pdf").write_bytes(b"new")
    (folder / "b.jpg").unlink()

    d2 = scan(
        job_id=3, folder_path=str(folder), recursive=False,
        include_globs=["*.pdf", "*.jpg"], exclude_globs=[],
    )
    added_names = {Path(r.path).name for r in d2.added}
    modified_names = {Path(r.path).name for r in d2.modified}
    removed_names = {Path(r.path).name for r in d2.removed}
    assert added_names == {"c.pdf"}
    assert modified_names == {"a.pdf"}
    assert removed_names == {"b.jpg"}


def test_scan_recursive_walks_subdirs(chayuan_root_tmp, tmp_path):
    from chayuan.server.folder_sync.scanner import scan

    folder = tmp_path / "src"
    sub = folder / "sub"
    sub.mkdir(parents=True)
    (folder / "root.pdf").write_bytes(b"x")
    (sub / "nested.pdf").write_bytes(b"y")

    diff = scan(
        job_id=4, folder_path=str(folder), recursive=True,
        include_globs=["*.pdf"], exclude_globs=[],
    )
    names = {Path(r.path).name for r in diff.added}
    assert names == {"root.pdf", "nested.pdf"}


def test_scan_excludes_match_filename(chayuan_root_tmp, tmp_path):
    from chayuan.server.folder_sync.scanner import scan

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "doc.pdf").write_bytes(b"x")
    (folder / "~$tmp.docx").write_bytes(b"y")

    diff = scan(
        job_id=5, folder_path=str(folder), recursive=False,
        include_globs=["*.pdf", "*.docx"], exclude_globs=["~$*"],
    )
    names = {Path(r.path).name for r in diff.added}
    assert names == {"doc.pdf"}


def test_scan_missing_folder_returns_error(chayuan_root_tmp):
    from chayuan.server.folder_sync.scanner import scan
    diff = scan(
        job_id=6, folder_path="/path/does/not/exist", recursive=False,
        include_globs=["*"], exclude_globs=[],
    )
    assert diff.added == []
    assert len(diff.errors) == 1


def test_scan_with_partial_success_keeps_failed_in_state(chayuan_root_tmp, tmp_path):
    """uploader 失败 → state 不更新该文件,下次同步重试。"""
    from chayuan.server.folder_sync.scanner import (
        apply_state_after_diff, load_state, scan,
    )

    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "ok.pdf").write_bytes(b"x")
    (folder / "fail.jpg").write_bytes(b"y")

    d1 = scan(
        job_id=7, folder_path=str(folder), recursive=False,
        include_globs=["*"], exclude_globs=[],
    )
    # 模拟只有 ok.pdf 上传成功
    apply_state_after_diff(
        7, d1, successful_paths={str(folder / "ok.pdf")},
    )

    state = load_state(7)
    assert str(folder / "ok.pdf") in state
    assert str(folder / "fail.jpg") not in state


# ---------------------------------------------------------------------------
# uploader.kind_for_path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,kind", [
    ("/x/y/a.pdf", "document"),
    ("/x/a.docx", "document"),
    ("/x/a.txt", "document"),
    ("/x/a.JPG", "image"),
    ("/x/a.png", "image"),
    ("/x/a.WEBP", "image"),
    ("/x/a.exe", None),
    ("/x/a.zip", None),
])
def test_kind_for_path(path, kind):
    from chayuan.server.folder_sync.uploader import kind_for_path
    assert kind_for_path(path) == kind


# ---------------------------------------------------------------------------
# uploader.resolve_target
# ---------------------------------------------------------------------------

def test_resolve_target_doc_prefix(chayuan_root_tmp):
    from chayuan.server.folder_sync.uploader import resolve_target
    rt = resolve_target("doc:my_kb")
    assert rt.doc_kb == "my_kb"
    assert rt.src_id == 0


def test_resolve_target_src_prefix(chayuan_root_tmp):
    from chayuan.server.folder_sync.uploader import resolve_target
    rt = resolve_target("src:7")
    assert rt.src_id == 7


def test_resolve_target_bare_name_treated_as_doc(chayuan_root_tmp):
    from chayuan.server.folder_sync.uploader import resolve_target
    rt = resolve_target("bare_kb")
    assert rt.doc_kb == "bare_kb"


# ---------------------------------------------------------------------------
# uploader.apply_diff
# ---------------------------------------------------------------------------

def _make_rec(path: str):
    from chayuan.server.folder_sync.scanner import FileRecord
    return FileRecord(path=path, mtime=0.0, size=0)


def test_apply_diff_routes_doc_and_image(chayuan_root_tmp):
    from chayuan.server.folder_sync.scanner import ScanDiff
    from chayuan.server.folder_sync.uploader import apply_diff

    diff = ScanDiff()
    diff.added.append(_make_rec("/x/a.pdf"))
    diff.added.append(_make_rec("/x/b.jpg"))

    doc_calls = []
    img_calls = []
    apply_diff(
        diff, target="doc:test_kb",
        doc_upload=lambda kb, p: doc_calls.append((kb, p)),
        doc_delete=lambda kb, p: None,
        img_upload=lambda sid, p: img_calls.append((sid, p)),
        img_delete=lambda sid, p: None,
    )
    assert doc_calls == [("test_kb", "/x/a.pdf")]
    # target 没有 src,jpg 走不通会报 error,但 doc 不影响


def test_apply_diff_collection_routes_to_both(chayuan_root_tmp):
    """coll: 同时承载 doc + image,各自分发。

    用 patch 替代真 db,避免依赖 SessionLocal 配置。
    """
    from unittest.mock import patch
    from chayuan.server.folder_sync.scanner import ScanDiff
    from chayuan.server.folder_sync.uploader import (
        _ResolvedTarget, apply_diff,
    )

    fake_resolved = _ResolvedTarget(
        doc_kb="doc_a", src_id=7, via_collection=True,
    )
    diff = ScanDiff()
    diff.added.append(_make_rec("/x/a.pdf"))
    diff.added.append(_make_rec("/x/b.jpg"))

    doc_calls = []
    img_calls = []
    with patch(
        "chayuan.server.folder_sync.uploader.resolve_target",
        return_value=fake_resolved,
    ):
        result = apply_diff(
            diff, target="coll:1",
            doc_upload=lambda kb, p: doc_calls.append((kb, p)),
            doc_delete=lambda kb, p: None,
            img_upload=lambda sid, p: img_calls.append((sid, p)),
            img_delete=lambda sid, p: None,
        )
    assert ("doc_a", "/x/a.pdf") in doc_calls
    assert (7, "/x/b.jpg") in img_calls
    assert result.summary["added_indexed"] == 2


def test_apply_diff_skips_unsupported_extension(chayuan_root_tmp):
    from chayuan.server.folder_sync.scanner import ScanDiff
    from chayuan.server.folder_sync.uploader import apply_diff

    diff = ScanDiff()
    diff.added.append(_make_rec("/x/binary.exe"))

    result = apply_diff(
        diff, target="doc:any",
        doc_upload=lambda kb, p: None,
        doc_delete=lambda kb, p: None,
        img_upload=lambda sid, p: None,
        img_delete=lambda sid, p: None,
    )
    assert result.summary["skipped_unsupported"] == 1
    assert result.summary["added_indexed"] == 0


def test_apply_diff_single_file_failure_isolated(chayuan_root_tmp):
    """单个文件 upload 抛 → 不阻塞其它,记到 errors。"""
    from chayuan.server.folder_sync.scanner import ScanDiff
    from chayuan.server.folder_sync.uploader import apply_diff

    diff = ScanDiff()
    diff.added.append(_make_rec("/x/ok.pdf"))
    diff.added.append(_make_rec("/x/fail.pdf"))

    def _maybe_fail(kb, p):
        if "fail" in p:
            raise RuntimeError("boom")

    result = apply_diff(
        diff, target="doc:k",
        doc_upload=_maybe_fail,
        doc_delete=lambda kb, p: None,
        img_upload=lambda sid, p: None,
        img_delete=lambda sid, p: None,
    )
    assert result.summary["added_indexed"] == 1
    assert result.summary["errors"] == 1
    assert "/x/ok.pdf" in result.successful_paths
    assert "/x/fail.pdf" not in result.successful_paths


def test_apply_diff_removed_calls_delete(chayuan_root_tmp):
    from chayuan.server.folder_sync.scanner import ScanDiff
    from chayuan.server.folder_sync.uploader import apply_diff

    diff = ScanDiff()
    diff.removed.append(_make_rec("/x/old.pdf"))

    deleted = []
    apply_diff(
        diff, target="doc:k",
        doc_upload=lambda kb, p: None,
        doc_delete=lambda kb, p: deleted.append(p),
        img_upload=lambda sid, p: None,
        img_delete=lambda sid, p: None,
    )
    assert deleted == ["/x/old.pdf"]
