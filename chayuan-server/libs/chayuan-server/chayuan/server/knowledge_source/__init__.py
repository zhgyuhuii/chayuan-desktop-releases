"""察元知识源（Knowledge Source）子系统。

在「向量库型 KB」（现有 KnowledgeBase）之上，引入更上位抽象 KnowledgeSource，
统一接入结构化 / 半结构化数据源：MySQL、PostgreSQL、SQLite、SQL Server、
Oracle、ClickHouse、Doris、MongoDB、Elasticsearch 等。

所有接入源实现统一 Connector 契约（见 base.py），对外暴露：

- test_connection  —— 连通性验证（创建时必过）
- introspect       —— Schema 快照（驱动 Text2X 生成器）
- search           —— 归一为 RetrievalChunk 的检索结果

多源并行 / 汇总 / SSE 进度推送由 orchestrator.py 完成。
"""

from chayuan.server.knowledge_source.types import (  # noqa: F401
    NLQuery,
    RetrievalChunk,
    SchemaSnapshot,
    SourceKind,
)
