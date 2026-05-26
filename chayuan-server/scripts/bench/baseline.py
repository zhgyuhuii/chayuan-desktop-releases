"""Benchmark baseline schema + IO helpers.

Statement of intent
===================

任何"基准跑分"(KB recall / OCR 字段命中率 / 吞吐 p95 / GPU smoke 输出)
都写入同一个 schema 的 ``baseline.json``,后续在 PR 里打印 delta 时
有据可依。

不做的事:
* 不强行在 PR CI 里跑(GPU/真后端基准本就不该 block)
* 不引入新的实验跟踪(MLflow / W&B);本地 JSON 即可

文件位置::

    tests/perf/baseline.json
        {
          "schema_version": 1,
          "runs": [
            {
              "run_id": "...",
              "commit": "...",
              "dataset": "kb_golden_v1",
              "metric": {
                "recall@5": 0.87,
                "mrr": 0.61
              },
              "p50_ms": null,
              "p95_ms": null,
              "qps": null,
              "generated_at": 1714600000,
              "notes": ""
            }
          ]
        }
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.bench.baseline")

SCHEMA_VERSION = 1
DEFAULT_BASELINE_PATH = Path(__file__).resolve().parents[2] / "tests" / "perf" / "baseline.json"


@dataclass
class BenchmarkRun:
    run_id: str
    commit: str
    dataset: str
    metric: Dict[str, float] = field(default_factory=dict)
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    qps: Optional[float] = None
    generated_at: int = field(default_factory=lambda: int(time.time()))
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_baseline(path: Path = DEFAULT_BASELINE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "runs": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("load baseline failed (%s); starting fresh", e)
        return {"schema_version": SCHEMA_VERSION, "runs": []}


def append_run(run: BenchmarkRun, path: Path = DEFAULT_BASELINE_PATH) -> Dict[str, Any]:
    """Append a run; rotates oldest if > 100."""
    doc = load_baseline(path)
    doc.setdefault("schema_version", SCHEMA_VERSION)
    runs: List[Dict[str, Any]] = doc.setdefault("runs", [])
    runs.append(run.to_dict())
    if len(runs) > 100:
        runs[:] = runs[-100:]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return doc


def current_commit() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            return r.stdout.strip() or "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return os.environ.get("GIT_COMMIT") or "unknown"


def new_run(*, dataset: str, **kwargs: Any) -> BenchmarkRun:
    return BenchmarkRun(
        run_id=uuid.uuid4().hex,
        commit=current_commit(),
        dataset=dataset,
        **kwargs,
    )


def render_markdown(doc: Dict[str, Any], *, last_n: int = 10) -> str:
    runs = list(doc.get("runs") or [])[-last_n:]
    if not runs:
        return "_no benchmark runs yet._\n"
    lines = ["| run_id | commit | dataset | metric | p50 | p95 | qps | when |",
             "|---|---|---|---|---|---|---|---|"]
    for r in runs:
        metric = " · ".join(
            f"{k}={v:.3f}" if isinstance(v, (int, float)) else f"{k}={v}"
            for k, v in (r.get("metric") or {}).items()
        ) or "—"
        when = time.strftime("%Y-%m-%d", time.gmtime(int(r.get("generated_at") or 0)))
        lines.append(
            f"| `{r['run_id'][:8]}` | `{r.get('commit','-')}` | "
            f"{r.get('dataset','-')} | {metric} | "
            f"{r.get('p50_ms') or '—'} | {r.get('p95_ms') or '—'} | "
            f"{r.get('qps') or '—'} | {when} |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "BenchmarkRun",
    "DEFAULT_BASELINE_PATH",
    "SCHEMA_VERSION",
    "append_run",
    "current_commit",
    "load_baseline",
    "new_run",
    "render_markdown",
]
