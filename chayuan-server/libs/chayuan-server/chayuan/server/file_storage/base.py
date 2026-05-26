"""FileStorage 抽象基类 + 命名空间常量。

命名空间语义（localdir 名 / bucket 名）：

| namespace   | 内容                               | 保留策略         |
|-------------|------------------------------------|------------------|
| kb_content  | 知识库原始文件（pdf/md/docx/...）  | 永久             |
| chat_temp   | 对话侧上传的临时文件               | 24 小时（可配）  |
| image_files | 图像知识源里的原图                 | 永久             |
| misc        | 其它杂项（截图、导出等）           | 可选             |

**key 约定**：使用正斜杠 `/` 组织子目录；例如 `kb=samples, file=a.pdf` → `samples/a.pdf`。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Iterator, List, Optional, Union


class NS:
    """命名空间常量。"""
    KB_CONTENT = "kb_content"
    CHAT_TEMP = "chat_temp"
    IMAGE_FILES = "image_files"
    MISC = "misc"

    @classmethod
    def all(cls) -> List[str]:
        return [cls.KB_CONTENT, cls.CHAT_TEMP, cls.IMAGE_FILES, cls.MISC]


class StorageError(Exception):
    """所有 FileStorage 对外抛出的统一异常。"""


@dataclass
class StorageObject:
    """list / stat 返回的对象元数据。"""
    key: str
    size: int = 0
    last_modified: Optional[datetime] = None
    etag: str = ""
    content_type: str = ""


class FileStorage(ABC):
    """统一文件存储契约。

    实现约束：
    - 任何一方法**抛错都必须**包成 ``StorageError``（便于上层统一处理）
    - ``key`` 参数**不得**以 `/` 开头；内部实现自己做 normalize
    - ``open_read`` 返回的对象必须是"可被 ``.read()`` 或作为 context manager"的流
    - ``presigned_url`` 对不支持预签名的后端（local）返回内部代理 URL
    """

    name: str = "base"

    # ----- 必须实现 -----

    @abstractmethod
    def put(
        self,
        namespace: str,
        key: str,
        data: Union[bytes, IO[bytes]],
        *,
        content_type: str = "",
        metadata: Optional[dict] = None,
    ) -> StorageObject:
        """上传；data 可以是 bytes 或文件流。覆盖语义（same key 直接替换）。"""

    @abstractmethod
    def get(self, namespace: str, key: str) -> bytes:
        """一次性读全部；大文件应改用 ``open_read``。"""

    @abstractmethod
    def open_read(self, namespace: str, key: str):
        """返回一个流对象（context manager 用）；用于 StreamingResponse。"""

    @abstractmethod
    def exists(self, namespace: str, key: str) -> bool:
        ...

    @abstractmethod
    def stat(self, namespace: str, key: str) -> Optional[StorageObject]:
        """获取对象元数据；不存在返回 None。"""

    @abstractmethod
    def delete(self, namespace: str, key: str) -> bool:
        """删除对象；不存在返回 False（不抛）。"""

    @abstractmethod
    def list(
        self, namespace: str, *, prefix: str = "", limit: int = 1000,
    ) -> List[StorageObject]:
        """列对象；local 内部用 os.walk；minio 用 list_objects。"""

    @abstractmethod
    def presigned_url(
        self, namespace: str, key: str, *, expires_sec: int = 3600,
    ) -> str:
        """生成外部可直接访问的 URL（含鉴权 / 过期）。"""

    # ----- 可选便捷 -----

    def put_file(
        self, namespace: str, key: str, local_path: str, *, content_type: str = "",
    ) -> StorageObject:
        """把一个本地文件推上去；默认用 ``put`` + open。"""
        with open(local_path, "rb") as f:
            return self.put(namespace, key, f, content_type=content_type)

    def copy_to_local(self, namespace: str, key: str, local_path: str) -> str:
        """把远端对象拉到本地指定路径（保持与老 filepath-based 逻辑兼容用）。"""
        data = self.get(namespace, key)
        import os
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(data)
        return local_path

    # ----- 管理 -----

    @abstractmethod
    def backend_info(self) -> dict:
        """返回后端元信息：type / endpoint / healthy / 每 namespace 对象数 + 总大小。"""
