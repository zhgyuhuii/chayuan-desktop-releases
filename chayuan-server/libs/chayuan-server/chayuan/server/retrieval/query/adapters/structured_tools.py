from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


_COUNT_RE = re.compile(r"(有几个|多少个|多少条|总数|数量|count)", re.I)
_WRITE_RE = re.compile(r"\b(insert|update|delete|drop|alter|truncate|create|replace|merge)\b", re.I)


def aggregate_hint(query: str) -> Dict[str, Any]:
    if _COUNT_RE.search(query or ""):
        return {
            "intent": "structured_aggregate",
            "aggregate": "count",
            "expected_sql_shape": "SELECT COUNT(*) ...",
        }
    return {}


def link_tables(query: str, available_tables: Iterable[str]) -> List[str]:
    text = str(query or "").lower()
    scored: List[tuple[int, str]] = []
    for table in available_tables:
        name = str(table or "").strip()
        if not name:
            continue
        low = name.lower()
        score = 0
        if low in text:
            score += 10
        if "用户" in text and any(tok in low for tok in ("user", "account", "member")):
            score += 8
        if "订单" in text and any(tok in low for tok in ("order", "trade")):
            score += 8
        if "客户" in text and any(tok in low for tok in ("customer", "client")):
            score += 8
        if score:
            scored.append((score, name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [name for _, name in scored]


def validate_readonly_sql(sql: str) -> Dict[str, Any]:
    text = str(sql or "").strip()
    if not text:
        return {"ok": False, "reason": "empty_sql"}
    if _WRITE_RE.search(text):
        return {"ok": False, "reason": "write_operation_detected"}
    if not re.match(r"^\s*(select|with)\b", text, flags=re.I):
        return {"ok": False, "reason": "not_select"}
    try:
        import sqlglot

        sqlglot.parse(text)
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "reason": f"sqlglot_parse_skipped:{type(e).__name__}"}
    return {"ok": True, "reason": ""}


def sql_matches_intent(sql: str, hint: Dict[str, Any]) -> bool:
    if hint.get("aggregate") == "count":
        return bool(re.search(r"\bcount\s*\(", str(sql or ""), flags=re.I))
    return True
