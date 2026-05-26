"""PR-9 Phase A+B 单元测试 — TaskManager / store / event_bus 端到端。

不打真实上游 — 用一个 fake connector 临时注册到 ``(Capability.T2I, "fake")``,
通过 ``pick_connector`` 命中。验证:
  - create_task 把行写入 DB,初态 pending
  - run_task 跑完后,行变 succeeded,files_json 有产物,text_out 拼回完整文本
  - 事件总线 replay buffer 在订阅时能完整回放
  - SSE 断开 (订阅协程 cancel) 不会取消任务本身,任务跑完后再订阅仍能拿到
    完整事件流
  - cancel(task_id) 设置 cancelled 标志,connector 能 check 并退出
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Dict

import pytest

from chayuan.server.db.base import Base, engine
from chayuan.server.modality.router.connectors.base import Connector, register
from chayuan.server.modality.router.protocol import (
    Capability,
    GenerateReq,
    Cancelled,
)
from chayuan.server.modality.router.sse_v5 import (
    data_part,
    file_part,
    text_delta,
    text_end,
    text_start,
)
from chayuan.server.modality.router.tasks import event_bus, manager, store


@pytest.fixture(autouse=True)
def _sqlite_session(monkeypatch):
    """在 in-memory SQLite 上跑 — 避免 CI 没有 Postgres / 跨用例数据污染。

    monkeypatch 同时改两处 SessionLocal:
      - chayuan.server.db.base.SessionLocal(给其它代码 ``from base import`` 用)
      - chayuan.server.modality.router.tasks.store.SessionLocal
        (store.py 已经把名字 import 进模块命名空间,直接改 module attr)
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from chayuan.server.db import models as _m  # noqa: F401  触发所有表注册
    from chayuan.server.modality.router.tasks import db_model  # noqa: F401

    # asyncio.to_thread 会在 worker 线程开新连接 — 默认 :memory: 是 per-connection,
    # 新连接看不到 main 线程 create_all 的表。StaticPool 强制共用一条连接,绕开。
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autocommit=False, autoflush=False)

    monkeypatch.setattr("chayuan.server.db.base.SessionLocal", Session)
    monkeypatch.setattr(
        "chayuan.server.modality.router.tasks.store.SessionLocal", Session,
    )
    # event_bus 是 process-wide singleton — 清理上一次用例残留通道
    event_bus._channels.clear()
    manager._running.clear()
    manager._cancellers.clear()
    yield Session


# ──────────────────────────────────────────────────────────────
# Fake connector — 模拟一个 1 秒跑完的 t2i,产 1 张图 + 一段说明文字
# 注册到 ``(T2I, fake)``,通过 platform_type='fake' 命中,不影响其它 connector
# ──────────────────────────────────────────────────────────────


@register(Capability.T2I, "_test_fake_t2i_fast")
class _FakeFastT2I(Connector):
    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        yield text_start("t0")
        yield text_delta("t0", "测试产物 ")
        yield text_delta("t0", "已生成")
        yield text_end("t0")
        yield data_part("task-progress", {"percent": 50, "message": "渲染中"})
        yield file_part(
            media_type="image/png",
            url="/v1/artifacts/abc.png",
            metadata={"prompt": req.prompt, "model": req.model, "width": 1024, "height": 1024},
        )
        yield data_part("task-progress", {"percent": 100, "message": "完成"})


@register(Capability.T2I, "_test_fake_t2i_cancellable")
class _FakeCancellableT2I(Connector):
    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        for i in range(20):
            if req.cancelled is not None and req.cancelled.is_set():
                yield data_part("task-progress", {"percent": int(5 * i), "message": "已收到 cancel"})
                return
            await asyncio.sleep(0.05)
            yield data_part("task-progress", {"percent": int(5 * i), "message": f"步骤 {i}"})


def _make_req(platform_type: str, model: str = "fake-t2i", prompt: str = "测试 prompt") -> GenerateReq:
    return GenerateReq(
        capability=Capability.T2I,
        model=model,
        platform_name="fake-platform",
        platform_type=platform_type,
        api_base="http://fake",
        api_key="sk-fake",
        prompt=prompt,
    )


# ──────────────────────────────────────────────────────────────
# 测试用例
# ──────────────────────────────────────────────────────────────


