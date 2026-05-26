"""集中导入所有 ORM model,确保 ``Base.metadata.create_all`` 看到全部表。

启动链路 ``chayuan.server.knowledge_base.migrate.create_tables`` 调用
``Base.metadata.create_all(bind=engine)`` 之前必须先 import 本包,否则未被 import
的 model 类不会注册到 ``Base.registry``,造成 SQLite 单机库出现
``no such table: model_platform`` 等运行时错误。

新增 model 文件时**必须**在此处补一行 import,且 lint 不要 fold 成 unused —
所有 import 都仅为副作用注册。
"""
from __future__ import annotations

# 按模块名字母序排列,避免顺序耦合;每个 model 模块都只是注册到 metadata。
from chayuan.server.db.models import annotation_model  # noqa: F401
from chayuan.server.db.models import app_kb_grant_model  # noqa: F401
from chayuan.server.db.models import audit_log_model  # noqa: F401
from chayuan.server.db.models import conversation_model  # noqa: F401
from chayuan.server.db.models import data_mount_model  # noqa: F401
from chayuan.server.db.models import folder_sync_model  # noqa: F401
from chayuan.server.db.models import governance_model  # noqa: F401
from chayuan.server.db.models import graphrag_model  # noqa: F401
from chayuan.server.db.models import human_message_event  # noqa: F401
from chayuan.server.db.models import kb_access_model  # noqa: F401
from chayuan.server.db.models import kb_collection_model  # noqa: F401
from chayuan.server.db.models import knowledge_base_model  # noqa: F401
from chayuan.server.db.models import knowledge_file_model  # noqa: F401
from chayuan.server.db.models import knowledge_metadata_model  # noqa: F401
from chayuan.server.db.models import knowledge_source_model  # noqa: F401
from chayuan.server.db.models import mcp_connection_model  # noqa: F401
from chayuan.server.db.models import message_model  # noqa: F401
from chayuan.server.db.models import model_metadata_model  # noqa: F401
from chayuan.server.db.models import model_platform_model  # noqa: F401
# ``modality_task`` 表实际定义在 modality 包(就近;不属于 generic DB 域)。
# 这里只 import 触发 Base.registry 注册,让 create_all 看得到这张表。
from chayuan.server.modality.router.tasks import db_model as _modality_task_model  # noqa: F401
from chayuan.server.db.models import route_context_model  # noqa: F401
from chayuan.server.db.models import sql_training_model  # noqa: F401
from chayuan.server.db.models import user_model  # noqa: F401
