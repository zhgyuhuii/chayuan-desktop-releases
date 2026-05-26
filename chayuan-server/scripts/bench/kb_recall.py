"""KB 准召率基准评测.

用法::

    python scripts/bench/kb_recall.py [--kb <name>] [--top-k 5] [--update-baseline]

输入:
    tests/data/kb_golden/v1.jsonl —— 30 条 (query, expected_doc_ids[]) golden set

指标:
    Recall@K  =  匹配 expected 的命中数 / expected_doc_ids 总数 (per query, 平均)
    MRR       =  1 / rank_of_first_hit (per query, 平均;无命中视作 0)

行为:
    1) 跑 hybrid_search_docs(query) 拿 top_k 文档 id
    2) 与 expected_doc_ids 取交集求 Recall
    3) 把结果写到 ``tests/perf/baseline.json`` (--update-baseline 时)
    4) 渲染 ``docs/benchmarks/kb_recall.md``

注意:
    * 这个脚本"只读";不会触发文档导入或重建索引
    * 对 KB 内容空(还没人导文档)的环境,Recall 会显示 0 — 这是预期
    * Golden set 的 expected_doc_ids 用 "稳定可读 ID" (如 ``mirror-source``);
      调用方需保证 KB 内的文档 metadata.id 与之吻合
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 让脚本可以从仓库根目录直接运行
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "chayuan-server"))
sys.path.insert(0, str(ROOT / "scripts"))

from bench.baseline import BenchmarkRun, append_run, current_commit, render_markdown  # noqa: E402

logger = logging.getLogger("kb_recall_bench")
logging.basicConfig(level=logging.INFO, format="%(message)s")


GOLDEN_PATH = ROOT / "tests" / "data" / "kb_golden" / "v1.jsonl"
REPORT_PATH = ROOT / "docs" / "benchmarks" / "kb_recall.md"


def load_golden(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        logger.error("golden set not found: %s", path)
        return []
    items: List[Dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            items.append(json.loads(ln))
        except json.JSONDecodeError as e:
            logger.warning("skip bad line: %s (%s)", ln[:60], e)
    return items


def run_query(kb_name: Optional[str], query: str, top_k: int) -> List[str]:
    """调 hybrid_search_docs 拿 top_k 个文档 id。

    生产环境用 ``hybrid_service.hybrid_search_docs``;此处保留对 fail-soft
    fallback (返回 [])。
    """
    try:
        from chayuan.server.file_rag.hybrid_service import hybrid_search_docs

        results = hybrid_search_docs(
            query=query,
            knowledge_base_name=kb_name or "default",
            top_k=top_k,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("hybrid_search_docs failed for %s: %s", query[:30], e)
        return []
    out: List[str] = []
    for r in results or []:
        # results 项可能是 dict 或 langchain Document
        if isinstance(r, dict):
            doc_id = (
                r.get("doc_id")
                or r.get("metadata", {}).get("doc_id")
                or r.get("metadata", {}).get("id")
                or r.get("id")
            )
        else:
            md = getattr(r, "metadata", {}) or {}
            doc_id = md.get("doc_id") or md.get("id")
        if doc_id:
            out.append(str(doc_id))
    return out


def evaluate(
    golden: List[Dict[str, Any]],
    *,
    kb_name: Optional[str],
    top_k: int,
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    recall_total = 0.0
    mrr_total = 0.0
    answered = 0
    per_query: List[Dict[str, Any]] = []
    for item in golden:
        expected = list(item.get("expected_doc_ids") or [])
        if not expected:
            continue
        retrieved = run_query(kb_name, item["query"], top_k)
        hit_set = set(expected) & set(retrieved)
        recall = len(hit_set) / len(expected)
        # MRR
        mrr = 0.0
        for rank, did in enumerate(retrieved, start=1):
            if did in expected:
                mrr = 1.0 / rank
                break
        recall_total += recall
        mrr_total += mrr
        answered += 1
        per_query.append({
            "id": item.get("id"),
            "query": item["query"],
            "recall": round(recall, 3),
            "mrr": round(mrr, 3),
            "retrieved": retrieved,
            "expected": expected,
        })
    if answered == 0:
        return {f"recall@{top_k}": 0.0, "mrr": 0.0, "n": 0}, per_query
    return (
        {
            f"recall@{top_k}": round(recall_total / answered, 3),
            "mrr": round(mrr_total / answered, 3),
            "n": float(answered),
        },
        per_query,
    )


def write_report(metric: Dict[str, float], per_query: List[Dict[str, Any]], top_k: int) -> None:
    """生成 docs/benchmarks/kb_recall.md。"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    commit = current_commit()
    lines = [
        "# KB 准召率基准",
        "",
        f"_最近一次跑分: {when} · commit `{commit}` · golden set v1 ({len(per_query)} 条)_",
        "",
        "## 当前指标",
        "",
        f"* **Recall@{top_k}** = {metric.get(f'recall@{top_k}', 0):.3f}",
        f"* **MRR**          = {metric.get('mrr', 0):.3f}",
        f"* **有效样本数**     = {int(metric.get('n', 0))}",
        "",
        "## Targets",
        "",
        "| 阶段 | Recall@5 目标 | MRR 目标 | 备注 |",
        "|---|---|---|---|",
        "| 当前基线 | ≥ 0.70 | ≥ 0.50 | 透明发布 |",
        "| v6.1     | ≥ 0.85 | ≥ 0.65 | HyDE + 重排参数调优 |",
        "| v7       | ≥ 0.95 | ≥ 0.80 | 引入跨段 reasoning + LLM 重排 |",
        "",
        "## Per-query 明细",
        "",
        "| id | recall | mrr | query |",
        "|---|---|---|---|",
    ]
    for r in per_query:
        q = (r.get("query") or "")[:50]
        lines.append(f"| {r['id']} | {r['recall']:.2f} | {r['mrr']:.2f} | {q} |")
    lines.append("")
    lines.append("## 如何复现")
    lines.append("```bash")
    lines.append("python scripts/bench/kb_recall.py --kb <你的 KB 名> --top-k 5 --update-baseline")
    lines.append("```")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb", default=None, help="KB 名;默认 'default'")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--update-baseline", action="store_true",
                        help="把结果写入 tests/perf/baseline.json")
    parser.add_argument("--golden", default=str(GOLDEN_PATH))
    args = parser.parse_args()

    golden = load_golden(Path(args.golden))
    if not golden:
        logger.error("no golden items; abort")
        return 1
    metric, per_query = evaluate(golden, kb_name=args.kb, top_k=args.top_k)
    logger.info("metric=%s", metric)

    if args.update_baseline:
        run = BenchmarkRun(
            run_id=current_commit() + "-kb-" + str(int(time.time())),
            commit=current_commit(),
            dataset=f"kb_golden_v1@top{args.top_k}",
            metric={k: float(v) for k, v in metric.items()},
            notes=f"kb={args.kb or 'default'}",
        )
        append_run(run)
        logger.info("baseline updated")

    write_report(metric, per_query, args.top_k)
    logger.info("report written: %s", REPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
