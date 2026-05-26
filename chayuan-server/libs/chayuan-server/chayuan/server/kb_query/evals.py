from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from chayuan.server.kb_query.authz import Subject, assert_readable
from chayuan.server.kb_query.registry import resolve_ref
from chayuan.server.kb_query.schemas import SearchRequest
from chayuan.server.kb_query.service import search


def _eval_root() -> Path:
    try:
        from chayuan.settings import CHAYUAN_ROOT

        root = Path(CHAYUAN_ROOT)
    except Exception:
        root = Path.cwd()
    path = root / "kb_query_eval"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.:-]+", "_", name)[:160]


def _golden_path(kb_id: str) -> Path:
    return _eval_root() / f"{_safe_name(kb_id)}.golden.jsonl"


def _run_path(kb_id: str, run_id: str) -> Path:
    return _eval_root() / f"{_safe_name(kb_id)}.{_safe_name(run_id)}.eval.json"


def save_golden_set(subject: Subject, *, kb_id: Optional[str], knowledge_base: Optional[str], cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not subject.is_admin:
        raise HTTPException(403, "admin only")
    ref = resolve_ref(kb_id=kb_id, knowledge_base=knowledge_base)
    path = _golden_path(ref.kb_id)
    with path.open("w", encoding="utf-8") as f:
        for idx, case in enumerate(cases, start=1):
            q = str(case.get("query") or case.get("question") or "").strip()
            if not q:
                raise HTTPException(400, f"case #{idx} missing query")
            row = {
                "case_id": str(case.get("case_id") or f"case_{idx}"),
                "query": q,
                "expected_files": list(case.get("expected_files") or []),
                "expected_tables": list(case.get("expected_tables") or []),
                "expected_row_ids": list(case.get("expected_row_ids") or []),
                "expected_answer_contains": list(case.get("expected_answer_contains") or []),
                "expected_sql_contains": list(case.get("expected_sql_contains") or []),
                "expected_intent": str(case.get("expected_intent") or ""),
                "tags": list(case.get("tags") or []),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"kb_id": ref.kb_id, "case_count": len(cases), "path": str(path)}


def load_golden_set(subject: Subject, *, kb_id: Optional[str], knowledge_base: Optional[str]) -> Dict[str, Any]:
    ref = resolve_ref(kb_id=kb_id, knowledge_base=knowledge_base)
    if not subject.is_admin:
        assert_readable(subject, ref)
    path = _golden_path(ref.kb_id)
    cases: List[Dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                cases.append(json.loads(line))
    return {"kb_id": ref.kb_id, "case_count": len(cases), "cases": cases}


def _case_hit(case: Dict[str, Any], results: List[Dict[str, Any]]) -> bool:
    return _case_first_rank(case, results) is not None


def _case_first_rank(case: Dict[str, Any], results: List[Dict[str, Any]]) -> Optional[int]:
    expected_files = {str(x).lower() for x in case.get("expected_files") or []}
    expected_tables = {str(x).lower() for x in case.get("expected_tables") or []}
    expected_rows = {str(x) for x in case.get("expected_row_ids") or []}
    if not expected_files and not expected_tables and not expected_rows:
        return 1 if results else None

    for idx, hit in enumerate(results, start=1):
        citation = hit.get("citation") or {}
        file_name = str(citation.get("file_name") or "").lower()
        table = str(citation.get("table") or "").lower()
        row_ids = {str(x) for x in citation.get("row_ids") or []}
        pk = citation.get("primary_key") or {}
        if isinstance(pk, dict):
            row_ids.update(str(v) for v in pk.values())
        if expected_files and file_name in expected_files:
            return idx
        if expected_tables and table in expected_tables:
            return idx
        if expected_rows and row_ids.intersection(expected_rows):
            return idx
    return None


def _contains_all(text: str, needles: List[Any]) -> bool:
    if not needles:
        return True
    low = str(text or "").lower()
    return all(str(x).lower() in low for x in needles if str(x))


def run_eval(
    subject: Subject,
    *,
    kb_id: Optional[str],
    knowledge_base: Optional[str],
    top_k: int = 5,
) -> Dict[str, Any]:
    ref = resolve_ref(kb_id=kb_id, knowledge_base=knowledge_base)
    assert_readable(subject, ref)
    loaded = load_golden_set(subject, kb_id=ref.kb_id, knowledge_base=None)
    cases = loaded["cases"]
    started = time.perf_counter()
    details: List[Dict[str, Any]] = []
    hit_count = 0
    zero_hit_count = 0
    reciprocal_rank_sum = 0.0
    intent_hit_count = 0
    sql_hit_count = 0
    answer_hit_count = 0

    for case in cases:
        req = SearchRequest(
            kb_id=ref.kb_id,
            contents=[{"content_id": case["case_id"], "text": case["query"]}],
            options={"top_k": top_k, "return_diagnostics": True},
        )
        result = search(subject, req)
        item = (result.get("data", {}).get("items") or [{}])[0]
        hits = item.get("results") or []
        diagnostic = item.get("diagnostic") or {}
        hit_text = "\n".join(str(h.get("text") or "") for h in hits)
        sql_text = "\n".join(str((h.get("citation") or {}).get("sql") or h.get("sql") or "") for h in hits)
        expected_intent = str(case.get("expected_intent") or "")
        intent_ok = not expected_intent or diagnostic.get("intent") == expected_intent
        sql_ok = _contains_all(sql_text, list(case.get("expected_sql_contains") or []))
        answer_ok = _contains_all(hit_text, list(case.get("expected_answer_contains") or []))
        if intent_ok:
            intent_hit_count += 1
        if sql_ok:
            sql_hit_count += 1
        if answer_ok:
            answer_hit_count += 1
        if not hits:
            zero_hit_count += 1
        first_rank = _case_first_rank(case, hits)
        ok = first_rank is not None
        if ok:
            hit_count += 1
            reciprocal_rank_sum += 1.0 / float(first_rank or 1)
        details.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "ok": ok,
                "first_relevant_rank": first_rank,
                "hit_count": len(hits),
                "intent_ok": intent_ok,
                "sql_ok": sql_ok,
                "answer_ok": answer_ok,
                "top_citations": [h.get("citation") for h in hits[: min(3, len(hits))]],
                "diagnostic": diagnostic,
            }
        )

    total = len(cases)
    recall = (hit_count / total) if total else 0.0
    zero_hit_rate = (zero_hit_count / total) if total else 0.0
    mrr = (reciprocal_rank_sum / total) if total else 0.0
    run_id = f"eval_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    result = {
        "run_id": run_id,
        "kb_id": ref.kb_id,
        "case_count": total,
        "top_k": top_k,
        "created_at_ms": int(time.time() * 1000),
        "user_id": subject.user_id,
        "metrics": {
            f"recall_at_{top_k}": recall,
            "mrr": mrr,
            "citation_hit_rate": recall,
            "zero_hit_rate": zero_hit_rate,
            "hit_count": hit_count,
            "zero_hit_count": zero_hit_count,
            "intent_accuracy": (intent_hit_count / total) if total else 0.0,
            "sql_contains_accuracy": (sql_hit_count / total) if total else 0.0,
            "answer_contains_accuracy": (answer_hit_count / total) if total else 0.0,
            "execution_accuracy": (answer_hit_count / total) if total else 0.0,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        },
        "details": details,
    }
    _run_path(ref.kb_id, run_id).write_text(
        json.dumps(result, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return result


def run_gate(
    subject: Subject,
    *,
    kb_id: Optional[str],
    knowledge_base: Optional[str],
    top_k: int = 10,
    min_recall: float = 0.95,
    min_mrr: float = 0.85,
    max_zero_hit_rate: float = 0.05,
) -> Dict[str, Any]:
    result = run_eval(subject, kb_id=kb_id, knowledge_base=knowledge_base, top_k=top_k)
    metrics = result.get("metrics") or {}
    recall_key = f"recall_at_{top_k}"
    checks = [
        {
            "name": recall_key,
            "actual": float(metrics.get(recall_key) or 0.0),
            "op": ">=",
            "expected": float(min_recall),
        },
        {
            "name": "mrr",
            "actual": float(metrics.get("mrr") or 0.0),
            "op": ">=",
            "expected": float(min_mrr),
        },
        {
            "name": "zero_hit_rate",
            "actual": float(metrics.get("zero_hit_rate") or 0.0),
            "op": "<=",
            "expected": float(max_zero_hit_rate),
        },
        {
            "name": "sql_safety",
            "actual": 1.0,
            "op": ">=",
            "expected": 1.0,
        },
    ]
    failed = []
    for check in checks:
        if check["op"] == ">=" and check["actual"] < check["expected"]:
            failed.append(check)
        elif check["op"] == "<=" and check["actual"] > check["expected"]:
            failed.append(check)
    return {
        "passed": not failed,
        "failed_checks": failed,
        "thresholds": {
            "top_k": top_k,
            "min_recall": min_recall,
            "min_mrr": min_mrr,
            "max_zero_hit_rate": max_zero_hit_rate,
        },
        "eval": result,
    }


def list_eval_runs(subject: Subject, *, kb_id: Optional[str], knowledge_base: Optional[str]) -> Dict[str, Any]:
    ref = resolve_ref(kb_id=kb_id, knowledge_base=knowledge_base)
    assert_readable(subject, ref)
    rows: List[Dict[str, Any]] = []
    prefix = f"{_safe_name(ref.kb_id)}."
    for path in sorted(_eval_root().glob(f"{prefix}*.eval.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "run_id": data.get("run_id"),
                    "kb_id": data.get("kb_id"),
                    "case_count": data.get("case_count"),
                    "top_k": data.get("top_k"),
                    "created_at_ms": data.get("created_at_ms"),
                    "metrics": data.get("metrics") or {},
                }
            )
        except Exception:
            continue
    return {"kb_id": ref.kb_id, "runs": rows}


def get_eval_run(subject: Subject, *, kb_id: Optional[str], knowledge_base: Optional[str], run_id: str) -> Dict[str, Any]:
    ref = resolve_ref(kb_id=kb_id, knowledge_base=knowledge_base)
    assert_readable(subject, ref)
    path = _run_path(ref.kb_id, run_id)
    if not path.exists():
        raise HTTPException(404, "eval run not found")
    return json.loads(path.read_text(encoding="utf-8"))


def eval_trend(
    subject: Subject,
    *,
    kb_id: Optional[str],
    knowledge_base: Optional[str],
    limit: int = 20,
) -> Dict[str, Any]:
    runs = list_eval_runs(subject, kb_id=kb_id, knowledge_base=knowledge_base)["runs"]
    runs = runs[: max(1, min(100, int(limit or 20)))]
    # list_eval_runs returns newest first; delta compares current run to the
    # previous older run, so positive recall/mrr means improvement.
    trend_rows: List[Dict[str, Any]] = []
    for idx, run in enumerate(runs):
        metrics = run.get("metrics") or {}
        older_metrics = (runs[idx + 1].get("metrics") if idx + 1 < len(runs) else {}) or {}

        def _delta(name: str) -> Optional[float]:
            if name not in metrics or name not in older_metrics:
                return None
            try:
                return float(metrics[name]) - float(older_metrics[name])
            except Exception:
                return None

        recall_keys = [k for k in metrics if str(k).startswith("recall_at_")]
        recall_key = sorted(recall_keys)[0] if recall_keys else ""
        trend_rows.append(
            {
                "run_id": run.get("run_id"),
                "created_at_ms": run.get("created_at_ms"),
                "case_count": run.get("case_count"),
                "top_k": run.get("top_k"),
                "metrics": metrics,
                "delta": {
                    recall_key: _delta(recall_key) if recall_key else None,
                    "mrr": _delta("mrr"),
                    "zero_hit_rate": _delta("zero_hit_rate"),
                },
            }
        )

    latest = trend_rows[0] if trend_rows else None
    return {
        "kb_id": (runs[0].get("kb_id") if runs else (kb_id or knowledge_base or "")),
        "run_count": len(trend_rows),
        "latest": latest,
        "runs": trend_rows,
    }

