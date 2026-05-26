"""统一文件存储子系统。

覆盖察元所有用户上传文件的读写场景：
- KB 内容文件（知识库）
- 对话临时文件（file_chat）
- 图像知识源里的原图

**后端**：``local``（默认，零依赖）/ ``minio``（通过 minio-py）。
切换通过 ``Settings.basic_settings.FILE_STORAGE_BACKEND`` 即可，业务代码不感知。

**命名空间**（常量见下）：一个 namespace 对应 local 下的子目录，或 minio 下的一个 bucket。

对外稳定 API：
    from chayuan.server.file_storage import get_storage, NS
    storage = get_storage()
    storage.put(NS.KB_CONTENT, f"{kb}/{filename}", data_bytes)
    data = storage.get(NS.KB_CONTENT, f"{kb}/{filename}")
    url = storage.presigned_url(NS.KB_CONTENT, key, expires_sec=3600)
    storage.delete(NS.CHAT_TEMP, f"{tid}/{filename}")
"""

from chayuan.server.file_storage.base import (  # noqa: F401
    FileStorage,
    NS,
    StorageError,
    StorageObject,
)
from chayuan.server.file_storage.factory import (  # noqa: F401
    get_storage, get_storage_for_kb, reset_cache,
)
