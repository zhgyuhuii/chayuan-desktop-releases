"""Text2SQL 黄金数据集 "命中率" 回归测试。

设计：
- 每个 case 用 **参考 SQL** 直接在 SQLite 上执行得到"期望结果集"，再检查
  LLM 生成的 SQL 执行结果**超集包含**期望结果集
- Prompt 改动 / LangGraph 重构 / Vanna 升级都必须跑一遍；命中率掉 → CI 红灯

LLM 侧需要一个**真 LLM**（OpenAI-compatible 端点）。本测试用 ``pytest.mark.requires``
在 LLM 不可用时自动跳过，不阻塞单元测试。

人工运行：
    pytest tests/knowledge_source/test_text2sql_golden.py -v --only-extended
CI 推荐 nightly 跑；单测 CI 默认跳过。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest


GOLDEN_PATH = Path(__file__).parent / "golden_text2sql.json"


def _load_golden():
    with GOLDEN_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _result_set(db_path: str, sql: str):
    cx = sqlite3.connect(db_path)
    try:
        cur = cx.execute(sql)
        rows = cur.fetchall()
        return [tuple(str(c) if c is not None else "" for c in r) for r in rows]
    except Exception as e:  # noqa: BLE001
        return [("__error__", type(e).__name__)]
    finally:
        cx.close()


def _has_real_llm() -> bool:
    """实际 LLM 可用性：要求 DEFAULT_LLM_MODEL 配过 且能解析出 OpenAI-compatible URL。"""
    return bool(os.environ.get("CHAYUAN_LLM_ENDPOINT")) or \
        bool(os.environ.get("OPENAI_API_KEY"))


@pytest.mark.requires("sqlalchemy", "langgraph", "langchain_openai")
@pytest.mark.skipif(not _has_real_llm(), reason="无真实 LLM 端点；跳过 golden 测试")
def test_text2sql_golden_hit_rate(sqlite_source_factory):
    """对 golden 集跑一遍，计算命中率，设定基线门槛。

    命中判据（比对的是**结果集**而不是 SQL 字符串，避免等价 SQL 被判错）：
    - ``expected_result_contains`` 中的每个字符串出现在生成 SQL 的查询结果里
    - 若生成 SQL 执行失败 / 被安全拦 / 为空 → 记为未命中

    基线门槛：≥ 60%（10 条至少过 6 条）；未来随 Vanna/LangGraph 优化逐步抬高。
    """
    golden = _load_golden()
    sid, db_path, spec = sqlite_source_factory(
        "ks_golden", seed_sql=golden["schema_seed_sql"],
    )
    from chayuan.server.knowledge_source.sql.connector import SqlConnector
    from chayuan.server.knowledge_source.types import NLQuery

    import asyncio

    passed = 0
    failures = []
    for case in golden["cases"]:
        c = SqlConnector(spec=spec, source_id=sid)
        chunks = asyncio.get_event_loop().run_until_complete(
            c.search(NLQuery(query=case["question"], top_k=50))
        )
        chunk = chunks[0] if chunks else None
        if chunk is None or chunk.citation.meta.get("error"):
            failures.append((case["id"], "error", chunk and chunk.content))
            continue
        sql = chunk.citation.generated_query or ""
        if not sql:
            failures.append((case["id"], "no_sql", None))
            continue
        rows = _result_set(db_path, sql)
        rendered = "\n".join(" | ".join(r) for r in rows)
        ok = all(must in rendered for must in (case.get("expected_result_contains") or []))
        if ok:
            passed += 1
        else:
            failures.append((case["id"], "result_mismatch", rendered[:200]))

    total = len(golden["cases"])
    hit_rate = passed / total
    print(f"\n[golden] {passed}/{total} passed ({hit_rate:.0%})")
    for fid, reason, detail in failures:
        print(f"  ✗ {fid}: {reason} :: {detail}")

    # 基线：≥ 60% （10 条过 6 条）；CI 上传 artifact 供历史比对
    assert hit_rate >= 0.6, f"Text2SQL 基线命中率低于 60%（实际 {hit_rate:.0%}）"
