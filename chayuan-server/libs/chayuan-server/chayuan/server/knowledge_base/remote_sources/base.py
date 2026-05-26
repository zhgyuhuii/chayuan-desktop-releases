"""RemoteSource 抽象。

任何远端文件源(MinIO / FastDFS / OSS / SFTP …)实现此协议即可被同步引擎消费。
不依赖具体 SDK,SDK import 放在子类内部并以 SourceError 友好包裹,缺包不会拖死
import 链(子类的 import 失败由 registry 兜底)。
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, BinaryIO, Dict, List, Optional


class SourceError(Exception):
    """远端源相关的可向用户展示的错误(连接失败 / 路径不存在 / 缺依赖等)。"""


@dataclasses.dataclass(frozen=True)
class RemoteFile:
    """远端目录条目。

    `key` 是远端的"绝对寻址路径"(MinIO 是 object_name,FastDFS 是 file_id);
    `name` 是 UI 展示用 basename。`is_dir=True` 时 size 通常为 0。
    """
    key: str
    name: str
    size: int
    modified: Optional[datetime]
    is_dir: bool
    etag: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "size": self.size,
            "modified": self.modified.isoformat() if self.modified else None,
            "is_dir": self.is_dir,
            "etag": self.etag,
        }


@dataclasses.dataclass(frozen=True)
class BrowseResult:
    """单次浏览结果(文件夹一页)。

    cwd:当前路径(规范化后,以 '/' 结尾或空串表示根);
    parent:上层路径(根则为 None);
    truncated + next_marker:支持下一页,UI 才不至于一次拉爆百万对象。
    """
    cwd: str
    parent: Optional[str]
    entries: List[RemoteFile]
    truncated: bool = False
    next_marker: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cwd": self.cwd,
            "parent": self.parent,
            "entries": [e.to_dict() for e in self.entries],
            "truncated": self.truncated,
            "next_marker": self.next_marker,
        }


@dataclasses.dataclass(frozen=True)
class SourceConfig:
    """RemoteSource 的连接参数(透传给子类构造,字段含义由子类约定)。

    用 dataclass 而不是 dict 是为了:
    1) 类型清晰(IDE 跳得到);
    2) repr 时自动隐藏可能的 secret(参见 _redact)。
    """
    kind: str
    options: Dict[str, Any]

    def __repr__(self) -> str:  # pragma: no cover - dev-only
        return f"SourceConfig(kind={self.kind!r}, options={_redact(self.options)!r})"


_SECRET_KEYS = ("secret", "password", "token", "key", "secret_key")


def _redact(opts: Dict[str, Any]) -> Dict[str, Any]:
    return {k: ("***" if any(s in k.lower() for s in _SECRET_KEYS) else v) for k, v in opts.items()}


class RemoteSource(ABC):
    """远端文件源抽象基类。

    实现要点:
    - `__init__(config: SourceConfig)`:解析配置 + 建客户端;**不能**做"试连"
      (那是 test() 的职责),否则 browse 失败时构造也炸,无法返回结构化错误。
    - `test()`:做最轻量的探活(MinIO list_buckets / FastDFS tracker ping)。
    - `browse(path)`:返回当前层(目录在前文件在后,name 升序)。务必规范化
      路径:前后无多余 '/'、根为空串。
    - `open_read(key)`:返回支持 `with` 的可读流(read-once 即可),engine 拿 bytes
      写入 KB。
    - `close()`:释放底层连接(http session / fdfs client)。可选实现。
    """

    kind: str = ""

    def __init__(self, config: SourceConfig):
        self.config = config

    # —— 必实现 ——
    @abstractmethod
    def test(self) -> Dict[str, Any]: ...

    @abstractmethod
    def browse(
        self,
        path: str = "",
        *,
        marker: Optional[str] = None,
        limit: int = 200,
    ) -> BrowseResult: ...

    @abstractmethod
    def open_read(self, key: str) -> AbstractContextManager[BinaryIO]: ...

    # —— 可选 ——
    def stat(self, key: str) -> Optional[RemoteFile]:  # pragma: no cover - 默认走 browse 单条
        return None

    def walk(
        self,
        path: str = "",
        *,
        page_size: int = 500,
    ):
        """递归生成器,深度优先;sync_engine 用它喂工人池。

        默认实现 = 反复 browse + 入栈;子类若有 `recursive=True` 形态(MinIO
        的 list_objects(recursive=True))可以重写,显著快于 N 次往返。
        """
        stack: List[str] = [path]
        while stack:
            cwd = stack.pop()
            marker: Optional[str] = None
            while True:
                page = self.browse(cwd, marker=marker, limit=page_size)
                for e in page.entries:
                    if e.is_dir:
                        stack.append(e.key)
                    else:
                        yield e
                if not page.truncated or not page.next_marker:
                    break
                marker = page.next_marker

    def close(self) -> None:  # pragma: no cover - 默认 no-op
        return None

    # —— 上下文管理 ——
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
        return False


# ──────────────────────────────────────────────────────────────────────
# 路径规范化工具:子类与上层都依赖
# ──────────────────────────────────────────────────────────────────────

def normalize_dir(path: str) -> str:
    """规范成 'a/b/c/' 形式;根目录 = ''。"""
    p = (path or "").strip().lstrip("/")
    p = p.rstrip("/")
    return p + "/" if p else ""


def parent_of(path: str) -> Optional[str]:
    """parent('a/b/c/') == 'a/b/';parent('a/') == '';parent('') == None。"""
    norm = normalize_dir(path)
    if not norm:
        return None
    parts = norm.rstrip("/").split("/")
    if len(parts) <= 1:
        return ""
    return "/".join(parts[:-1]) + "/"
