"""知识源 ORM 模型。

为什么不直接扩 `knowledge_base`：
- 后者描述"文件+向量"的语义（embed_model / vs_type / file_count），
  把"DB 连接串 / 白名单表 / 方言"硬塞进去会变得杂乱且破坏兼容；
- 新表 `knowledge_source` 做上位抽象，现有 vector KB 映射为 kind=vector 的影子行，
  老 ACL 表 `kb_access_grants` 保留继续生效，新权限走 `source_access_grants`。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, func

from chayuan.server.db.base import Base


class KnowledgeSourceModel(Base):
    """上位抽象：vector / sql / mongo / es 四类源统一建索引在此表。"""

    __tablename__ = "knowledge_source"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(80), unique=True, index=True, nullable=False,
                  comment="系统内唯一标识；vector 源与 knowledge_base.kb_name 一致")
    display_name = Column(String(120), default="", comment="UI 显示名，可含中文")
    kind = Column(String(16), default="vector", nullable=False, index=True,
                  comment="vector / sql / mongo / es")
    description = Column(Text, default="", comment="业务简介，会被 router 用来挑源")
    # 对 vector 类型，指向 knowledge_base.id；其它类型 NULL
    vs_kb_id = Column(Integer, nullable=True, index=True,
                      comment="向量库型时：knowledge_base.id")
    # 对 sql/mongo/es 类型，指向 knowledge_source_connection.id
    connection_id = Column(Integer, nullable=True, index=True,
                           comment="非 vector 源：knowledge_source_connection.id")
    # 多用户
    owner_id = Column(Integer, nullable=True, index=True, comment="users.id")
    visibility = Column(String(16), default="private", nullable=False, index=True,
                        comment="private / public")
    create_time = Column(DateTime, default=func.now())
    update_time = Column(DateTime, default=func.now(), onupdate=func.now())


class KnowledgeSourceConnectionModel(Base):
    """连接信息。密码字段使用 Fernet 加密；白名单 / 附加选项 JSON 存储。"""

    __tablename__ = "knowledge_source_connection"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dialect = Column(String(32), nullable=False, index=True,
                     comment="mysql/postgres/sqlite/mssql/oracle/clickhouse/doris/mongo/es")
    host = Column(String(255), default="")
    port = Column(Integer, default=0)
    database = Column(String(128), default="")
    username = Column(String(128), default="")
    password_enc = Column(Text, default="", comment="Fernet 加密后的密码")
    # JSON 字符串：ODBC driver / authSource / scheme=https / verify_certs 等
    options_json = Column(Text, default="")
    # 白名单 JSON：allowed_tables / allowed_collections / allowed_indices
    allowed_json = Column(Text, default="")
    # 连通性体检
    last_check_time = Column(DateTime, nullable=True)
    last_check_ok = Column(Integer, default=0, comment="1 成功 / 0 失败")
    last_check_error = Column(Text, default="")
    # 元数据
    owner_id = Column(Integer, nullable=True, index=True)
    create_time = Column(DateTime, default=func.now())
    update_time = Column(DateTime, default=func.now(), onupdate=func.now())


class KnowledgeSourceSchemaCache(Base):
    """introspect 结果缓存。

    设计原则：按 (source_id, object_name) 粒度存；每次 introspect 清空本源旧行，
    重新插入当前快照。Text2SQL 从这张表读 DDL hint，避免每次查询都打数据库。
    """

    __tablename__ = "knowledge_source_schema_cache"
    __table_args__ = (
        UniqueConstraint("source_id", "object_name", name="uq_ksc_source_object"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, nullable=False, index=True)
    object_type = Column(String(16), default="table", comment="table / collection / index")
    object_name = Column(String(255), nullable=False)
    object_comment = Column(Text, default="")
    columns_json = Column(Text, default="", comment="ColumnInfo[] 的 JSON")
    sample_rows_json = Column(Text, default="", comment="3 行采样 JSON")
    row_count_estimate = Column(Integer, nullable=True)
    refreshed_at = Column(DateTime, default=func.now())


class SourceAccessGrantModel(Base):
    """知识源的多用户授权。语义与 kb_access_grants 一致，范围扩到所有 source 类型。

    owner 不依赖本表；admin 直接放行。
    """

    __tablename__ = "source_access_grants"
    __table_args__ = (
        UniqueConstraint("source_id", "user_id", name="uq_sag_source_user"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    role = Column(String(16), default="reader", nullable=False,
                  comment="reader / editor")
    granted_at = Column(DateTime, default=func.now())
    granted_by = Column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Pydantic Schemas（API 出入参）
# ---------------------------------------------------------------------------

class KnowledgeSourceSchema(BaseModel):
    id: int
    name: str
    display_name: str = ""
    kind: str
    description: str = ""
    vs_kb_id: Optional[int] = None
    connection_id: Optional[int] = None
    owner_id: Optional[int] = None
    visibility: str = "private"
    create_time: Optional[datetime] = None
    update_time: Optional[datetime] = None

    class Config:
        from_attributes = True


class KnowledgeSourceConnectionPayload(BaseModel):
    """创建/更新连接时的入参。密码走明文，由 repository 加密后再落库。"""

    dialect: str
    host: str = ""
    port: int = 0
    database: str = ""
    username: str = ""
    password: str = ""
    options: Dict[str, Any] = {}
    allowed: Dict[str, List[str]] = {}   # {"tables":[], "collections":[], "indices":[]}
