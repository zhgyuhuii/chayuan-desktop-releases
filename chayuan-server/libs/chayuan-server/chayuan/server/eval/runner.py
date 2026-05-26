"""RAG 评估执行器。

输入：一份 golden JSON（结构兼容我们的 Text2SQL golden 文件 + 新增 RAG 格式）：

    {
        "cases": [
            {
                "id": "R001",
                "question": "...",
                "expected_answer_contains": ["...", "..."],  // 宽松判据
                "expected_sources_contains": ["a.pdf", "b.md"]
            }
        ]
    }

输出：
    {
        "summary": {
            "passed": int, "total": int, "hit_rate": float,
            "ragas": {"faithfulness": 0.87, "answer_correctness": 0.78, ...}
        },
        "per_case": [...]
    }

失败 fail-soft：某个 case 失败不中断，整体报告继续生成。

策略：
- 优先调 kb_chat 做 RAG 回答
- RAGAS 指标异步计算；未装则只算 hit rate
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.eval.runner")


def _call_kb_chat(query: str, kb_name: str, top_k: int = 5) -> Dict[str, Any]:
    """调 kb_chat 取得 answer + sources（非流式）。"""
    try:
        import asyncio
        from chayuan.server.chat.kb_chat import kb_chat
        # kb_chat 是 async；这里 loop 跑同步
        async def _run():
            resp = await kb_chat(
                query=query, mode="local_kb", kb_name=kb_name,
                top_k=int(top_k), score_threshold=2.0,
                history=[], stream=False, model="", temperature=0.3,
                max_tokens=None, prompt_name="default", return_direct=False,
                request=None,
            )
            # kb_chat 非流式下返回异步生成器的首条；简化处理
            if hasattr(resp, "__anext__"):
                return await resp.__anext__()
            return resp

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("_call_kb_chat 失败：%r", e)
        return {"error": f"{type(e).__name__}: {e}"}


def _call_retrieval_only(query: str, kb_name: str, top_k: int = 5) -> List[str]:
    """直接命中 hybrid_service 抓 contexts，供 RAGAS faithfulness 计算。

    走检索层而非 kb_chat 是为了（a）独立评估召回质量，不混入 LLM 风格；
    （b）不消耗 LLM quota。失败返回空列表。
    """
    try:
        from chayuan.server.file_rag.hybrid_service import hybrid_search_docs
        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
        kb_service = KBServiceFactory.get_service_by_name(kb_name)
        if kb_service is None:
            return []
        docs = hybrid_search_docs(
            kb_service=kb_service, query=query,
            top_k=int(top_k), score_threshold=2.0,
        )
        return [(d.page_content or "")[:2000] for d in (docs or [])]
    except Exception as e:  # noqa: BLE001
        logger.debug("_call_retrieval_only 失败：%r", e)
        return []


def _extract_answer_from_response(resp: Any) -> str:
    """尽量从 kb_chat 的各种返回形态中抽 answer 文本。"""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        # OpenAIChatOutput.model_dump 形态
        if "choices" in resp and resp["choices"]:
            try:
                return resp["choices"][0].get("message", {}).get("content") or ""
            except Exception:  # noqa: BLE001
                return ""
        if "content" in resp:
            return str(resp["content"] or "")
    # OpenAIChatOutput pydantic object
    try:
        d = resp.model_dump() if hasattr(resp, "model_dump") else {}
        if d.get("choices"):
            return d["choices"][0].get("message", {}).get("content") or ""
        return str(d.get("content") or "")
    except Exception:  # noqa: BLE001
        return ""


def _ragas_scores(cases: List[Dict[str, Any]]) -> Dict[str, float]:
    """若装了 ragas：算 faithfulness / answer_correctness 等；否则空 dict。"""
    try:
        from ragas import evaluate  # type: ignore
        from ragas.metrics import (  # type: ignore
            answer_correctness, context_precision, faithfulness,
        )
        from datasets import Dataset  # type: ignore
    except Exception as e:  # noqa: BLE001
        logger.info("RAGAS 未装，跳过 metric 计算：%r", e)
        return {}

    try:
        ds = Dataset.from_list([
            {
                "question": c.get("question") or "",
                "answer": c.get("actual_answer") or "",
                "contexts": c.get("actual_contexts") or [],
                "ground_truth": c.get("expected_answer") or (c.get("expected_answer_contains") or [""])[0],
            }
            for c in cases
        ])
        result = evaluate(ds, metrics=[faithfulness, answer_correctness, context_precision])
        scores = {}
        for k, v in (result._metadata if hasattr(result, "_metadata") else result).items():
            try:
                scores[k] = float(v)
            except Exception:  # noqa: BLE001
                pass
        return scores
    except Exception as e:  # noqa: BLE001
        logger.debug("RAGAS evaluate 失败：%r", e)
        return {}


def run_eval_against_golden(
    golden_path: str, kb_name: str, *, top_k: int = 5,
) -> Dict[str, Any]:
    """主入口。"""
    p = Path(golden_path)
    if not p.exists():
        return {"error": f"golden 文件不存在：{golden_path}"}
    try:
        golden = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"golden 文件解析失败：{e}"}
    cases = golden.get("cases") or []

    per_case: List[Dict[str, Any]] = []
    t0 = time.time()
    for case in cases:
        q = case.get("question") or ""
        cid = case.get("id") or ""
        resp = _call_kb_chat(q, kb_name, top_k=top_k)
        answer = _extract_answer_from_response(resp)
        contexts = _call_retrieval_only(q, kb_name, top_k=top_k)
        # 宽松命中：期望的关键词都在 answer 里
        expected_contains = case.get("expected_answer_contains") or []
        hit = all(kw in answer for kw in expected_contains) if expected_contains else None
        per_case.append({
            "id": cid,
            "question": q,
            "actual_answer": answer[:500],
            "actual_contexts": contexts,
            "expected_answer_contains": expected_contains,
            "expected_answer": case.get("expected_answer") or "",
            "hit": bool(hit) if hit is not None else None,
        })

    hit_count = sum(1 for c in per_case if c.get("hit") is True)
    total_with_hit_criteria = sum(1 for c in per_case if c.get("hit") is not None)
    hit_rate = float(hit_count) / max(1, total_with_hit_criteria)

    ragas = _ragas_scores(per_case)

    # P2-10：把分数挂到 Prometheus（最新值 Gauge）
    try:
        from chayuan.server.observability.metrics import record_rag_scores
        record_rag_scores(kb_name, ragas or {}, hit_rate=hit_rate)
    except Exception:  # noqa: BLE001
        logger.debug("record_rag_scores 失败", exc_info=True)

    return {
        "summary": {
            "passed": int(hit_count),
            "total": int(total_with_hit_criteria),
            "hit_rate": round(hit_rate, 4),
            "elapsed_sec": round(time.time() - t0, 2),
            "ragas": ragas,
        },
        "per_case": per_case,
    }


def run_eval_with_gate(
    golden_path: str, kb_name: str, *,
    top_k: int = 5,
    min_hit_rate: float = 0.8,
    min_faithfulness: float = 0.0,
    min_context_precision: float = 0.0,
    min_answer_correctness: float = 0.0,
) -> Dict[str, Any]:
    """CI eval gate：跑 ``run_eval_against_golden`` 并按阈值判定通过与否。

    返回：``{"passed": bool, "violations": [...], "report": {...}}``

    违规的 metric 会列在 ``violations`` 中（形如 ``"hit_rate 0.72 < 0.80"``）；
    faithfulness / context_precision / answer_correctness 默认阈值 0.0（不设门槛）。
    CI 中可通过设置非零阈值阻止回退。
    """
    report = run_eval_against_golden(golden_path, kb_name, top_k=top_k)
    if "error" in report:
        return {"passed": False, "violations": [report["error"]], "report": report}
    summary = report.get("summary") or {}
    violations: List[str] = []
    hr = float(summary.get("hit_rate") or 0.0)
    if hr + 1e-9 < float(min_hit_rate):
        violations.append(f"hit_rate {hr:.3f} < {min_hit_rate:.3f}")
    ragas = summary.get("ragas") or {}
    for key, th in (
        ("faithfulness", min_faithfulness),
        ("context_precision", min_context_precision),
        ("answer_correctness", min_answer_correctness),
    ):
        if float(th) <= 0.0:
            continue
        v = float(ragas.get(key) or 0.0)
        if v + 1e-9 < float(th):
            violations.append(f"{key} {v:.3f} < {th:.3f}")
    return {"passed": not violations, "violations": violations, "report": report}
