"""知识源子系统核心类型定义。

原则：**归一化**。所有 Connector（SQL / Mongo / ES / Vector）检索出的结果都
收敛到 RetrievalChunk；orchestrator 对上层（chat / webui）只暴露这一种片段
结构，避免每加一种源就改一遍 UI / prompt 编排。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SourceKind(str, Enum):
    VECTOR = "vector"
    # VS：外部向量库（BYO collection），与 VECTOR（受管 KB）分开
    # - VECTOR = 平台托管的知识库：文件上传 + 自动向量化，落在 KB_ROOT_PATH
    # - VS     = 用户自己的 Milvus / PG / ES / Chroma / Zilliz / Relyt 集合，我们只负责连接 + 查询
    VS = "vs"
    SQL = "sql"
    MONGO = "mongo"
    ES = "es"


# ---------------------------------------------------------------------------
# 检索归一片段
# ---------------------------------------------------------------------------

@dataclass
class Citation:
    """出处描述。前端决定如何渲染与下载。"""

    # 必填：给用户看的标题。对 SQL=表名 / 对 Mongo=collection / 对 ES=index /
    # 对 Vector=文件名。
    title: str
    # 可选：机器可读的 source_id（用于 download_url 等）
    source_id: Optional[int] = None
    source_kind: Optional[str] = None
    # 生成的查询（SQL 字符串 / aggregation JSON / ES DSL）；前端可 expander 展开
    generated_query: Optional[str] = None
    # 直接可点击的下载 URL（SQL 结果 csv / ES json / vector 原文件）
    download_url: Optional[str] = None
    # 预览链接（浏览器内打开，非强制下载）
    preview_url: Optional[str] = None
    # 其它 metadata（表/列/行号 / collection+文档 id / ES index+doc_id 等）
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalChunk:
    """所有源统一的检索结果片段。

    SQL/ES/Mongo 返回的行/文档会被 Connector 序列化为 content（markdown/纯文本），
    由 LLM 合成答复。score 在向量库是余弦相似度，在结构化源是 Connector 自己
    定义的相关性（默认按命中顺序线性衰减 1.0→0.1）。
    """

    content: str
    citation: Citation
    score: float = 1.0
    # 片段归属源，必填；orchestrator 依此做分组
    source_id: int = 0
    source_kind: str = ""

    def to_wire(self) -> Dict[str, Any]:
        """转换为 JSON 可序列化字典（SSE / HTTP 响应 / 缓存用）。"""
        return {
            "content": self.content,
            "score": self.score,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "citation": {
                "title": self.citation.title,
                "source_id": self.citation.source_id,
                "source_kind": self.citation.source_kind,
                "generated_query": self.citation.generated_query,
                "download_url": self.citation.download_url,
                "preview_url": self.citation.preview_url,
                "meta": self.citation.meta,
            },
        }


# ---------------------------------------------------------------------------
# Schema 快照（Text2SQL / Text2ESQL 的稳定语义层）
# ---------------------------------------------------------------------------

@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    comment: str = ""


@dataclass
class TableInfo:
    """SQL 表 / Mongo collection / ES index 统一描述。"""

    name: str
    schema: str = ""           # schema / database / namespace
    comment: str = ""
    columns: List[ColumnInfo] = field(default_factory=list)
    sample_rows: List[Dict[str, Any]] = field(default_factory=list)
    row_count_estimate: Optional[int] = None

    def ddl_hint(self) -> str:
        """供 Text2X prompt 使用的轻量 DDL 片段（比完整 DDL 省 token）。"""
        cols = []
        for c in self.columns:
            line = f"  {c.name} {c.type}"
            if c.primary_key:
                line += " PK"
            if not c.nullable:
                line += " NOT NULL"
            if c.comment:
                line += f"  -- {c.comment}"
            cols.append(line)
        head = f"-- {self.comment}\n" if self.comment else ""
        return f"{head}TABLE {self.name} (\n" + "\n".join(cols) + "\n)"


@dataclass
class SchemaSnapshot:
    """Connector.introspect() 的统一返回。"""

    source_id: int
    source_kind: str
    dialect: str = ""          # mysql / postgres / mongo / es ...
    tables: List[TableInfo] = field(default_factory=list)
    # Connector 可塞原始信息，供调试
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 查询输入
# ---------------------------------------------------------------------------

@dataclass
class NLQuery:
    """检索输入。SQL / Mongo / ES 的生成器都读这个结构。

    - history：上下文，允许多轮（比如"把上一条 SQL 换成 2023 年"）
    - hints：允许用户或 router 强制注入表 / collection / index 白名单
    - llm_model：生成查询用哪个模型，允许与主回答模型不同（通常用小模型省钱）

    **治理字段**（P1-9）：
    - user_id / user_role：一路从入口透传到 Connector，让结果集脱敏能按用户角色做
    - masking_level_override：强制设定脱敏等级，覆盖默认"按 role 推导"的规则
    """

    query: str
    history: List[Dict[str, str]] = field(default_factory=list)
    hints: Dict[str, Any] = field(default_factory=dict)
    top_k: int = 5
    llm_model: Optional[str] = None
    # 每个源独立超时（秒），orchestrator 会用 asyncio.wait_for 包裹
    timeout: float = 30.0
    # 治理上下文
    user_id: Optional[int] = None
    user_role: str = ""
    masking_level_override: str = ""
