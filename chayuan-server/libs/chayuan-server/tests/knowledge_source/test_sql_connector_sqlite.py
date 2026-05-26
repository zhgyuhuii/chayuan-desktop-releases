"""SqlConnector 契约测试（方言：SQLite，免驱动）。

覆盖：
- test_connection 正向 / 反向
- introspect 能拿到表 / 列 / 采样
- search → Text2SQL StateGraph 全链路，LLM 打桩为返回预置 JSON
  * 正常路径：生成 SQL → 校验 → 执行 → 合成 markdown
  * 安全拦截：生成了 DML → validation_error
  * 执行错误重试：第一次 SQL 有语法错 → revise → 第二次正确

本测试不依赖 Redis / langgraph（未装时走 fallback 线性执行），但会触发数据库、
SchemaSnapshot、RAG 训练语料补种等完整链路。
"""
from __future__ import annotations

import json

import pytest

from chayuan.server.knowledge_source.base import ConnectionSpec


@pytest.mark.requires("sqlalchemy")
def test_test_connection_ok(sqlite_source_factory):
    sid, _, spec = sqlite_source_factory("ks_sqlite_ok")
    from chayuan.server.knowledge_source.sql.connector import SqlConnector
    c = SqlConnector(spec=spec, source_id=sid)
    ok, msg = c.test_connection()
    assert ok, msg
    assert "sqlite" in msg


@pytest.mark.requires("sqlalchemy")
def test_test_connection_fails_on_bad_path(tmp_path):
    """不存在的 sqlite 库 → SQLAlchemy 会报 OperationalError。"""
    from chayuan.server.knowledge_source.sql.connector import SqlConnector
    spec = ConnectionSpec(dialect="sqlite", database=str(tmp_path / "no_such_dir" / "x.db"))
    c = SqlConnector(spec=spec, source_id=0)
    ok, msg = c.test_connection()
    # Windows / Linux 下错误表述不同，但 ok 必须是 False
    assert ok is False
    assert msg


@pytest.mark.requires("sqlalchemy")
def test_introspect_returns_tables_and_samples(sqlite_source_factory):
    sid, _, spec = sqlite_source_factory("ks_sqlite_intr")
    from chayuan.server.knowledge_source.sql.connector import SqlConnector
    snap = SqlConnector(spec=spec, source_id=sid).introspect(sample_rows=2)
    names = [t.name for t in snap.tables]
    assert "products" in names
    assert "orders" in names

    products = next(t for t in snap.tables if t.name == "products")
    col_names = [c.name for c in products.columns]
    assert "id" in col_names and "name" in col_names and "price" in col_names
    # id 是 PK
    assert any(c.primary_key and c.name == "id" for c in products.columns)
    # 至少有采样
    assert products.sample_rows, "should have at least one sample row"


