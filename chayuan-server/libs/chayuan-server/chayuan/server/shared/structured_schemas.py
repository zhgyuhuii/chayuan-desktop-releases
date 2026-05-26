"""Structured LLM 的 Pydantic schema 集合。

把散落在各模块里的 LLM JSON 输出 schema 集中到这里，便于：
- 统一迭代（加字段不再四处改）
- 复用给前端 openapi-client 生成 TypeScript 类型
- 测试时直接 import 用
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Text2SQL
# ---------------------------------------------------------------------------

class SqlGen(BaseModel):
    """单轮 SQL 生成结果。"""
    sql: str = Field(default="", description="生成的 SQL；无法回答时为空字符串")
    reason: str = Field(default="", description="两句话理由")


class T2SClassify(BaseModel):
    """graph_text2sql.node_classify 输出。"""
    can_answer: bool = True
    reason: str = ""


class T2SSchemaLink(BaseModel):
    """graph_text2sql.node_schema_link 输出。"""
    tables: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Text2Mongo
# ---------------------------------------------------------------------------

class MongoQueryGen(BaseModel):
    collection: str = ""
    op: str = "find"  # find | aggregate
    filter: Dict[str, Any] = Field(default_factory=dict)
    projection: Dict[str, Any] = Field(default_factory=dict)
    sort: Dict[str, Any] = Field(default_factory=dict)
    pipeline: List[Dict[str, Any]] = Field(default_factory=list)
    limit: int = 50
    reason: str = ""


# ---------------------------------------------------------------------------
# Text2ES
# ---------------------------------------------------------------------------

class EsDslGen(BaseModel):
    index: str = ""
    body: Dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


# ---------------------------------------------------------------------------
# 知识源 Router
# ---------------------------------------------------------------------------

class RoutedSource(BaseModel):
    id: int
    score: float = 0.5


class SourceRouterResult(BaseModel):
    """router.route_sources 输出。"""
    items: List[RoutedSource] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GraphRAG 实体 / 关系
# ---------------------------------------------------------------------------

class GraphEntity(BaseModel):
    name: str
    type: str = "OTHER"
    description: str = ""


class GraphRelation(BaseModel):
    src: str
    dst: str
    type: str = ""
    description: str = ""


class GraphExtractionResult(BaseModel):
    entities: List[GraphEntity] = Field(default_factory=list)
    relations: List[GraphRelation] = Field(default_factory=list)
