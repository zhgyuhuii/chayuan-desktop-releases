"""Text2MongoQuery 生成器。

让 LLM 依据 collection 的字段采样生成 MongoDB 查询语义：

输出 JSON：
{
  "collection": "<名>",
  "op": "find" | "aggregate",
  "filter": { ... },               // op=find 时使用
  "projection": { "_id": 0, ... }, // 可选
  "sort": { "createdAt": -1 },     // 可选
  "pipeline": [ ... ],             // op=aggregate 时使用
  "reason": "<简述>"
}

只允许只读操作：find / aggregate；pipeline 里禁止 $out / $merge。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from chayuan.server.knowledge_source.types import NLQuery, SchemaSnapshot

logger = logging.getLogger("chayuan.knowledge_source.text2mongo")


SYSTEM_PROMPT = """你是 MongoDB 查询专家。根据用户问题和可用 collection 字段采样，生成**只读**查询。
严格规则：
1. 只允许 op=find 或 op=aggregate；绝对禁止使用 $out / $merge / $function / $where / mapReduce。
2. 只能使用「可用 collection 清单」中的 collection 与字段；不要编造。
3. 输出必须是合法 JSON 对象，结构：
   {{"collection":"<名>","op":"find"|"aggregate","filter":{{...}},"projection":{{...}},"sort":{{...}},"pipeline":[...],"limit":{top_k},"reason":"<两句话>"}}
4. op=find 时使用 filter/projection/sort/limit；op=aggregate 时使用 pipeline（最后一个 stage 应包含 $limit {top_k}）。
5. 无法回答时输出 {{"collection":"","op":"find","filter":{{}},"pipeline":[],"reason":"无法回答：<简述>"}}。
"""


USER_PROMPT = """【用户问题】
{query}

【可用 collection 清单（含 3 行采样）】
{schema_block}

【最近对话】
{history_block}

请输出 JSON。"""


BANNED_STAGES = ("$out", "$merge", "$function", "$where", "$accumulator", "$expr")


def _render_schema(schema: SchemaSnapshot, max_collections: int = 20) -> str:
    blocks: List[str] = []
    for t in schema.tables[:max_collections]:
        lines = [f"Collection: {t.name}"]
        if t.comment:
            lines.append(f"  desc: {t.comment}")
        if t.columns:
            field_hint = ", ".join(
                f"{c.name}({c.type})" for c in t.columns[:20]
            )
            lines.append(f"  fields: {field_hint}")
        for i, row in enumerate(t.sample_rows[:3]):
            lines.append(f"  sample#{i+1}: {json.dumps(row, ensure_ascii=False, default=str)[:220]}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "(无可用 collection)"


def _render_history(history, limit: int = 4) -> str:
    items = (history or [])[-limit:]
    if not items:
        return "(无)"
    return "\n".join(
        f"- {h.get('role', 'user')}: {str(h.get('content', ''))[:200]}" for h in items
    )


def validate_query(spec: Dict[str, Any], allowed_collections: List[str]) -> Optional[str]:
    """校验生成的查询是否合法/安全。返回 None 表示通过，否则返回错误描述。"""
    if not isinstance(spec, dict):
        return "返回结构非对象"
    col = (spec.get("collection") or "").strip()
    if not col:
        return "缺少 collection"
    if allowed_collections and col not in allowed_collections:
        return f"collection {col!r} 不在白名单中"
    op = (spec.get("op") or "find").lower()
    if op not in ("find", "aggregate"):
        return f"不支持的 op：{op}"
    if op == "aggregate":
        pipeline = spec.get("pipeline") or []
        if not isinstance(pipeline, list):
            return "pipeline 必须为数组"
        for stage in pipeline:
            if not isinstance(stage, dict):
                return "pipeline stage 必须为对象"
            for banned in BANNED_STAGES:
                if banned in stage:
                    return f"禁止的 stage：{banned}"
    return None


def generate_query(
    nl: NLQuery,
    schema: SchemaSnapshot,
    llm_model: Optional[str] = None,
) -> Dict[str, Any]:
    """N-2 升级：走 structured_llm / with_structured_output。"""
    from chayuan.server.shared.structured_llm import call_structured
    from chayuan.server.shared.structured_schemas import MongoQueryGen

    top_k = max(1, int(nl.top_k or 50))
    sys_msg = SYSTEM_PROMPT.format(top_k=top_k)
    usr_msg = USER_PROMPT.format(
        query=nl.query,
        schema_block=_render_schema(schema),
        history_block=_render_history(nl.history),
    )
    res = call_structured(
        system=sys_msg, user=usr_msg, schema=MongoQueryGen,
        llm_model=(llm_model or nl.llm_model),
        default=MongoQueryGen(
            collection="", op="find", filter={}, projection={}, sort={},
            pipeline=[], limit=top_k, reason="LLM 失败或解析失败",
        ),
    )
    if res is None:
        res = MongoQueryGen(limit=top_k)
    return {
        "collection": res.collection,
        "op": res.op or "find",
        "filter": res.filter or {},
        "projection": res.projection or {},
        "sort": res.sort or {},
        "pipeline": res.pipeline or [],
        "limit": int(res.limit or top_k),
        "reason": res.reason or "",
    }
