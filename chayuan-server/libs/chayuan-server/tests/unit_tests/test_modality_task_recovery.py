"""PR-9 E:进程重启恢复测试。

覆盖三条路径:
  - 有 upstream_task_id 的 t2v 任务 → connector.resume() 被调用,继续轮询
  - 没有 upstream_task_id 的(sync 类型 / 上游还没拿 id 就被杀)→ 标 orphaned
  - 找不到 platform 元信息 → 标 orphaned_platform_missing

策略:用 fake 连接器 + monkeypatch get_model_info,完全离线。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from chayuan.server.db.base import Base
from chayuan.server.modality.router.connectors.base import Connector, register
from chayuan.server.modality.router.protocol import Capability, GenerateReq
from chayuan.server.modality.router.sse_v5 import (
    data_part,
    error_event,
    file_part,
    text_end,
    text_start,
)
from chayuan.server.modality.router.tasks import event_bus, manager, store


# fake t2v connector — generate() 永远不该在重启路径上被调用;
# 重启走 resume() 模拟"接着轮询然后成功"
class _ResumableFakeT2V(Connector):
    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        raise AssertionError(
            "recovery 路径不该调 generate;只能调 resume",
        )

    async def resume(self, req: GenerateReq, upstream_task_id: str) -> AsyncIterator[Dict]:
        yield text_start("t0")
        yield text_end("t0")
        yield data_part(
            "task-progress",
            {"percent": 50, "message": "继续轮询", "task_id": upstream_task_id},
        )
        yield file_part(
            media_type="video/mp4",
            url=f"/v1/artifacts/{upstream_task_id}.mp4",
            metadata={"task_id": upstream_task_id},
        )
        yield data_part("task-progress", {"percent": 100, "message": "完成"})


@pytest.fixture(autouse=True)
def _sqlite_session(monkeypatch):
    from chayuan.server.db import models as _m  # noqa: F401
    from chayuan.server.modality.router.tasks import db_model  # noqa: F401
    from chayuan.server.modality.router.connectors.base import _CONNECTORS

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

    # fake get_model_info — 测试唯一的外部 IO 依赖
    def _fake_get_model_info(model_name=None, platform_name=None):
        if model_name == "wanx-t2v-fake-resumable":
            return {
                "platform_name": platform_name or "fake-platform",
                "platform_type": "dashscope",  # 走 vendor 短路命中 fake connector
                "api_base_url": "http://fake",
                "api_key": "sk-fake",
            }
        if model_name == "lost-model":
            return None
        return None

    # manager 函数里 `from chayuan.server.utils import get_model_info` 是延迟
    # 局部 import,monkeypatch 模块属性会被覆盖。直接 patch 源头。
    import chayuan.server.utils as utils_mod
    monkeypatch.setattr(utils_mod, "get_model_info", _fake_get_model_info)

    # 临时把 (T2V, "dashscope") 换成 fake 连接器;退出时恢复
    saved = _CONNECTORS.get((Capability.T2V, "dashscope"))
    _CONNECTORS[(Capability.T2V, "dashscope")] = _ResumableFakeT2V

    event_bus._channels.clear()
    manager._running.clear()
    manager._cancellers.clear()
    yield Session

    if saved is not None:
        _CONNECTORS[(Capability.T2V, "dashscope")] = saved
    else:
        _CONNECTORS.pop((Capability.T2V, "dashscope"), None)


# ──────────────────────────────────────────────────────────────
# 工具:同步往 DB 插一条"上次进程在跑"的任务行
# ──────────────────────────────────────────────────────────────


async def _insert_unfinished(
    *,
    task_id: str,
    capability: str,
    model: str,
    upstream: str | None,
    platform_name: str | None = "fake-platform",
) -> None:
    await store.create_task(
        task_id=task_id,
        capability=capability,
        model=model,
        platform_name=platform_name,
        prompt="一只猫在草地上奔跑",
        params={},
    )
    # 模拟"上次进程"已经把状态切到 running,且(t2v 场景)拿到了 upstream
    await store.patch_task(
        task_id,
        status="running",
        upstream_task_id=upstream,
        progress_percent=10,
    )


# ──────────────────────────────────────────────────────────────
# 测试用例
# ──────────────────────────────────────────────────────────────


async def test_resume_t2v_with_upstream_task_id():
    await _insert_unfinished(
        task_id="t-resume-1",
        capability="t2v",
        model="wanx-t2v-fake-resumable",
        upstream="upstream-xyz-001",
    )

    summary = await manager.resume_unfinished_tasks()
    assert summary["resumed"] == 1
    assert summary["orphaned"] == 0

    # ensure_running 已经把后台 Task 拉起 — 等它跑完
    bg = manager._running.get("t-resume-1")
    assert bg is not None
    await bg

    row = await store.get_task("t-resume-1")
    assert row["status"] == "succeeded"
    assert len(row["files"]) == 1
    assert row["files"][0]["url"] == "/v1/artifacts/upstream-xyz-001.mp4"


async def test_no_upstream_marks_orphaned():
    """sync 类型 / submit 失败的任务 → 没法续传,直接 orphaned。"""
    await _insert_unfinished(
        task_id="t-orphan-1",
        capability="t2i",
        model="wanx-t2v-fake-resumable",
        upstream=None,
    )

    summary = await manager.resume_unfinished_tasks()
    assert summary["resumed"] == 0
    assert summary["orphaned"] == 1

    row = await store.get_task("t-orphan-1")
    assert row["status"] == "failed"
    assert row["error_code"] == "orphaned_by_restart"
    assert "重启" in (row["error_text"] or "")


async def test_platform_missing_marks_orphaned():
    """关联的 model_platform 配置已经被删 → 即使有 upstream 也无法续传。"""
    await _insert_unfinished(
        task_id="t-orphan-2",
        capability="t2v",
        model="lost-model",
        upstream="upstream-some-id",
    )

    summary = await manager.resume_unfinished_tasks()
    assert summary["resumed"] == 0
    assert summary["orphaned"] == 1

    row = await store.get_task("t-orphan-2")
    assert row["status"] == "failed"
    assert row["error_code"] == "orphaned_platform_missing"


async def test_recovery_is_idempotent_when_no_unfinished():
    """完全空表 → 不报错,summary 全 0。"""
    summary = await manager.resume_unfinished_tasks()
    assert summary == {"resumed": 0, "orphaned": 0, "skipped": 0}
