from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, model_validator

from chayuan.server.pydantic_v2 import BaseModel, Field


_FORBIDDEN_EMBEDDING_KEYS = {
    "embed_model",
    "embedding_model",
    "model_name_for_embedding",
    "embedding",
    "embeddings",
}


def reject_external_embedding_fields(payload: Any) -> None:
    """Reject embedding model overrides before Pydantic drops unknown fields."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in _FORBIDDEN_EMBEDDING_KEYS:
                raise ValueError("embedding_model_not_allowed")
            reject_external_embedding_fields(value)
    elif isinstance(payload, list):
        for value in payload:
            reject_external_embedding_fields(value)


class QueryContent(BaseModel):
    content_id: Optional[str] = Field(None, description="调用方传入的内容 ID；为空时服务端生成")
    text: str = Field(..., description="要查询的一段内容")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class QueryOptions(BaseModel):
    model_config = ConfigDict(extra="allow")

    top_k: int = Field(5, ge=1, le=50)
    per_query_top_k: Optional[int] = Field(None, ge=1, le=50)
    top_k_per_query: Optional[int] = Field(None, ge=1, le=50)
    score_threshold: Optional[float] = Field(None, ge=0.0, le=2.0)
    use_hybrid: Optional[bool] = None
    use_rerank: Optional[bool] = None
    fusion: Optional[str] = None
    merge_strategy: Optional[str] = None
    rewrite_strategy: str = "auto"
    return_diagnostics: bool = True
    timeout_ms: int = Field(30000, ge=1000, le=300000)
    structured_scopes: Dict[str, List[str]] = Field(default_factory=dict)
    vector_scopes: Dict[str, List[str]] = Field(default_factory=dict)

    @property
    def effective_top_k(self) -> int:
        return int(self.top_k_per_query or self.per_query_top_k or self.top_k or 5)

    @property
    def effective_fusion(self) -> str:
        return str(self.merge_strategy or self.fusion or "rrf")


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    knowledge_base: Optional[str] = Field(None, description="知识库名称；兼容字段")
    kb_id: Optional[str] = Field(None, description="统一知识库 ID，如 doc:name / src:42")
    ku_ids: List[str] = Field(default_factory=list, description="统一知识库 ID 列表，作为新查询真源")
    intent: Optional[str] = Field(None, description="调用方可选传入的查询意图；为空时服务端自动识别")
    structured_scopes: Dict[str, List[str]] = Field(default_factory=dict)
    vector_scopes: Dict[str, List[str]] = Field(default_factory=dict)
    contents: List[QueryContent] = Field(..., min_length=1)
    options: QueryOptions = Field(default_factory=QueryOptions)

    @model_validator(mode="before")
    @classmethod
    def _reject_embedding_overrides(cls, data: Any) -> Any:
        reject_external_embedding_fields(data)
        return data

    @model_validator(mode="after")
    def _normalize_compat_fields(self) -> "SearchRequest":
        # 正向:kb_id / knowledge_base → ku_ids
        if self.kb_id and self.kb_id not in self.ku_ids:
            self.ku_ids.insert(0, self.kb_id)
        elif not self.ku_ids and self.knowledge_base:
            self.ku_ids.append(self.knowledge_base)
        # 反向:ku_ids → kb_id / knowledge_base
        # 必要性:仍在生产环境跑的旧 service.py 走 registry.resolve_ref(kb_id, knowledge_base),
        # 前端只发 ku_ids 时会被报"knowledge_base 与 kb_id 至少提供一个"。统一在此回填,
        # 让新旧两条消费路径在 schema 层就拿到一致字段。
        if not self.kb_id and self.ku_ids:
            self.kb_id = self.ku_ids[0]
        if not self.knowledge_base and self.ku_ids:
            head = self.ku_ids[0]
            # 旧 service.py 期望 knowledge_base 是裸 KB 名(无 doc:/src: 前缀),
            # 同时新 service.py 自己看 ku_ids,所以这里给个对老路径友好的默认值。
            if isinstance(head, str) and ":" in head:
                self.knowledge_base = head.split(":", 1)[1]
            else:
                self.knowledge_base = head
        if self.structured_scopes and not self.options.structured_scopes:
            self.options.structured_scopes = dict(self.structured_scopes)
        if self.vector_scopes and not self.options.vector_scopes:
            self.options.vector_scopes = dict(self.vector_scopes)
        return self


class KnowledgeBaseListQuery(BaseModel):
    kind: Optional[str] = None
    include_vector: bool = False


class ErrorInfo(BaseModel):
    code: str
    message: str

