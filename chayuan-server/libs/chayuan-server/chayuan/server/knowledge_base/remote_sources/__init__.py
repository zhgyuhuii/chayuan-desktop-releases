"""远端文件源 → 知识库同步模块。

设计目标:
- **统一抽象**:RemoteSource 协议把不同后端(MinIO / FastDFS / 未来 OSS / SFTP)
  收成同一形状,sync_engine 只对协议编程,与具体后端解耦。
- **可插拔**:registry.SOURCE_KINDS 是唯一的注册点;新增后端 = 写一个 RemoteSource
  + 在 registry 里挂个名,前后端无须再改。
- **复用 ingest 路径**:同步落库走 _ingest_one(),内部 = FileStorage.put + 本地缓存
  + update_docs([file]) — 与 upload_docs 端点完全同形,任何向量库后端都能跑。
- **并发可控**:sync_engine 用 ThreadPoolExecutor,worker 数 = JobSpec.concurrency,
  避免淹没远端 / 向量库。
- **流式可观察**:job_manager 给每个 job 一个 asyncio.Queue,SSE 路由把事件透传给
  前端 — 进度条不是轮询出来的,是后端推的。
"""

from .base import (
    BrowseResult,
    RemoteFile,
    RemoteSource,
    SourceConfig,
    SourceError,
)
from .filters import SyncFilter
from .job_manager import Job, JobEvent, JobManager, JobStatus, get_job_manager
from .registry import build_source, list_source_kinds
from .sync_engine import JobSpec, SyncEngine

__all__ = [
    "BrowseResult",
    "Job",
    "JobEvent",
    "JobManager",
    "JobSpec",
    "JobStatus",
    "RemoteFile",
    "RemoteSource",
    "SourceConfig",
    "SourceError",
    "SyncEngine",
    "SyncFilter",
    "build_source",
    "get_job_manager",
    "list_source_kinds",
]
