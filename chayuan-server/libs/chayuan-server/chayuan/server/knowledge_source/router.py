"""知识源路由器（Router）。

用户勾选 N 个源后，不一定所有源都与当前问题相关。对 N>3 的情形用一次小模型调用
做**相关性预筛**，输出一个稀疏子集与每个源的相关性打分，从而：

- 省 Text2SQL / Text2ES 生成的 token 与耗时
- 减少"不相关源强答"带来的噪声（例如工单问题问到了销售库）

路由失败（LLM 不可用 / 解析失败）时**不阻断**：回退到"不路由，全量并行"。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.knowledge_source.router")


SYSTEM_PROMPT = """你是一个检索路由器。给定用户的自然语言问题和一组候选知识源的描述，
判断每个源是否与问题相关，只返回相关的子集。

严格规则：
1. 输出必须是合法 JSON 数组，形如：[{{"id": <int>, "score": <0~1 浮点>}}, ...]
2. 只输出 score ≥ 0.3 的源；最多保留 Top-{max_keep} 个；按 score 降序。
3. 如所有源都不相关，返回 []；宁可多保留也不要漏掉可能相关的。
4. score 含义：1.0 肯定相关；0.7 很可能；0.4 或许；< 0.3 几乎无关。
"""


USER_PROMPT = """【用户问题】
{query}

【候选源（id / kind / 描述）】
{sources_block}

请输出 JSON 数组。"""


def _render_sources(sources: List[Dict[str, Any]]) -> str:
    lines = []
    for s in sources:
        sid = s.get("id")
        kind = s.get("kind")
        name = s.get("display_name") or s.get("name")
        desc = (s.get("description") or "").strip() or "(无描述)"
        lines.append(f"- id={sid} kind={kind} name={name}  desc={desc[:200]}")
    return "\n".join(lines) or "(空)"


def route_sources(
    query: str,
    sources: List[Dict[str, Any]],
    *,
    max_keep: int = 5,
    min_score: float = 0.3,
    llm_model: Optional[str] = None,
    enabled_threshold: int = 4,
) -> List[Dict[str, Any]]:
    """根据问题过滤相关源。

    - ``enabled_threshold``：源数量 < 此阈值时直接返回原列表不路由（少量源不值得多一次 LLM）
    - 失败时返回原列表（fail-open）
    """
    if not sources:
        return []
    if len(sources) < int(enabled_threshold):
        return list(sources)

    from chayuan.server.shared.structured_llm import call_structured
    from chayuan.server.shared.structured_schemas import SourceRouterResult

    sys_msg = SYSTEM_PROMPT.format(max_keep=int(max_keep))
    usr_msg = USER_PROMPT.format(query=query, sources_block=_render_sources(sources))
    res = call_structured(
        system=sys_msg, user=usr_msg, schema=SourceRouterResult,
        llm_model=llm_model, default=SourceRouterResult(items=[]),
    )
    if res is None or not res.items:
        return list(sources)

    picked_ids: List[int] = []
    scored: Dict[int, float] = {}
    for item in res.items:
        try:
            sid = int(item.id)
            sc = float(item.score or 0.0)
            if sc >= min_score:
                scored[sid] = sc
                picked_ids.append(sid)
        except Exception:  # noqa: BLE001
            continue
    if not picked_ids:
        return list(sources)

    by_id = {int(s["id"]): s for s in sources if s.get("id") is not None}
    kept: List[Dict[str, Any]] = []
    for sid in picked_ids[: int(max_keep)]:
        if sid in by_id:
            kept.append({**by_id[sid], "_router_score": scored.get(sid, 0.0)})
    if not kept:
        return list(sources)
    return kept
