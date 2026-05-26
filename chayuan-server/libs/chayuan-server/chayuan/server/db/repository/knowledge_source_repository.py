"""knowledge_source 相关 Repository。

提供给 API / Connector / WebUI 的**唯一**入口；密码加解密、白名单 JSON 解析、
schema 缓存增量刷新都在这里收敛。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from chayuan.server.db.models.knowledge_source_model import (
    KnowledgeSourceConnectionModel,
    KnowledgeSourceModel,
    KnowledgeSourceSchemaCache,
    SourceAccessGrantModel,
)
from chayuan.server.db.session import with_session
from chayuan.server.knowledge_source.base import ConnectionSpec
from chayuan.server.knowledge_source.crypto import decrypt, encrypt
from chayuan.server.knowledge_source.registry import normalize_dialect
from chayuan.server.knowledge_source.types import (
    ColumnInfo,
    SchemaSnapshot,
    SourceKind,
    TableInfo,
)

logger = logging.getLogger("chayuan.knowledge_source.repo")


# ---------------------------------------------------------------------------
# 序列化 / 反序列化 helper
# ---------------------------------------------------------------------------

def _loads(s: str, default):
    try:
        return json.loads(s) if s else default
    except Exception:  # noqa: BLE001
        return default


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class PasswordDecryptError(RuntimeError):
    """数据源密码解密失败的领域异常。

    触发条件:`password_enc` 列非空,但 `decrypt(...)` 返回空串(InvalidToken /
    密钥已轮换)。把空明文吞下去会让下游 psycopg2 / pymysql 报"no password supplied"
    一类离散的连接错误,运维需要看到原因。

    这种异常被 universe 路由的 try/except 捕获后会落到 `degraded` 字段,前端
    "内省失败 banner" 会显示带原文的中文文案,运维一眼就能定位。
    """


def row_to_connection_spec(
    conn_row: KnowledgeSourceConnectionModel,
    override: Optional[Dict[str, Any]] = None,
) -> ConnectionSpec:
    """DB 行 → ConnectionSpec（密码已解密）。override 用于测试连接时前端塞新密码。

    密码解密保护:历史密文若用进程级临时密钥加密(用户没设
    CHAYUAN_SOURCE_SECRET_KEY),重启后 Fernet InvalidToken,decrypt 静默返
    空串。这里检测"密文存在但解密成空"的特征,改抛 PasswordDecryptError —
    比让下游连库时报 fe_sendauth: no password supplied 强得多。
    override 里如果显式塞了新 password(测试连接 / 前端临时填的),跳过该校验。
    """
    options = _loads(conn_row.options_json or "", {}) or {}
    allowed = _loads(conn_row.allowed_json or "", {}) or {}

    raw_enc = conn_row.password_enc or ""
    pwd = decrypt(raw_enc)
    has_override_pwd = bool(override and override.get("password"))
    if raw_enc and not pwd and not has_override_pwd:
        raise PasswordDecryptError(
            "数据源密码解密失败:历史密文已用临时密钥加密、重启后无法还原。"
            "请到「知识库管理 → 该数据源」重新填写密码;"
            "并在 basic_settings.yaml 或环境变量配置 CHAYUAN_SOURCE_SECRET_KEY,"
            "避免再次发生(否则每次重启服务密文都失效)。"
        )

    spec = ConnectionSpec(
        dialect=normalize_dialect(conn_row.dialect or ""),
        host=conn_row.host or "",
        port=int(conn_row.port or 0),
        database=conn_row.database or "",
        username=conn_row.username or "",
        password=pwd,
        options=options,
        allowed_tables=list(allowed.get("tables") or []),
        allowed_collections=list(allowed.get("collections") or []),
        allowed_indices=list(allowed.get("indices") or []),
    )
    if override:
        for k, v in override.items():
            if hasattr(spec, k) and v is not None:
                setattr(spec, k, v)
    return spec


# ---------------------------------------------------------------------------
# KnowledgeSourceConnection CRUD
# ---------------------------------------------------------------------------

@with_session
def create_connection(
    session: Session,
    *,
    dialect: str,
    host: str = "",
    port: int = 0,
    database: str = "",
    username: str = "",
    password: str = "",
    options: Optional[Dict[str, Any]] = None,
    allowed: Optional[Dict[str, List[str]]] = None,
    owner_id: Optional[int] = None,
) -> int:
    row = KnowledgeSourceConnectionModel(
        dialect=normalize_dialect(dialect),
        host=host, port=int(port or 0), database=database, username=username,
        password_enc=encrypt(password or ""),
        options_json=_dumps(options or {}),
        allowed_json=_dumps(allowed or {}),
        owner_id=owner_id,
    )
    session.add(row)
    session.flush()
    return int(row.id)


@with_session
def update_connection(
    session: Session,
    connection_id: int,
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    database: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    allowed: Optional[Dict[str, List[str]]] = None,
) -> bool:
    row = session.get(KnowledgeSourceConnectionModel, int(connection_id))
    if row is None:
        return False
    if host is not None:
        row.host = host
    if port is not None:
        row.port = int(port)
    if database is not None:
        row.database = database
    if username is not None:
        row.username = username
    if password is not None and password != "":
        # 空字符串视为"不改"；前端 UI 必须在不改密码时传空串
        row.password_enc = encrypt(password)
    if options is not None:
        row.options_json = _dumps(options)
    if allowed is not None:
        row.allowed_json = _dumps(allowed)
    return True


@with_session
def mark_connection_check(
    session: Session, connection_id: int, ok: bool, error: str = "",
) -> None:
    row = session.get(KnowledgeSourceConnectionModel, int(connection_id))
    if row is None:
        return
    row.last_check_time = datetime.utcnow()
    row.last_check_ok = 1 if ok else 0
    row.last_check_error = (error or "")[:1000]


# ---------------------------------------------------------------------------
# KnowledgeSource CRUD
# ---------------------------------------------------------------------------

@with_session
def create_source(
    session: Session,
    *,
    name: str,
    kind: str,
    display_name: str = "",
    description: str = "",
    vs_kb_id: Optional[int] = None,
    connection_id: Optional[int] = None,
    owner_id: Optional[int] = None,
    visibility: str = "private",
) -> int:
    # 名字冲突直接返回现有 id，保持幂等（向量源名 = kb_name）
    existing = (
        session.query(KnowledgeSourceModel)
        .filter(KnowledgeSourceModel.name == name).one_or_none()
    )
    if existing is not None:
        return int(existing.id)
    row = KnowledgeSourceModel(
        name=name, kind=kind, display_name=display_name or name,
        description=description,
        vs_kb_id=vs_kb_id, connection_id=connection_id,
        owner_id=owner_id, visibility=visibility,
    )
    session.add(row)
    session.flush()
    return int(row.id)


@with_session
def get_source(session: Session, source_id: int) -> Optional[Dict[str, Any]]:
    row = session.get(KnowledgeSourceModel, int(source_id))
    if row is None:
        return None
    return _source_to_dict(row)


@with_session
def get_source_by_name(session: Session, name: str) -> Optional[Dict[str, Any]]:
    row = session.query(KnowledgeSourceModel).filter(
        KnowledgeSourceModel.name == name
    ).one_or_none()
    return _source_to_dict(row) if row else None


@with_session
def list_sources(session: Session, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    q = session.query(KnowledgeSourceModel)
    if kind:
        q = q.filter(KnowledgeSourceModel.kind == kind)
    return [_source_to_dict(r) for r in q.order_by(KnowledgeSourceModel.id.asc()).all()]


@with_session
def update_source(
    session: Session,
    source_id: int,
    *,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    visibility: Optional[str] = None,
) -> bool:
    row = session.get(KnowledgeSourceModel, int(source_id))
    if row is None:
        return False
    if display_name is not None:
        row.display_name = display_name
    if description is not None:
        row.description = description
    if visibility is not None:
        row.visibility = visibility
    return True


@with_session
def delete_source(session: Session, source_id: int) -> bool:
    row = session.get(KnowledgeSourceModel, int(source_id))
    if row is None:
        return False
    # 同时级联删 connection（向量源 connection_id 为空，不受影响）
    conn_id = row.connection_id
    session.delete(row)
    if conn_id:
        conn = session.get(KnowledgeSourceConnectionModel, int(conn_id))
        if conn is not None:
            session.delete(conn)
    # 清理 grants / schema_cache
    session.query(SourceAccessGrantModel).filter(
        SourceAccessGrantModel.source_id == int(source_id)
    ).delete(synchronize_session=False)
    session.query(KnowledgeSourceSchemaCache).filter(
        KnowledgeSourceSchemaCache.source_id == int(source_id)
    ).delete(synchronize_session=False)
    return True


def _source_to_dict(row: KnowledgeSourceModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "display_name": row.display_name or row.name,
        "kind": row.kind,
        "description": row.description or "",
        "vs_kb_id": row.vs_kb_id,
        "connection_id": row.connection_id,
        "owner_id": row.owner_id,
        "visibility": row.visibility or "private",
        "create_time": row.create_time,
        "update_time": row.update_time,
    }


@with_session
def get_connection(session: Session, connection_id: int) -> Optional[KnowledgeSourceConnectionModel]:
    row = session.get(KnowledgeSourceConnectionModel, int(connection_id))
    if row is None:
        return None
    session.expunge(row)
    return row


@with_session
def connection_spec_for_source(
    session: Session, source_id: int,
) -> Optional[Tuple[str, ConnectionSpec]]:
    """联查一次，返回 (dialect, ConnectionSpec)；向量源返回 None。"""
    src = session.get(KnowledgeSourceModel, int(source_id))
    if src is None or src.kind == SourceKind.VECTOR.value:
        return None
    if not src.connection_id:
        return None
    conn = session.get(KnowledgeSourceConnectionModel, int(src.connection_id))
    if conn is None:
        return None
    session.expunge(conn)
    spec = row_to_connection_spec(conn)
    return spec.dialect, spec


# ---------------------------------------------------------------------------
# Schema cache
# ---------------------------------------------------------------------------

@with_session
def replace_schema_cache(session: Session, source_id: int, snapshot: SchemaSnapshot) -> None:
    session.query(KnowledgeSourceSchemaCache).filter(
        KnowledgeSourceSchemaCache.source_id == int(source_id)
    ).delete(synchronize_session=False)
    for t in snapshot.tables:
        row = KnowledgeSourceSchemaCache(
            source_id=int(source_id),
            object_type=("collection" if snapshot.source_kind == "mongo"
                         else "index" if snapshot.source_kind == "es" else "table"),
            object_name=t.name,
            object_comment=t.comment,
            columns_json=_dumps([c.__dict__ for c in t.columns]),
            sample_rows_json=_dumps(t.sample_rows),
            row_count_estimate=t.row_count_estimate,
        )
        session.add(row)


@with_session
def delete_schema_cache(session: Session, source_id: int) -> int:
    """清掉某 source 的全部 schema 缓存行；返回被删行数。

    改白名单 / 改连接串 都应该触发——否则 Text2SQL 可能读到旧表清单。
    """
    n = session.query(KnowledgeSourceSchemaCache).filter(
        KnowledgeSourceSchemaCache.source_id == int(source_id)
    ).delete(synchronize_session=False)
    return int(n or 0)


@with_session
def load_schema_cache(session: Session, source_id: int) -> Optional[SchemaSnapshot]:
    rows = session.query(KnowledgeSourceSchemaCache).filter(
        KnowledgeSourceSchemaCache.source_id == int(source_id)
    ).all()
    if not rows:
        return None
    src = session.get(KnowledgeSourceModel, int(source_id))
    kind = src.kind if src is not None else ""
    conn_row = session.get(KnowledgeSourceConnectionModel, int(src.connection_id)) if (src and src.connection_id) else None
    dialect = normalize_dialect(conn_row.dialect) if conn_row is not None else kind
    tables: List[TableInfo] = []
    for r in rows:
        col_dicts = _loads(r.columns_json or "", [])
        cols = [ColumnInfo(**c) for c in col_dicts if isinstance(c, dict)]
        tables.append(TableInfo(
            name=r.object_name,
            comment=r.object_comment or "",
            columns=cols,
            sample_rows=_loads(r.sample_rows_json or "", []),
            row_count_estimate=r.row_count_estimate,
        ))
    return SchemaSnapshot(
        source_id=int(source_id),
        source_kind=kind,
        dialect=dialect,
        tables=tables,
    )


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------

@with_session
def grant_source_access(
    session: Session,
    source_id: int,
    user_id: int,
    role: str = "reader",
    granted_by: Optional[int] = None,
) -> None:
    if role not in ("reader", "editor"):
        role = "reader"
    row = session.query(SourceAccessGrantModel).filter(
        SourceAccessGrantModel.source_id == int(source_id),
        SourceAccessGrantModel.user_id == int(user_id),
    ).one_or_none()
    if row is None:
        row = SourceAccessGrantModel(
            source_id=int(source_id), user_id=int(user_id),
            role=role, granted_by=granted_by,
        )
        session.add(row)
    else:
        row.role = role
        row.granted_by = granted_by


@with_session
def revoke_source_access(session: Session, source_id: int, user_id: int) -> None:
    session.query(SourceAccessGrantModel).filter(
        SourceAccessGrantModel.source_id == int(source_id),
        SourceAccessGrantModel.user_id == int(user_id),
    ).delete(synchronize_session=False)


@with_session
def grant_source_access_batch(
    session: Session,
    source_ids: List[int],
    user_ids: List[int],
    role: str = "reader",
    granted_by: Optional[int] = None,
) -> int:
    """批量授权：满足"一次对多个用户授权多个源"的诉求。返回新增条数。"""
    if role not in ("reader", "editor"):
        role = "reader"
    cnt = 0
    for sid in source_ids:
        for uid in user_ids:
            row = session.query(SourceAccessGrantModel).filter(
                SourceAccessGrantModel.source_id == int(sid),
                SourceAccessGrantModel.user_id == int(uid),
            ).one_or_none()
            if row is None:
                session.add(SourceAccessGrantModel(
                    source_id=int(sid), user_id=int(uid),
                    role=role, granted_by=granted_by,
                ))
                cnt += 1
            else:
                row.role = role
                row.granted_by = granted_by
    return cnt


@with_session
def list_source_grants(session: Session, source_id: int) -> List[Dict[str, Any]]:
    rows = session.query(SourceAccessGrantModel).filter(
        SourceAccessGrantModel.source_id == int(source_id)
    ).all()
    return [
        {"user_id": r.user_id, "role": r.role, "granted_by": r.granted_by}
        for r in rows
    ]


@with_session
def list_accessible_source_ids(
    session: Session, user_id: Optional[int], role: str = "",
) -> List[int]:
    """列出用户可读的源 id（owner / 被授权 / public 的并集）。

    admin 不过滤（由上层判定后直接跳过本函数）。
    """
    q = session.query(KnowledgeSourceModel.id, KnowledgeSourceModel.owner_id,
                       KnowledgeSourceModel.visibility)
    ids: List[int] = []
    for row in q.all():
        sid, owner_id, vis = int(row[0]), row[1], row[2]
        if vis == "public":
            ids.append(sid)
            continue
        if user_id is not None and owner_id is not None and int(owner_id) == int(user_id):
            ids.append(sid)
            continue
    if user_id is not None:
        g_rows = session.query(SourceAccessGrantModel.source_id).filter(
            SourceAccessGrantModel.user_id == int(user_id)
        ).all()
        ids.extend(int(r[0]) for r in g_rows)
    return sorted(set(ids))
