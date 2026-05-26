"""文件筛选器 — 同步前在 engine 一侧统一应用,与 RemoteSource 无关。"""

from __future__ import annotations

import dataclasses
import fnmatch
from typing import Iterable, List, Optional

from .base import RemoteFile


@dataclasses.dataclass
class SyncFilter:
    """筛选规则。

    - extensions:白名单后缀(全小写,带或不带点都可),为空表示不限。
    - max_size_bytes:超过则跳过(常见 PDF/视频太大,向量化无意义)。
    - include_globs / exclude_globs:fnmatch 风格,基于 RemoteFile.name(basename)。
    - skip_hidden:跳过点开头的文件。
    """
    extensions: List[str] = dataclasses.field(default_factory=list)
    max_size_bytes: Optional[int] = None
    include_globs: List[str] = dataclasses.field(default_factory=list)
    exclude_globs: List[str] = dataclasses.field(default_factory=list)
    skip_hidden: bool = True

    def __post_init__(self):
        self.extensions = [
            ("." + e.lstrip(".")).lower() for e in self.extensions if e
        ]

    def accepts(self, f: RemoteFile) -> bool:
        if f.is_dir:
            return False
        name = f.name
        if self.skip_hidden and name.startswith("."):
            return False
        if self.max_size_bytes is not None and f.size > self.max_size_bytes:
            return False
        if self.extensions:
            lower = name.lower()
            if not any(lower.endswith(ext) for ext in self.extensions):
                return False
        if self.include_globs and not any(fnmatch.fnmatch(name, g) for g in self.include_globs):
            return False
        if self.exclude_globs and any(fnmatch.fnmatch(name, g) for g in self.exclude_globs):
            return False
        return True

    def filter(self, files: Iterable[RemoteFile]) -> List[RemoteFile]:
        return [f for f in files if self.accepts(f)]
