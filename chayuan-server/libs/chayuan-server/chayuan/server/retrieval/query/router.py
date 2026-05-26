from __future__ import annotations

import re
from typing import Iterable, Optional

from chayuan.server.retrieval.query.refs import KnowledgeRef


_AGG_RE = re.compile(
    r"(有几个|多少个|多少条|总数|数量|count|求和|合计|总额|平均|均值|最大|最小|top\s*\d*|排名)",
    re.I,
)
_LOOKUP_RE = re.compile(r"(列出|查询|筛选|有哪些|明细|详情|记录|最近|大于|小于|等于|包含)")


def infer_intent(query: str, refs: Iterable[KnowledgeRef], explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    text = str(query or "")
    kinds = {r.kind for r in refs}
    if "structured" in kinds and _AGG_RE.search(text):
        return "structured_aggregate"
    if "structured" in kinds and _LOOKUP_RE.search(text):
        return "structured_lookup"
    if "vector" in kinds and kinds <= {"vector"}:
        return "vector_semantic"
    if len(kinds) > 1:
        return "multi_source"
    if "vector" in kinds:
        return "vector_semantic"
    return "document_qa"
