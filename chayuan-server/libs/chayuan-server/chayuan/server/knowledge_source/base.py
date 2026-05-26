"""Connector 抽象基类。

所有数据源（SQL / Mongo / ES / 向量库）都实现 BaseConnector 契约。
Connector 本身是**无状态**的：构造参数 = 连接信息 + 可选白名单。真正的资源
（SQLAlchemy engine / pymongo Client / ES Client）按需惰性创建，close() 释放。

具体 Connector 应当：

- 在 __init__ 里**不**做网络 I/O；test_connection / introspect / search 才动网
- 尊重 allowed_tables / allowed_collections 白名单
- 对外只抛 ConnectorError；内部异常要 wrap，避免暴露敏感连接串
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from chayuan.server.knowledge_source.types import (
    NLQuery,
    RetrievalChunk,
    SchemaSnapshot,
)

logger = logging.getLogger("chayuan.knowledge_source.connector")


class ConnectorError(Exception):
    """所有 Connector 对外抛出的唯一异常类型。

    code 供前端分类展示（如 auth_failed / network_unreachable / permission_denied）。
    """

    def __init__(self, message: str, code: str = "error", *, dialect: str = ""):
        super().__init__(message)
        self.code = code
        self.dialect = dialect


@dataclass
class ConnectionSpec:
    """统一的连接参数。不同 Connector 按需取用。"""

    dialect: str                # mysql / postgres / sqlite / mssql / oracle / clickhouse / doris / mongo / es
    host: str = ""
    port: int = 0
    database: str = ""
    username: str = ""
    password: str = ""          # 明文；由上层从 DB 取出解密后传入
    options: Dict[str, Any] = field(default_factory=dict)
    # 白名单
    allowed_tables: List[str] = field(default_factory=list)
    allowed_collections: List[str] = field(default_factory=list)
    allowed_indices: List[str] = field(default_factory=list)
    # 连接超时（秒）
    connect_timeout: float = 5.0
    # 强制只读
    read_only: bool = True


class BaseConnector(ABC):
    """所有数据源 Connector 的契约。"""

    #: Connector 自己声明支持的方言（小写），供 registry 注册
    dialects: Tuple[str, ...] = ()

    #: Connector 所属源类型（见 SourceKind）
    source_kind: str = ""

    def __init__(self, spec: ConnectionSpec, source_id: int = 0):
        self.spec = spec
        self.source_id = source_id

    # ----- 连通性 -----

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """同步验证连通性。返回 (ok, 提示信息)。

        **不**抛 ConnectorError；失败只把原因填到消息里，供 UI 直接展示。
        这样前端可以在"测试连接"按钮下方实时显示红/绿文字，无需处理异常。
        """

    # ----- Schema -----

    @abstractmethod
    def introspect(self, sample_rows: int = 3) -> SchemaSnapshot:
        """抓取 schema 快照。耗时操作，UI 触发一次即可，结果落 DB 缓存。"""

    # ----- 检索 -----

    @abstractmethod
    async def search(self, query: NLQuery) -> List[RetrievalChunk]:
        """自然语言检索。Connector 内部决定生成 SQL/DSL 的时机与方式。"""

    # ----- 清理 -----

    def close(self) -> None:
        """释放底层资源。默认实现为空。"""
        return None

    # ----- 便捷方法 -----

    def _trunc(self, text: str, limit: int = 4000) -> str:
        """Prompt / SSE 推送前对字符串做长度保护。"""
        if text is None:
            return ""
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."
