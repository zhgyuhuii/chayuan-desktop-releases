"""ChatGraph（P1-6）行为测试。

覆盖：
- classify 节点：mode 自动识别（llm / kb / multi_source / vision）
- run_chat_sync 端到端：非流式；带治理（PII 脱敏 + 血缘）
- 流式事件协议：stage → token* → final → done

LLM 打桩：流式走 astream_events 时，我们打一个最小 stub llm 实现 astream_events。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List

import pytest

from chayuan.server.chat.graph.state import ChatMode, ChatRequest


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs,expected", [
    ({"query": "hi"}, "llm"),
    ({"query": "搜知识库", "kb_name": "samples"}, "kb"),
    ({"query": "多源", "source_ids": [1, 2]}, "multi_source"),
    ({"query": "图", "image_url": "http://x/1.png"}, "vision"),
    ({"query": "agent", "tools": ["calc"]}, "agent"),
])
def test_classify_auto_mode(kwargs, expected):
    from chayuan.server.chat.graph.nodes import node_classify
    req = ChatRequest(**kwargs)
    out = node_classify({"request": req})
    assert out["resolved_mode"] == expected


def test_classify_explicit_mode_wins():
    from chayuan.server.chat.graph.nodes import node_classify
    req = ChatRequest(query="q", mode=ChatMode.AGENT, kb_name="samples")
    out = node_classify({"request": req})
    assert out["resolved_mode"] == "agent"


# ---------------------------------------------------------------------------
# 非流式端到端（同步 generate + 治理节点）
# ---------------------------------------------------------------------------

def test_run_chat_sync_llm_mode_with_masking(ks_db, stub_llm):
    """纯 LLM 模式；LLM 产出含手机号 → finalize 节点应脱敏为 masked_answer。"""
    stub_llm.respond("请拨打 13800138000 联系我们")

    from chayuan.server.chat.graph import run_chat_sync

    req = ChatRequest(
        query="联系方式", stream=False, user_id=1, user_role="user",
        governance_enabled=True,
    )
    result = asyncio.get_event_loop().run_until_complete(run_chat_sync(req))
    assert result["code"] == 0
    answer = result["data"]["answer"]
    # strict 模式：手机号被全打码
    assert "13800138000" not in answer
    # PII 数量应 ≥ 1
    assert len(result["data"]["pii_entities"]) >= 1
    # 血缘 id 被写入
    assert result["data"]["lineage_id"] is not None


def test_run_chat_sync_quota_denied(ks_db, stub_llm, fake_redis, monkeypatch):
    """人工把配额用满 → 图第一道就拒；不应调用 LLM。"""
    import chayuan.server.governance.quota as q
    monkeypatch.setattr(q, "_REDIS_CLIENT", fake_redis, raising=False)
    monkeypatch.setattr(q, "_REDIS_CHECKED", True, raising=False)
    monkeypatch.setattr(q, "_get_redis", lambda: fake_redis)
    from chayuan.server.governance.policy import upsert_policy
    upsert_policy(scope="user:9", qps=-1, daily_token_budget=100, masking_level="loose")
    q.record_usage(user_id=9, tokens=200, mode="llm")  # 超 budget

    from chayuan.server.chat.graph import run_chat_sync
    req = ChatRequest(query="any", stream=False, user_id=9, user_role="user")
    result = asyncio.get_event_loop().run_until_complete(run_chat_sync(req))
    assert result["code"] == 0
    assert result["data"]["quota_rejected"] is True
    # LLM 不应被调用
    assert len(stub_llm.calls) == 0


def test_run_chat_sync_governance_off_skips_masking(ks_db, stub_llm):
    stub_llm.respond("电话 13912345678")
    from chayuan.server.chat.graph import run_chat_sync
    req = ChatRequest(query="x", stream=False, user_id=1, user_role="user",
                       governance_enabled=False)
    result = asyncio.get_event_loop().run_until_complete(run_chat_sync(req))
    # 未开启治理：不脱敏，不写血缘
    assert "13912345678" in result["data"]["answer"]
    assert result["data"]["lineage_id"] is None


# ---------------------------------------------------------------------------
# 流式：用一个打桩 llm.astream_events 测试 SSE 协议
# ---------------------------------------------------------------------------

class _ChunkLike:
    def __init__(self, content: str):
        self.content = content


class _FakeStreamingLLM:
    async def astream_events(self, messages, version="v2"):
        # 模拟若干 token chunk 再一个 end 事件
        for piece in ["你好", "，我是", "察元"]:
            yield {"event": "on_chat_model_stream", "data": {"chunk": _ChunkLike(piece)}}
            await asyncio.sleep(0)
        yield {
            "event": "on_chat_model_end",
            "data": {"output": _Out()},
        }


class _Out:
    def __init__(self):
        self.response_metadata = {"finish_reason": "stop"}
        self.usage_metadata = {
            "input_tokens": 5, "output_tokens": 3, "total_tokens": 8,
        }


async def _collect(gen):
    events = []
    async for e in gen:
        events.append({"event": e["event"], "data": json.loads(e["data"])})
    return events


def test_run_chat_stream_sse_protocol(ks_db, monkeypatch):
    from chayuan.server.chat.graph import run_chat_stream

    import chayuan.server.utils as U
    monkeypatch.setattr(U, "get_ChatOpenAI", lambda *a, **k: _FakeStreamingLLM(),
                         raising=True)

    req = ChatRequest(query="hi", stream=True, user_id=1, user_role="admin",
                       governance_enabled=True)
    events = asyncio.get_event_loop().run_until_complete(_collect(run_chat_stream(req)))

    names = [e["event"] for e in events]
    # 必要事件集
    assert "stage" in names
    assert "token" in names
    assert "llm_end" in names
    assert "final" in names
    assert "done" in names

    # token 合并后应该是 "你好，我是察元"
    merged = "".join(
        e["data"]["delta"] for e in events
        if e["event"] == "token" and "delta" in e["data"]
    )
    assert merged == "你好，我是察元"

    # finish_reason 正确透传
    end = next(e for e in events if e["event"] == "llm_end")
    assert end["data"]["finish_reason"] == "stop"
    assert end["data"]["usage"]["total_tokens"] == 8
