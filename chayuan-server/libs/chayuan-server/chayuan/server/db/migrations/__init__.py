"""极简迁移框架，够用就好。

为什么不用 Alembic：引入 Alembic 会多一套配置 / alembic_version 表 / CLI，
对一个中小型项目成本偏高；这里手写一个"幂等 migration 列表 + 内置版本表"
的迷你框架，够覆盖我们目前的字段新增需求。

用法：
    from chayuan.server.db.migrations import run_migrations
    run_migrations()

每一条 migration 都必须**幂等** —— 我们用 "先探测列/表是否存在再 ALTER" 的方式实现，
即使迁移记录表被人手动清掉也不会重复加列。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("chayuan.db.migrations")


@dataclass
class Migration:
    version: str  # 形如 "2026_04_20_p3_auth"
    description: str
    run: Callable[[Engine], None]


# ---------------------------------------------------------------------------
# 内置迁移：幂等 ALTER / CREATE
# ---------------------------------------------------------------------------

def _has_table(engine: Engine, table: str) -> bool:
    return inspect(engine).has_table(table)


def _has_column(engine: Engine, table: str, column: str) -> bool:
    insp = inspect(engine)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def _add_column_if_missing(
    engine: Engine, table: str, column: str, ddl_type: str, nullable: bool = True,
    default_sql: str = "",
) -> None:
    if not _has_table(engine, table):
        logger.debug("migration: table %s not present yet, skip add column %s", table, column)
        return
    if _has_column(engine, table, column):
        return
    stmt = f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
    if not nullable:
        stmt += " NOT NULL"
    if default_sql:
        stmt += f" DEFAULT {default_sql}"
    logger.info("migration: %s", stmt)
    with engine.begin() as conn:
        conn.execute(text(stmt))


def _create_index_if_missing(engine: Engine, table: str, index: str, columns: List[str]) -> None:
    insp = inspect(engine)
    if not insp.has_table(table):
        return
    existing = {idx.get("name") for idx in insp.get_indexes(table)}
    if index in existing:
        return
    cols = ", ".join(columns)
    stmt = f"CREATE INDEX {index} ON {table} ({cols})"
    logger.info("migration: %s", stmt)
    with engine.begin() as conn:
        conn.execute(text(stmt))


def _create_users_table(engine: Engine) -> None:
    from chayuan.server.db.models.user_model import UserModel  # noqa: F401
    from chayuan.server.db.base import Base

    UserModel.__table__.create(bind=engine, checkfirst=True)


def _create_kb_access_table(engine: Engine) -> None:
    from chayuan.server.db.models.kb_access_model import KBAccessGrantModel  # noqa: F401

    KBAccessGrantModel.__table__.create(bind=engine, checkfirst=True)


def _create_config_center_tables(engine: Engine) -> None:
    """引入配置中心 chayuan_config / chayuan_config_history 两张表。"""
    from chayuan.server.config_center.models import (  # noqa: F401
        ConfigEntry, ConfigHistory,
    )

    ConfigEntry.__table__.create(bind=engine, checkfirst=True)
    ConfigHistory.__table__.create(bind=engine, checkfirst=True)


# ---------------------------------------------------------------------------
# P3：引入用户体系
# ---------------------------------------------------------------------------

def _migrate_p3_auth(engine: Engine) -> None:
    # 1) users
    _create_users_table(engine)

    # 2) conversation.user_id
    _add_column_if_missing(engine, "conversation", "user_id", "INTEGER", nullable=True)

    # 3) message.user_id
    _add_column_if_missing(engine, "message", "user_id", "INTEGER", nullable=True)

    # 4) knowledge_base.owner_id / visibility
    _add_column_if_missing(engine, "knowledge_base", "owner_id", "INTEGER", nullable=True)
    # 注意：默认值写成带引号的字符串
    _add_column_if_missing(
        engine, "knowledge_base", "visibility", "VARCHAR(16)",
        nullable=True, default_sql="'private'",
    )

    # 5) kb_access_grants
    _create_kb_access_table(engine)


def _migrate_config_center(engine: Engine) -> None:
    _create_config_center_tables(engine)


def _create_knowledge_source_tables(engine: Engine) -> None:
    from chayuan.server.db.models.knowledge_source_model import (  # noqa: F401
        KnowledgeSourceConnectionModel,
        KnowledgeSourceModel,
        KnowledgeSourceSchemaCache,
        SourceAccessGrantModel,
    )
    KnowledgeSourceConnectionModel.__table__.create(bind=engine, checkfirst=True)
    KnowledgeSourceModel.__table__.create(bind=engine, checkfirst=True)
    KnowledgeSourceSchemaCache.__table__.create(bind=engine, checkfirst=True)
    SourceAccessGrantModel.__table__.create(bind=engine, checkfirst=True)


def _backfill_vector_sources(engine: Engine) -> None:
    """把现有 knowledge_base 行同步为 knowledge_source(kind=vector) 影子行。

    幂等：按 name 去重；若老表不存在则跳过。只做一次性回填，后续新建 KB 会由
    kb_routes 直接同步。
    """
    from sqlalchemy import text
    if not _has_table(engine, "knowledge_base"):
        return
    if not _has_table(engine, "knowledge_source"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, kb_name, kb_info, owner_id, visibility FROM knowledge_base"
        )).fetchall()
        for rid, name, info, owner_id, visibility in rows:
            if not name:
                continue
            exists = conn.execute(
                text("SELECT id FROM knowledge_source WHERE name = :n"),
                {"n": name},
            ).fetchone()
            if exists:
                continue
            conn.execute(
                text(
                    "INSERT INTO knowledge_source "
                    "(name, display_name, kind, description, vs_kb_id, owner_id, visibility) "
                    "VALUES (:n, :d, 'vector', :desc, :vs, :o, :v)"
                ),
                {"n": name, "d": name, "desc": info or "", "vs": rid,
                 "o": owner_id, "v": (visibility or "private")},
            )


def _migrate_knowledge_source(engine: Engine) -> None:
    _create_knowledge_source_tables(engine)
    _backfill_vector_sources(engine)


def _create_audit_log_table(engine: Engine) -> None:
    from chayuan.server.db.models.audit_log_model import AuditLogModel  # noqa: F401

    AuditLogModel.__table__.create(bind=engine, checkfirst=True)


def _migrate_audit_log(engine: Engine) -> None:
    _create_audit_log_table(engine)


def _create_sql_training_table(engine: Engine) -> None:
    from chayuan.server.db.models.sql_training_model import (  # noqa: F401
        SqlTrainingSampleModel,
    )
    SqlTrainingSampleModel.__table__.create(bind=engine, checkfirst=True)


def _migrate_sql_training(engine: Engine) -> None:
    _create_sql_training_table(engine)


def _create_governance_tables(engine: Engine) -> None:
    from chayuan.server.db.models.governance_model import (  # noqa: F401
        GovernancePolicyModel,
        LineageRecordModel,
        LineageTouchModel,
        UsageCounterModel,
    )
    GovernancePolicyModel.__table__.create(bind=engine, checkfirst=True)
    LineageRecordModel.__table__.create(bind=engine, checkfirst=True)
    LineageTouchModel.__table__.create(bind=engine, checkfirst=True)
    UsageCounterModel.__table__.create(bind=engine, checkfirst=True)


def _migrate_governance(engine: Engine) -> None:
    _create_governance_tables(engine)


def _create_graphrag_tables(engine: Engine) -> None:
    from chayuan.server.db.models.graphrag_model import (  # noqa: F401
        GraphCommunityModel, GraphEntityModel, GraphRelationModel,
    )
    GraphEntityModel.__table__.create(bind=engine, checkfirst=True)
    GraphRelationModel.__table__.create(bind=engine, checkfirst=True)
    GraphCommunityModel.__table__.create(bind=engine, checkfirst=True)


def _migrate_graphrag(engine: Engine) -> None:
    _create_graphrag_tables(engine)


def _create_model_metadata_table(engine: Engine) -> None:
    from chayuan.server.db.models.model_metadata_model import ModelMetadataModel  # noqa: F401

    ModelMetadataModel.__table__.create(bind=engine, checkfirst=True)


def _migrate_model_metadata(engine: Engine) -> None:
    _create_model_metadata_table(engine)


def _migrate_route_context(engine: Engine) -> None:
    from chayuan.server.db.models.route_context_model import RouteContextModel  # noqa: F401

    RouteContextModel.__table__.create(bind=engine, checkfirst=True)


def _migrate_annotation_tables(engine: Engine) -> None:
    from chayuan.server.db.models.annotation_model import (  # noqa: F401
        AnnotationFeedbackModel,
        AnnotationTaskModel,
    )

    AnnotationTaskModel.__table__.create(bind=engine, checkfirst=True)
    AnnotationFeedbackModel.__table__.create(bind=engine, checkfirst=True)


def _migrate_annotation_feedback(engine: Engine) -> None:
    from chayuan.server.db.models.annotation_model import AnnotationFeedbackModel  # noqa: F401

    AnnotationFeedbackModel.__table__.create(bind=engine, checkfirst=True)


def _migrate_data_mount_tables(engine: Engine) -> None:
    from chayuan.server.db.models.data_mount_model import (  # noqa: F401
        DataMountArtifactModel,
        DataMountHitLogModel,
        DataMountModel,
    )

    DataMountModel.__table__.create(bind=engine, checkfirst=True)
    DataMountArtifactModel.__table__.create(bind=engine, checkfirst=True)
    DataMountHitLogModel.__table__.create(bind=engine, checkfirst=True)


def _migrate_kb_storage_backend(engine: Engine) -> None:
    """knowledge_base.storage_backend — 支持按 KB 存储后端覆盖。

    NULL/'' 视为跟随全局 Settings.FILE_STORAGE_BACKEND；'local' / 'minio' 为显式覆盖。
    幂等：列存在则跳过。
    """
    _add_column_if_missing(
        engine, "knowledge_base", "storage_backend", "VARCHAR(16)",
        nullable=True,
    )


def _migrate_kb_vector_namespace(engine: Engine) -> None:
    """knowledge_base.vector_namespace — 底层向量库内部集合/索引名。"""
    _add_column_if_missing(
        engine, "knowledge_base", "vector_namespace", "VARCHAR(64)",
        nullable=True,
    )


def _migrate_kb_file_dedupe(engine: Engine) -> None:
    """knowledge_file 增加内容指纹与上传人,用于上传前重复检测。"""
    _add_column_if_missing(engine, "knowledge_file", "file_hash", "VARCHAR(64)", nullable=True)
    _add_column_if_missing(engine, "knowledge_file", "uploader_id", "INTEGER", nullable=True)
    _create_index_if_missing(engine, "knowledge_file", "idx_knowledge_file_hash", ["file_hash"])
    _create_index_if_missing(engine, "knowledge_file", "idx_knowledge_file_uploader", ["uploader_id"])


_ALL: List[Migration] = [
    Migration(
        version="2026_04_20_p3_auth",
        description="引入用户表 / conversation.user_id / knowledge_base.owner_id / kb_access_grants",
        run=_migrate_p3_auth,
    ),
    Migration(
        version="2026_04_21_config_center",
        description="引入配置中心：chayuan_config / chayuan_config_history",
        run=_migrate_config_center,
    ),
    Migration(
        version="2026_04_22_knowledge_source",
        description="引入知识源抽象：knowledge_source / connection / schema_cache / source_access_grants；回填向量源影子行",
        run=_migrate_knowledge_source,
    ),
    Migration(
        version="2026_04_22_audit_log",
        description="引入审计日志 chayuan_audit_log",
        run=_migrate_audit_log,
    ),
    Migration(
        version="2026_04_22_sql_training",
        description="引入 Text2SQL 训练语料表 sql_training_sample（Vanna-style RAG 数据面）",
        run=_migrate_sql_training,
    ),
    Migration(
        version="2026_04_22_governance",
        description="P1-9 数据治理四件套：policy / lineage / lineage_touch / usage_counter",
        run=_migrate_governance,
    ),
    Migration(
        version="2026_04_22_graphrag",
        description="B 路线：GraphRAG 实体 / 关系 / 社区 三表",
        run=_migrate_graphrag,
    ),
    Migration(
        version="2026_04_22_kb_storage_backend",
        description="knowledge_base 加 storage_backend 列（按 KB 存储后端覆盖）",
        run=_migrate_kb_storage_backend,
    ),
    Migration(
        version="2026_05_01_kb_vector_namespace",
        description="knowledge_base 加 vector_namespace 列（解耦用户名称与向量库集合名）",
        run=_migrate_kb_vector_namespace,
    ),
    Migration(
        version="2026_05_01_kb_file_dedupe",
        description="knowledge_file 加 file_hash / uploader_id（上传重复检测）",
        run=_migrate_kb_file_dedupe,
    ),
    Migration(
        version="2026_04_27_model_metadata",
        description="模型元数据 model_metadata：LLM 自动补全的简介/发布日期/性能/上下文长度",
        run=_migrate_model_metadata,
    ),
    Migration(
        version="2026_04_30_route_context",
        description="统一上下文定位历史 route_context",
        run=_migrate_route_context,
    ),
    Migration(
        version="2026_04_30_annotation_center",
        description="数据标注中心 annotation_task / annotation_feedback 表",
        run=_migrate_annotation_tables,
    ),
    Migration(
        version="2026_04_30_annotation_feedback",
        description="多人标注与用户反馈 annotation_feedback 表",
        run=_migrate_annotation_feedback,
    ),
    Migration(
        version="2026_05_01_data_mount",
        description="训练数据挂载 data_mount / artifact / hit_log 表",
        run=_migrate_data_mount_tables,
    ),
]


# ---------------------------------------------------------------------------
# version 表 + 调度器
# ---------------------------------------------------------------------------

_VERSION_TABLE = "chayuan_schema_version"


def _ensure_version_table(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {_VERSION_TABLE} ("
                "version VARCHAR(64) PRIMARY KEY, applied_at VARCHAR(32))"
            )
        )


def _applied_versions(engine: Engine) -> set:
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT version FROM {_VERSION_TABLE}")).fetchall()
    return {r[0] for r in rows}


def _mark_applied(engine: Engine, version: str) -> None:
    from datetime import datetime
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {_VERSION_TABLE} (version, applied_at) VALUES (:v, :t)"
            ),
            {"v": version, "t": datetime.utcnow().isoformat()},
        )


def run_migrations() -> None:
    """在 startup 时调用。不抛异常，只打 error 日志，避免 DB 异常直接挂掉 API。"""
    try:
        from chayuan.server.db.base import engine
    except Exception as e:  # noqa: BLE001
        logger.error("migrations: db engine not available: %r", e)
        return

    try:
        _ensure_version_table(engine)
        applied = _applied_versions(engine)
        for m in _ALL:
            if m.version in applied:
                continue
            logger.info("migrations: applying %s (%s)", m.version, m.description)
            try:
                m.run(engine)
                _mark_applied(engine, m.version)
                logger.info("migrations: %s applied", m.version)
            except Exception as e:  # noqa: BLE001
                logger.error("migrations: %s failed: %r", m.version, e, exc_info=True)
                # 继续后续 migration 或直接 break 都行；这里选择 break 避免前后依赖翻车
                break
    except Exception as e:  # noqa: BLE001
        logger.error("migrations: unexpected error: %r", e, exc_info=True)
