"""95-2:文件夹扫描 + state diff(mtime/sha1)。

设计:
* state 文件 ``<CHAYUAN_ROOT>/data/folder_sync/{job_id}.state.json``
* state 内容 ``{path: {mtime, size, sha1?}}``;sha1 仅在 mtime/size 变化时计算
* mtime 预过滤优化:子目录 mtime 没变就跳过整个子树(目前先做平铺,后续优化)
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("chayuan.folder_sync.scanner")


@dataclass
class FileRecord:
    """state.json 里的单个文件记录。"""
    path: str           # 绝对路径
    mtime: float
    size: int
    sha1: str = ""      # 可选,仅在判等不确定时计算

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mtime": float(self.mtime),
            "size": int(self.size),
            "sha1": self.sha1 or "",
        }

    @classmethod
    def from_dict(cls, path: str, d: Dict[str, Any]) -> "FileRecord":
        return cls(
            path=path,
            mtime=float(d.get("mtime") or 0.0),
            size=int(d.get("size") or 0),
            sha1=str(d.get("sha1") or ""),
        )


@dataclass
class ScanDiff:
    """单次扫描 diff 结果。"""
    added: List[FileRecord] = field(default_factory=list)
    modified: List[FileRecord] = field(default_factory=list)
    removed: List[FileRecord] = field(default_factory=list)
    unchanged: int = 0
    skipped_excluded: int = 0
    errors: List[Dict[str, str]] = field(default_factory=list)

    def to_summary(self) -> Dict[str, Any]:
        return {
            "added": len(self.added),
            "modified": len(self.modified),
            "removed": len(self.removed),
            "unchanged": self.unchanged,
            "skipped_excluded": self.skipped_excluded,
            "errors": len(self.errors),
        }


# ---------------------------------------------------------------------------
# state.json 持久化
# ---------------------------------------------------------------------------

def _state_root() -> Path:
    """返回 state 文件所在目录,未配置时用 <CHAYUAN_ROOT>/data/folder_sync。"""
    try:
        from chayuan.settings import CHAYUAN_ROOT as _ROOT
        return Path(_ROOT) / "data" / "folder_sync"
    except Exception:
        # 测试 / 异常环境兜底
        return Path.cwd() / ".folder_sync"


def state_file_path(job_id: int) -> Path:
    p = _state_root() / f"{int(job_id)}.state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_state(job_id: int) -> Dict[str, FileRecord]:
    p = state_file_path(job_id)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[folder_sync] load state %s failed: %r", p, e)
        return {}
    out: Dict[str, FileRecord] = {}
    for path, d in raw.items():
        if isinstance(d, dict):
            out[path] = FileRecord.from_dict(path, d)
    return out


def save_state(job_id: int, state: Dict[str, FileRecord]) -> None:
    p = state_file_path(job_id)
    payload = {path: rec.to_dict() for path, rec in state.items()}
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# 扫描 + diff
# ---------------------------------------------------------------------------

def _matches_any(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _iter_files(
    root: Path, *, recursive: bool,
    include_globs: List[str], exclude_globs: List[str],
) -> Iterable[Path]:
    """生成器:遍历 root 下匹配 globs 的文件路径。"""
    if not root.exists() or not root.is_dir():
        return
    if recursive:
        for dirpath, dirnames, filenames in os.walk(root):
            # 排除目录(可选,避免进 ~$tmp 之类)
            dirnames[:] = [d for d in dirnames
                           if not _matches_any(d, exclude_globs)]
            for fn in filenames:
                if _matches_any(fn, exclude_globs):
                    continue
                if include_globs and not _matches_any(fn, include_globs):
                    continue
                yield Path(dirpath) / fn
    else:
        for entry in root.iterdir():
            if not entry.is_file():
                continue
            if _matches_any(entry.name, exclude_globs):
                continue
            if include_globs and not _matches_any(entry.name, include_globs):
                continue
            yield entry


def _stat_record(p: Path) -> Optional[FileRecord]:
    try:
        st = p.stat()
        return FileRecord(
            path=str(p), mtime=float(st.st_mtime),
            size=int(st.st_size), sha1="",
        )
    except OSError as e:
        logger.warning("stat %s failed: %r", p, e)
        return None


def _compute_sha1(p: Path) -> str:
    """读小文件算 sha1;失败返空字符串。"""
    try:
        h = hashlib.sha1()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def scan(
    job_id: int, *, folder_path: str, recursive: bool,
    include_globs: List[str], exclude_globs: List[str],
    use_sha1_for_modified: bool = False,
) -> ScanDiff:
    """扫一次目录,返 diff 不写 state(由 uploader 完成后再写)。

    Args:
        use_sha1_for_modified: True 时对大小相同 / mtime 变化的文件算 sha1
            做精确判等(避免误标 modified)。默认 False,只用 mtime+size
            判等(快但可能误报;对入索引来说重传不致命)。
    """
    diff = ScanDiff()
    root = Path(folder_path)
    if not root.exists():
        diff.errors.append({"path": folder_path,
                            "error": f"folder 不存在: {folder_path}"})
        return diff
    if not root.is_dir():
        diff.errors.append({"path": folder_path,
                            "error": f"不是目录: {folder_path}"})
        return diff

    old_state = load_state(job_id)
    seen_paths: set = set()
    for p in _iter_files(
        root, recursive=recursive,
        include_globs=include_globs or [],
        exclude_globs=exclude_globs or [],
    ):
        rec = _stat_record(p)
        if rec is None:
            diff.errors.append({"path": str(p), "error": "stat failed"})
            continue
        seen_paths.add(rec.path)
        old = old_state.get(rec.path)
        if old is None:
            diff.added.append(rec)
            continue
        # mtime + size 判等(快)
        if old.mtime == rec.mtime and old.size == rec.size:
            diff.unchanged += 1
            # 沿用旧 sha1 节省后续计算
            rec.sha1 = old.sha1
            continue
        # 不等:进一步校验
        if use_sha1_for_modified:
            new_sha = _compute_sha1(p)
            if new_sha and new_sha == old.sha1:
                # 真没变(只是 touch)
                diff.unchanged += 1
                rec.sha1 = new_sha
                continue
            rec.sha1 = new_sha
        diff.modified.append(rec)

    # removed = 旧 state 中存在但本次扫描没看到
    for path, old in old_state.items():
        if path not in seen_paths:
            diff.removed.append(old)

    return diff


def apply_state_after_diff(
    job_id: int, diff: ScanDiff, *,
    successful_paths: Optional[set] = None,
) -> None:
    """把 diff 落到新 state 并 save。

    ``successful_paths``:upload 成功的路径集;只把这些进 state(失败的留旧值,
    下次同步重试)。None 表示所有 added+modified 都成功。
    """
    state = load_state(job_id)
    succ = successful_paths if successful_paths is not None else None
    for rec in diff.added + diff.modified:
        if succ is None or rec.path in succ:
            state[rec.path] = rec
    for rec in diff.removed:
        if succ is None or rec.path in succ:
            state.pop(rec.path, None)
    save_state(job_id, state)