@pytest.mark.requires("sqlalchemy")
def test_search_end_to_end_happy_path(sqlite_source_factory, stub_llm, stub_embeddings, monkeypatch):
    """生成合法 SELECT → 执行 → 命中。"""
    sid, db_path, spec = sqlite_source_factory("ks_sqlite_e2e_ok")

    # LLM 固定返回 schema_link → generate 两次都 OK
    # 新版 structured_llm 把 schema / 规则写在 system prompt 里，user prompt 只含问题+上下文。
    # 所以 matcher 同时看 system+user 的合并文本判断分支。
    def canned(messages):
        joined = "\n".join(
            (m.get("content") if isinstance(m, dict) else "") or ""
            for m in (messages or [])
        )
        if "Schema Linker" in joined or "挑选最可能" in joined:
            return '{"tables": ["products"]}'
        if "资深工程师" in joined or "SQL 生成" in joined or "生成的 SQL" in joined or "请严格遵守" in joined:
            return '{"sql": "SELECT name, price FROM products ORDER BY price DESC", "reason": "按价格降序列出商品"}'
        if "分诊员" in joined or "can_answer" in joined:
            return '{"can_answer": true, "reason": "ok"}'
        return '{"sql": "SELECT 1", "reason": "fallback"}'

    stub_llm.respond(canned)

    import asyncio
    from chayuan.server.knowledge_source.sql.connector import SqlConnector
    from chayuan.server.knowledge_source.types import NLQuery

    c = SqlConnector(spec=spec, source_id=sid)
    chunks = asyncio.get_event_loop().run_until_complete(
        c.search(NLQuery(query="最贵的商品", top_k=5))
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    # 命中 success 路径：没有 meta.error
    assert not chunk.citation.meta.get("error"), chunk.content
    # 结果 markdown 里应该含有 MacBook Pro（最贵的）
    assert "MacBook Pro" in chunk.content
    # 生成的 SQL 被保留到 citation
    assert chunk.citation.generated_query.strip().upper().startswith("SELECT")


@pytest.mark.requires("sqlalchemy")
def test_search_rejects_generated_dml(sqlite_source_factory, stub_llm, stub_embeddings):
    """LLM 生成了 DELETE → sqlglot 静态校验必须拦住。"""
    sid, _, spec = sqlite_source_factory("ks_sqlite_dml")

    def canned(messages):
        joined = "\n".join(
            (m.get("content") if isinstance(m, dict) else "") or ""
            for m in (messages or [])
        )
        if "Schema Linker" in joined:
            return '{"tables": ["products"]}'
        if "分诊员" in joined:
            return '{"can_answer": true, "reason": "ok"}'
        # generate 时强行塞一个 DELETE
        return '{"sql": "DELETE FROM products", "reason": "bad llm"}'

    stub_llm.respond(canned)

    import asyncio
    from chayuan.server.knowledge_source.sql.connector import SqlConnector
    from chayuan.server.knowledge_source.types import NLQuery

    c = SqlConnector(spec=spec, source_id=sid)
    chunks = asyncio.get_event_loop().run_until_complete(
        c.search(NLQuery(query="删除所有商品", top_k=5))
    )
    chunk = chunks[0]
    assert chunk.citation.meta.get("error"), "应当命中 validation_failed"
    # 不应当真的执行，商品表必须完整
    import sqlite3
    cx = sqlite3.connect(spec.database)
    cnt = cx.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    cx.close()
    assert cnt == 3, "DML 必须被拦截，不应真的删除"


@pytest.mark.requires("sqlalchemy")
def test_search_self_correct_on_exec_error(sqlite_source_factory, stub_llm, stub_embeddings):
    """第一次生成有语法错误 → LangGraph 进 revise → 第二次修正后执行成功。"""
    sid, _, spec = sqlite_source_factory("ks_sqlite_revise")

    call_counter = {"generate": 0}

    def canned(messages):
        joined = "\n".join(
            (m.get("content") if isinstance(m, dict) else "") or ""
            for m in (messages or [])
        )
        if "Schema Linker" in joined:
            return '{"tables": ["products"]}'
        if "分诊员" in joined:
            return '{"can_answer": true, "reason": "ok"}'
        # 执行失败后的 revise prompt 会带 "数据库错误" 字样
        if "数据库错误" in joined or "修正 SQL" in joined:
            return '{"sql": "SELECT name FROM products", "reason": "把 nonexistent_column 改为 name"}'
        # 第一次 generate
        call_counter["generate"] += 1
        return '{"sql": "SELECT nonexistent_column FROM products", "reason": "第一次错误"}'

    stub_llm.respond(canned)

    import asyncio
    from chayuan.server.knowledge_source.sql.connector import SqlConnector
    from chayuan.server.knowledge_source.types import NLQuery

    c = SqlConnector(spec=spec, source_id=sid)
    chunks = asyncio.get_event_loop().run_until_complete(
        c.search(NLQuery(query="列出商品名称", top_k=5))
    )
    chunk = chunks[0]
    # 最终必须成功
    assert not chunk.citation.meta.get("error"), chunk.content
    # retry_count ≥ 1
    assert int(chunk.citation.meta.get("retry_count") or 0) >= 1
    # 最终 SQL 是修正后的
    assert "name" in chunk.citation.generated_query
