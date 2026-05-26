"""多源并行编排 Orchestrator 的行为测试。

验证：
- 一个源失败 / 超时不影响其他源的结果
- SSE 事件序列完整（stage → source_started → source_chunks/failed → aggregating → final）
- 成功的 chunks 会进入 final.aggregated
- 路由器（use_router）在源数 ≥4 时触发；< 4 直接走全量

Connector 全部打桩为 FakeConnector，不走真实数据库。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from chayuan.server.knowledge_source.base import BaseConnector, ConnectionSpec
from chayuan.server.knowledge_source.types import (
    Citation, NLQuery, RetrievalChunk, SchemaSnapshot,
)


# ---------------------------------------------------------------------------
# FakeConnector：用参数化行为模拟快 / 慢 / 异常源
# ---------------------------------------------------------------------------

class FakeConnector(BaseConnector):
    dialects = ("fake",)
    source_kind = "sql"

    def __init__(self, spec, source_id=0, delay=0.0, fail=False, hang=False, content="ok"):
        super().__init__(spec, source_id)
        self.delay = delay
        self.fail = fail
        self.hang = hang
        self.content = content

    def test_connection(self):
        return True, "ok"

    def introspect(self, sample_rows=3):
        return SchemaSnapshot(
            source_id=self.source_id, source_kind="sql", dialect="fake", tables=[],
        )

    async def search(self, query: NLQuery):
        if self.hang:
            await asyncio.sleep(99)
            return []
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"source {self.source_id} boom")
        return [RetrievalChunk(
            content=f"{self.content} for: {query.query}",
            citation=Citation(
                title=f"fake#{self.source_id}",
                source_id=self.source_id,
                source_kind="sql",
                generated_query=f"SELECT * FROM t{self.source_id}",
            ),
            score=1.0,
            source_id=self.source_id,
            source_kind="sql",
        )]


async def _collect_events(gen):
    events = []
    async for e in gen:
        payload = json.loads(e["data"])
        events.append({"event": e["event"], "data": payload})
    return events


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# 核心场景
# ---------------------------------------------------------------------------

def test_multi_source_happy_path(monkeypatch):
    """3 个源，全部成功，事件序列完整。"""
    from chayuan.server.knowledge_source import orchestrator
    fakes = {
        1: FakeConnector(ConnectionSpec(dialect="fake"), 1, delay=0.05),
        2: FakeConnector(ConnectionSpec(dialect="fake"), 2, delay=0.02),
        3: FakeConnector(ConnectionSpec(dialect="fake"), 3, delay=0.01),
    }
    monkeypatch.setattr(orchestrator, "_connector_for_source",
                         lambda src: fakes[int(src["id"])])

    sources = [{"id": i, "kind": "sql", "name": f"s{i}", "description": f"svc#{i}"}
               for i in (1, 2, 3)]
    events = _run(_collect_events(orchestrator.multi_search_stream(
        query="hi", sources=sources, top_k=3, per_source_timeout=5.0,
        use_router=False,
    )))
    names = [e["event"] for e in events]
    assert "planning" not in names  # planning 是 stage.data 里的 stage 字段
    # 必要事件都出现
    assert any(e["event"] == "stage" and e["data"].get("stage") == "planning" for e in events)
    assert sum(1 for e in events if e["event"] == "source_started") == 3
    assert sum(1 for e in events if e["event"] == "source_chunks") == 3
    final = next(e for e in events if e["event"] == "final")
    assert len(final["data"]["aggregated"]) == 3


def test_one_source_fails_others_survive(monkeypatch):
    from chayuan.server.knowledge_source import orchestrator
    fakes = {
        1: FakeConnector(ConnectionSpec(dialect="fake"), 1),
        2: FakeConnector(ConnectionSpec(dialect="fake"), 2, fail=True),
        3: FakeConnector(ConnectionSpec(dialect="fake"), 3),
    }
    monkeypatch.setattr(orchestrator, "_connector_for_source",
                         lambda src: fakes[int(src["id"])])

    sources = [{"id": i, "kind": "sql", "name": f"s{i}"} for i in (1, 2, 3)]
    events = _run(_collect_events(orchestrator.multi_search_stream(
        query="hi", sources=sources, top_k=3, per_source_timeout=3.0, use_router=False,
    )))
    # 2 成功 + 1 失败
    assert sum(1 for e in events if e["event"] == "source_chunks") == 2
    assert sum(1 for e in events if e["event"] == "source_failed") == 1
    final = next(e for e in events if e["event"] == "final")
    # aggregated 只含成功源的 chunks
    assert len(final["data"]["aggregated"]) == 2
    ok_ids = {c["source_id"] for c in final["data"]["aggregated"]}
    assert ok_ids == {1, 3}


def test_source_timeout_does_not_block_others(monkeypatch):
    from chayuan.server.knowledge_source import orchestrator
    fakes = {
        1: FakeConnector(ConnectionSpec(dialect="fake"), 1),
        2: FakeConnector(ConnectionSpec(dialect="fake"), 2, hang=True),  # 一直挂
        3: FakeConnector(ConnectionSpec(dialect="fake"), 3),
    }
    monkeypatch.setattr(orchestrator, "_connector_for_source",
                         lambda src: fakes[int(src["id"])])

    sources = [{"id": i, "kind": "sql", "name": f"s{i}"} for i in (1, 2, 3)]
    events = _run(_collect_events(orchestrator.multi_search_stream(
        query="hi", sources=sources, top_k=3, per_source_timeout=0.2, use_router=False,
    )))
    # 超时归到 source_failed
    failed = [e for e in events if e["event"] == "source_failed"]
    assert len(failed) == 1 and "超时" in failed[0]["data"]["error"]
    final = next(e for e in events if e["event"] == "final")
    assert len(final["data"]["aggregated"]) == 2


def test_router_disabled_when_too_few_sources(monkeypatch):
    """源 < 4 时 route_sources 应直接返回全量，不调 LLM。"""
    from chayuan.server.knowledge_source import router
    called = {"n": 0}

    def _spy(*a, **kw):
        called["n"] += 1
        return ([], {})  # 如果被调用就返回错的结构（确保被调用会失败）

    # 只要走进 LLM 调用就代表路由生效；源数 <4 时不应进入 LLM
    import chayuan.server.utils as U
    monkeypatch.setattr(U, "get_ChatOpenAI", _spy, raising=True)

    out = router.route_sources(
        query="任意问题",
        sources=[{"id": 1, "kind": "sql"}, {"id": 2, "kind": "mongo"}, {"id": 3, "kind": "es"}],
        enabled_threshold=4,
    )
    assert len(out) == 3
    assert called["n"] == 0, "源数 < 阈值不应触发 LLM"


def test_router_fail_open(monkeypatch):
    """LLM 异常时 route_sources 必须 fail-open 返回原列表。"""
    from chayuan.server.knowledge_source import router

    class _Boom:
        def invoke(self, *a, **k):
            raise RuntimeError("llm down")

    import chayuan.server.utils as U
    monkeypatch.setattr(U, "get_ChatOpenAI", lambda *a, **k: _Boom(), raising=True)

    out = router.route_sources(
        query="x",
        sources=[{"id": i, "kind": "sql"} for i in range(5)],
        enabled_threshold=4,
    )
    assert len(out) == 5, "LLM 异常必须 fail-open"