async def test_run_task_end_to_end():
    req = _make_req("_test_fake_t2i_fast")
    task_id = await manager.create_task(req)
    assert task_id and len(task_id) >= 16

    # 任务行已经落库,初态 pending
    row = await store.get_task(task_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["capability"] == "t2i"
    assert row["model"] == "fake-t2i"
    assert row["prompt"] == "测试 prompt"

    # 启动 + 订阅 — 完整跑完
    manager.ensure_running(task_id, req)
    events = []
    async for ev in manager.subscribe(task_id):
        events.append(ev)

    types = [e.get("type") for e in events]
    assert "start" in types
    assert "data-modality-meta" in types
    assert "text-start" in types
    assert "text-delta" in types
    assert "text-end" in types
    assert "file" in types
    assert "data-task-progress" in types
    assert "finish-step" in types
    assert "finish" in types

    # 落库:succeeded + files + text_out
    row = await store.get_task(task_id)
    assert row["status"] == "succeeded"
    assert row["progress_percent"] == 100
    assert len(row["files"]) == 1
    assert row["files"][0]["mediaType"] == "image/png"
    assert row["files"][0]["url"] == "/v1/artifacts/abc.png"
    assert row["text_out"] == "测试产物 已生成"
    assert row["finished_at"] is not None


async def test_subscribe_after_completion_replays_from_buffer():
    """grace 期内,任务已完成的订阅者应该立即拿到完整 buffer。"""
    req = _make_req("_test_fake_t2i_fast")
    task_id = await manager.create_task(req)
    task = manager.ensure_running(task_id, req)
    await task  # 等任务跑完

    # 任务通道在 grace 期内还活着 — 新订阅能拿到 buffer 完整回放
    events = [ev async for ev in manager.subscribe(task_id)]
    types = [e.get("type") for e in events]
    assert types[0] == "start"
    assert types[-1] == "finish"
    assert "file" in types


async def test_subscribe_after_channel_drop_replays_from_db():
    """通道被 grace cleanup 掉之后,subscribe 应该从 DB 合成回放。"""
    req = _make_req("_test_fake_t2i_fast")
    task_id = await manager.create_task(req)
    task = manager.ensure_running(task_id, req)
    await task

    # 强制 drop channel,模拟 grace 期已过
    event_bus.drop_channel(task_id)
    assert event_bus.get_channel(task_id) is None

    events = [ev async for ev in manager.subscribe(task_id)]
    types = [e.get("type") for e in events]
    # 从 DB 合成的事件流也要满足 start / file / finish 的结构契约
    assert types[0] == "start"
    assert types[-1] == "finish"
    assert "file" in types


async def test_subscribe_unknown_task_yields_error():
    events = [ev async for ev in manager.subscribe("nonexistent-task-id-abc")]
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "task_not_found" == events[0].get("code")


async def test_subscriber_disconnect_does_not_cancel_task():
    """关键场景 — SSE 客户端断开,后台任务必须继续跑到自然终态。"""
    req = _make_req("_test_fake_t2i_cancellable")
    task_id = await manager.create_task(req)
    bg_task = manager.ensure_running(task_id, req)

    # 订阅几条就退出,模拟客户端断连
    async def short_subscriber() -> None:
        async for ev in manager.subscribe(task_id):
            if ev.get("type") == "data-task-progress":
                return  # 拿一条进度就走

    await short_subscriber()
    # 任务仍在跑 — 等它自然完成
    await bg_task

    row = await store.get_task(task_id)
    # 没人 cancel → succeeded
    assert row["status"] == "succeeded"


async def test_cancel_sets_flag_and_connector_exits():
    req = _make_req("_test_fake_t2i_cancellable")
    task_id = await manager.create_task(req)
    bg_task = manager.ensure_running(task_id, req)

    # 等任务进入 running(至少跑过一个进度事件)
    async for ev in manager.subscribe(task_id):
        if ev.get("type") == "data-task-progress":
            break

    assert manager.cancel(task_id) is True
    await bg_task

    row = await store.get_task(task_id)
    assert row["status"] == "cancelled"


async def test_replay_buffer_preserves_event_order():
    """同步消费完一条任务,replay buffer 应严格按生产顺序保留。"""
    req = _make_req("_test_fake_t2i_fast")
    task_id = await manager.create_task(req)
    task = manager.ensure_running(task_id, req)
    await task

    ch = event_bus.get_channel(task_id)
    assert ch is not None
    # buffer 内事件顺序 = manager 发送顺序
    types = [e.get("type") for e in ch.buffered_events]
    # start 必在 file 之前;file 必在 finish 之前
    assert types.index("start") < types.index("file") < types.index("finish")
