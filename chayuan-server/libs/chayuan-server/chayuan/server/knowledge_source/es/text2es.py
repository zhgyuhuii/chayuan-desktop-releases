"""Text2ES DSL 生成器。

让 LLM 根据 index mapping + 采样文档生成 ES Query DSL：

{
  "index": "<索引名>",
  "body": {
     "query": {...},
     "sort": [...],
     "size": <N>,
     "_source": [...]  // 可选
  },
  "reason": "<简述>"
}

禁止 "scripts" / "update_by_query"（这里本身只做 _search，但做一层 body 审计）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from chayuan.server.knowledge_source.types import NLQuery, SchemaSnapshot

logger = logging.getLogger("chayuan.knowledge_source.text2es")


SYSTEM_PROMPT = """你是 Elasticsearch 查询专家。根据用户问题和可用 index 映射，生成**只读** Query DSL。
严格规则：
1. 只生成 _search 使用的 body，禁止 update / delete / script_fields 等；
2. 只能使用「可用 index 清单」中的 index 与字段；不要编造；
3. body 必须含 size（不超过 {top_k}）；如无特殊排序需求，按 _score 降序；
4. 若含全文检索字段，优先 match / multi_match / query_string；中文可用 match + minimum_should_match；
5. 输出 JSON：{{"index":"<名>","body":{{...}},"reason":"<两句话>"}}
6. 无法回答时输出 {{"index":"","body":{{"query":{{"match_none":{{}}}}}},"reason":"无法回答：<简述>"}}。
"""


USER_PROMPT = """【用户问题】
{query}

【可用 index 清单】
{schema_block}

【最近对话】
{history_block}

请输出 JSON。"""


def _render_schema(schema: SchemaSnapshot, max_indices: int = 20) -> str:
    blocks: List[str] = []
    for t in schema.tables[:max_indices]:
        lines = [f"Index: {t.name}"]
        if t.columns:
            field_hint = ", ".join(f"{c.name}({c.type})" for c in t.columns[:30])
            lines.append(f"  fields: {field_hint}")
        for i, row in enumerate(t.sample_rows[:2]):
            lines.append(f"  sample#{i+1}: {json.dumps(row, ensure_ascii=False, default=str)[:220]}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "(无可用 index)"


def _render_history(history, limit: int = 4) -> str:
    items = (history or [])[-limit:]
    if not items:
        return "(无)"
    return "\n".join(
        f"- {h.get('role', 'user')}: {str(h.get('content', ''))[:200]}" for h in items
    )


BANNED_KEYS = ("script", "script_fields", "runtime_mappings")


def validate_dsl(spec: Dict[str, Any], allowed_indices: List[str], top_k: int) -> Optional[str]:
    if not isinstance(spec, dict):
        return "返回结构非对象"
    idx = (spec.get("index") or "").strip()
    if not idx:
        return "缺少 index"
    if allowed_indices and idx not in allowed_indices:
        return f"index {idx!r} 不在白名单"
    body = spec.get("body") or {}
    if not isinstance(body, dict):
        return "body 必须为对象"
    # 限制 size
    size = body.get("size") or top_k or 50
    try:
        size = int(size)
    except Exception:
        return "size 非整型"
    if size <= 0 or size > max(top_k * 4, 200):
        return f"size 越界：{size}"
    body["size"] = min(size, max(top_k, 50))
    # 禁用脚本字段
    def _scan(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in BANNED_KEYS:
                    return k
                r = _scan(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for it in obj:
                r = _scan(it)
                if r:
                    return r
        return None
    banned = _scan(body)
    if banned:
        return f"DSL 中出现禁用字段：{banned}"
    return None


def generate_dsl(
    nl: NLQuery,
    schema: SchemaSnapshot,
    llm_model: Optional[str] = None,
) -> Dict[str, Any]:
    """N-2 升级：走 structured_llm / with_structured_output。"""
    from chayuan.server.shared.structured_llm import call_structured
    from chayuan.server.shared.structured_schemas import EsDslGen

    top_k = max(1, int(nl.top_k or 50))
    sys_msg = SYSTEM_PROMPT.format(top_k=top_k)
    usr_msg = USER_PROMPT.format(
        query=nl.query,
        schema_block=_render_schema(schema),
        history_block=_render_history(nl.history),
    )
    res = call_structured(
        system=sys_msg, user=usr_msg, schema=EsDslGen,
        llm_model=(llm_model or nl.llm_model),
        default=EsDslGen(
            index="", body={"query": {"match_none": {}}, "size": top_k},
            reason="LLM 失败或解析失败",
        ),
    )
    body = (res.body if res else {}) or {"query": {"match_none": {}}, "size": top_k}
    body.setdefault("size", top_k)
    return {
        "index": res.index if res else "",
        "body": body,
        "reason": res.reason if res else "",
    }
