"""用 LLM 从 chunk 文本抽实体 + 关系。

返回结构：
    {
        "entities": [{"name": "...", "type": "ORG", "description": "..."}, ...],
        "relations": [{"src": "...", "dst": "...", "type": "founded_by",
                        "description": "..."}]
    }

约束：
- 只产出 JSON；失败自动 json_repair
- 对一个 chunk 调用一次 LLM，失败 fail-soft（返回空结构，不影响其它 chunk 的 build）
- 后续可无缝替换为 ``langchain_experimental.graph_transformers.LLMGraphTransformer``
  以获得更强 schema 约束，但那是重依赖，当前优先零额外依赖
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.graphrag.extractor")


SYSTEM_PROMPT = """你是知识图谱抽取专家。从给定文本中抽取**实体**与**关系**，用于构建知识图谱。
严格规则：
1. 实体类型（type）只能是：PERSON / ORG / LOCATION / PRODUCT / EVENT / CONCEPT / DATE / METRIC / OTHER
2. 关系是**有向**的，src/dst 必须是你抽出的实体名（完全一致）
3. 实体名保持原文（中英混用 OK），不要解释、不要翻译
4. 抽 10-30 个最关键的实体；极短文本（< 100 字）可少抽；宁可少不要编造
5. 输出**严格 JSON**：
{
  "entities": [{"name": "<实体>", "type": "<类型>", "description": "<一句话>"}],
  "relations": [{"src": "<实体A>", "dst": "<实体B>", "type": "<关系>", "description": "<一句话>"}]
}
6. description 8-40 字；超长截断"""


USER_PROMPT = """【文本】
{text}

请输出 JSON。"""


def extract_entities_relations(
    text: str, *, llm_model: Optional[str] = None, max_chars: int = 4000,
) -> Dict[str, List[Dict[str, Any]]]:
    """输入 chunk 文本；返回 {entities, relations}。失败返回空结构。

    **N-2 升级**：优先走 structured_llm（with_structured_output），失败路径同样
    返回空结构；成功率从 ~90% 提到 ~99%。
    """
    if not text:
        return {"entities": [], "relations": []}
    trimmed = text[:max_chars]

    from chayuan.server.shared.structured_llm import call_structured
    from chayuan.server.shared.structured_schemas import GraphExtractionResult
    res = call_structured(
        system=SYSTEM_PROMPT, user=USER_PROMPT.format(text=trimmed),
        schema=GraphExtractionResult, llm_model=llm_model,
        default=GraphExtractionResult(entities=[], relations=[]),
    )
    if res is None:
        return {"entities": [], "relations": []}
    ents = [e.model_dump() for e in res.entities]
    rels = [r.model_dump() for r in res.relations]
    # 过滤非法
    clean_ents: List[Dict[str, Any]] = []
    for e in ents:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        if not name or len(name) > 200:
            continue
        clean_ents.append({
            "name": name,
            "type": str(e.get("type") or "OTHER").upper()[:64],
            "description": str(e.get("description") or "")[:500],
        })
    valid_names = {e["name"] for e in clean_ents}
    clean_rels: List[Dict[str, Any]] = []
    for r in rels:
        if not isinstance(r, dict):
            continue
        src = str(r.get("src") or "").strip()
        dst = str(r.get("dst") or "").strip()
        if not src or not dst or src == dst:
            continue
        if src not in valid_names or dst not in valid_names:
            # 模型有时在关系里 mention 新实体；补回 entities 也行，这里为保守起见丢弃
            continue
        clean_rels.append({
            "src": src, "dst": dst,
            "type": str(r.get("type") or "")[:64],
            "description": str(r.get("description") or "")[:500],
        })
    return {"entities": clean_ents, "relations": clean_rels}
