"""三层缓存行为测试（P0-2）。

场景：
- Redis 未配置 / 连不上 → 全部 get 返回 None、set 静默（fail-open）
- Redis 就绪 → schema / template / result 三层独立工作
- 时间敏感词 → template / result 层自动 bypass（但 schema 层正常走 redis）
- 结果缓存按 source_id / user_id 隔离
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 1. 时间敏感词检测
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q,expected", [
    ("今天的订单数", True),
    ("昨天的销售额", True),
    ("show me orders from last 7 days", True),
    ("LATEST sales report", True),
    ("本月销量前十", True),
    ("最近 24 小时登录", True),
    ("北京销售量", False),
    ("列出所有商品", False),
    ("用户 id 为 1 的订单", False),
])
def test_time_sensitive_detection(q, expected):
    from chayuan.server.knowledge_source.cache import is_time_sensitive
    assert is_time_sensitive(q) is expected


# ---------------------------------------------------------------------------
# 2. Redis 不可用：fail-open
# ---------------------------------------------------------------------------

def test_fail_open_without_redis(monkeypatch):
    import chayuan.server.knowledge_source.cache as c
    monkeypatch.setattr(c, "_get_redis", lambda: None, raising=True)

    # set 不抛；get 返回 None
    c.schema_cache_set(1, {"tables": [{"name": "x"}]})
    assert c.schema_cache_get(1) is None

    c.template_cache_set(1, "q", {"tables": []}, payload={"sql": "SELECT 1"})
    assert c.template_cache_get(1, "q", {"tables": []}) is None

    c.result_cache_set("sql", 1, "q", payload={"x": 1})
    assert c.result_cache_get("sql", 1, "q") is None


# ---------------------------------------------------------------------------
# 3. Redis 就绪：schema 层独立工作
# ---------------------------------------------------------------------------

def test_schema_cache_roundtrip(fake_redis):
    from chayuan.server.knowledge_source.cache import (
        schema_cache_get, schema_cache_set, schema_cache_invalidate,
    )
    src = 42
    payload = {"tables": [{"name": "users"}], "dialect": "mysql"}
    schema_cache_set(src, payload, ttl=60)
    got = schema_cache_get(src)
    assert got and got["dialect"] == "mysql"
    schema_cache_invalidate(src)
    assert schema_cache_get(src) is None


# ---------------------------------------------------------------------------
# 4. Template cache：命中 + 时间敏感 bypass
# ---------------------------------------------------------------------------

def test_template_cache_hit_and_miss(fake_redis):
    from chayuan.server.knowledge_source.cache import (
        template_cache_get, template_cache_set,
    )
    schema = {"tables": [{"name": "products", "columns": [{"name": "id"}]}]}
    src, q = 1, "列出所有商品"
    assert template_cache_get(src, q, schema) is None
    template_cache_set(src, q, schema, payload={"sql": "SELECT * FROM products"})
    hit = template_cache_get(src, q, schema)
    assert hit and hit["sql"] == "SELECT * FROM products"


def test_template_cache_bypasses_time_sensitive(fake_redis):
    from chayuan.server.knowledge_source.cache import (
        template_cache_get, template_cache_set,
    )
    schema = {"tables": []}
    # 时间敏感 query → set 被静默，get 也 bypass
    template_cache_set(1, "今天的订单", schema, payload={"sql": "SELECT ..."})
    assert template_cache_get(1, "今天的订单", schema) is None


def test_template_cache_schema_change_miss(fake_redis):
    """schema 变了 → key 自然变 → miss。"""
    from chayuan.server.knowledge_source.cache import (
        template_cache_get, template_cache_set,
    )
    template_cache_set(1, "q", {"tables": [{"name": "a"}]}, payload={"sql": "x"})
    assert template_cache_get(1, "q", {"tables": [{"name": "b"}]}) is None  # 不同 schema
    assert template_cache_get(1, "q", {"tables": [{"name": "a"}]}) is not None


# ---------------------------------------------------------------------------
# 5. Result cache：按 source + user_id 隔离
# ---------------------------------------------------------------------------

def test_result_cache_user_isolation(fake_redis):
    from chayuan.server.knowledge_source.cache import (
        result_cache_get, result_cache_set,
    )
    result_cache_set("sql", 1, "q", payload={"content": "A"}, user_id=100)
    result_cache_set("sql", 1, "q", payload={"content": "B"}, user_id=200)

    a = result_cache_get("sql", 1, "q", user_id=100)
    b = result_cache_get("sql", 1, "q", user_id=200)
    nouser = result_cache_get("sql", 1, "q", user_id=None)

    assert a and a["content"] == "A"
    assert b and b["content"] == "B"
    # 不带 user_id 的 key 与两者都不同
    assert nouser is None


def test_result_cache_invalidate(fake_redis):
    from chayuan.server.knowledge_source.cache import (
        result_cache_get, result_cache_set,
        invalidate_result_cache_by_source,
    )
    result_cache_set("sql", 5, "q1", payload={"x": 1})
    result_cache_set("sql", 5, "q2", payload={"x": 2})
    result_cache_set("sql", 6, "q1", payload={"x": 3})
    invalidate_result_cache_by_source(5)
    assert result_cache_get("sql", 5, "q1") is None
    assert result_cache_get("sql", 5, "q2") is None
    # 别的 source 不受影响
    assert result_cache_get("sql", 6, "q1") is not None


# ---------------------------------------------------------------------------
# 6. 端到端：Connector.search 命中 result cache 应跳过 DB
# ---------------------------------------------------------------------------

@pytest.mark.requires("sqlalchemy")
def test_result_cache_short_circuits_connector(sqlite_source_factory, fake_redis,
                                                 stub_llm, stub_embeddings):
    """第一次走完整流程 → 第二次同问题应直接命中缓存。"""
    sid, _, spec = sqlite_source_factory("ks_sqlite_cache_sc")
    def _canned(msgs):
        joined = "\n".join(
            (m.get("content") if isinstance(m, dict) else "") or ""
            for m in (msgs or [])
        )
        if "Schema Linker" in joined:
            return '{"tables": ["products"]}'
        if "分诊员" in joined:
            return '{"can_answer": true, "reason": "ok"}'
        return '{"sql": "SELECT name FROM products", "reason": "ok"}'
    stub_llm.respond(_canned)

    import asyncio
    from chayuan.server.knowledge_source.sql.connector import SqlConnector
    from chayuan.server.knowledge_source.types import NLQuery

    c = SqlConnector(spec=spec, source_id=sid)
    q = "列出商品名"
    r1 = asyncio.get_event_loop().run_until_complete(c.search(NLQuery(query=q, top_k=5)))
    assert not r1[0].citation.meta.get("error")
    # 第一次调用后 result cache 里应该有条目
    calls_before = len(stub_llm.calls)

    # 第二次：用同一句 query
    r2 = asyncio.get_event_loop().run_until_complete(
        SqlConnector(spec=spec, source_id=sid).search(NLQuery(query=q, top_k=5))
    )
    # 必须命中缓存：from_cache=True
    assert r2[0].citation.meta.get("from_cache") is True
    # LLM 不应该被再次调用
    assert len(stub_llm.calls) == calls_before, "命中缓存应跳过 LLM"
