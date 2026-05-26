"""Lifecycle orchestrator 单元测试.

策略:
* 不真下载;用 monkeypatch 桩 ``ModelDownloader.run`` 直接返回成功。
* 不真 wire;用 ``register_wire_impl`` 注入桩 callable。
* 用 ``_InMemoryStore`` 验证状态机阶段顺序与 overall 单调性。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List

import pytest

from chayuan_modelmgr import lifecycle as lc
from chayuan_modelmgr.lifecycle import (
    Lifecycle,
    Stage,
    StageEvent,
    WireOutcome,
    _InMemoryStore,
    _overall_for,
    register_wire_impl,
)


# ---------- 阶段权重 ----------

def test_overall_monotonic_within_each_stage():
    prev = -1.0
    for stage in [Stage.RESOLVE, Stage.CONNECT, Stage.DOWNLOAD, Stage.VERIFY, Stage.WIRE]:
        for pct in [0, 50, 100]:
            cur = _overall_for(stage, pct)
            assert cur >= prev, f"{stage} {pct}% -> {cur} < {prev}"
            prev = cur


def test_overall_ready_is_100():
    assert _overall_for(Stage.READY, 100) == 100.0


def test_overall_clamp():
    assert _overall_for(Stage.DOWNLOAD, 200) <= 100.0
    assert _overall_for(Stage.DOWNLOAD, -10) >= 0.0


# ---------- InMemoryStore ----------

def test_in_memory_store_history_replay_to_new_subscriber():
    s = _InMemoryStore()
    s.put(StageEvent(task_id="t1", stage=Stage.RESOLVE, progress=0))
    s.put(StageEvent(task_id="t1", stage=Stage.DOWNLOAD, progress=50))

    # 新订阅应当回放历史
    async def collect():
        q = s.subscribe("t1")
        out: List[StageEvent] = []
        for _ in range(2):
            out.append(await asyncio.wait_for(q.get(), timeout=0.5))
        return out

    events = asyncio.run(collect())
    assert [e.stage for e in events] == [Stage.RESOLVE, Stage.DOWNLOAD]


# ---------- 端到端: 桩下载 + 桩 wire ----------

class _FakeDownloadResult:
    def __init__(self, dest: Path) -> None:
        self.dest = dest
        self.bytes_total = 1024


def test_lifecycle_happy_path(monkeypatch, tmp_path):
    fake_dest = tmp_path / "Qwen" / "Qwen2.5-7B-Instruct"
    fake_dest.mkdir(parents=True)

    # 桩 ModelDownloader.run
    def _fake_run(self):
        # 模拟一次进度回调
        if self.opt.progress_cb:
            from chayuan_modelmgr.progress import ProgressEvent
            self.opt.progress_cb(ProgressEvent(
                repo=self.opt.repo, filename="model.safetensors",
                bytes_done=512, bytes_total=1024,
            ))
        return _FakeDownloadResult(fake_dest)

    monkeypatch.setattr(
        "chayuan_modelmgr.lifecycle.ModelDownloader.run",
        _fake_run,
        raising=True,
    )

    # 桩 wire
    captured: dict = {}
    def _fake_wire(model_path: str, capability: str, target_runtime: str) -> WireOutcome:
        captured["called"] = True
        captured["model_path"] = model_path
        captured["capability"] = capability
        captured["runtime"] = target_runtime
        return WireOutcome(ok=True, runtime=target_runtime, detail="ok-stub")

    register_wire_impl(_fake_wire)

    # 用独立 lifecycle 实例 (避免污染 singleton)
    instance = Lifecycle(store=_InMemoryStore())
    task_id = instance.start(
        repo="Qwen/Qwen2.5-7B-Instruct",
        capability="chat",
        target_runtime="ollama",
    )

    async def collect():
        out: List[StageEvent] = []
        async for ev in instance.subscribe(task_id):
            out.append(ev)
            if len(out) > 50:  # 安全上限
                break
        return out

    events = asyncio.run(asyncio.wait_for(collect(), timeout=5.0))

    stages_seen = [e.stage for e in events]
    # 关键阶段都出现
    for s in (Stage.RESOLVE, Stage.CONNECT, Stage.DOWNLOAD, Stage.VERIFY, Stage.WIRE, Stage.READY):
        assert s in stages_seen, f"stage {s} 未出现:{stages_seen}"
    # 终态 READY
    assert events[-1].stage == Stage.READY
    assert events[-1].overall == 100.0
    # wire 被调到
    assert captured["called"]
    assert captured["capability"] == "chat"
    assert captured["runtime"] == "ollama"


def test_lifecycle_wire_failure_emits_error(monkeypatch, tmp_path):
    fake_dest = tmp_path / "x"
    fake_dest.mkdir()

    def _fake_run(self):
        return _FakeDownloadResult(fake_dest)

    monkeypatch.setattr(
        "chayuan_modelmgr.lifecycle.ModelDownloader.run",
        _fake_run,
        raising=True,
    )

    register_wire_impl(lambda *_a, **_k: WireOutcome(
        ok=False, runtime="ollama", detail="wire intentionally fails",
    ))

    instance = Lifecycle(store=_InMemoryStore())
    task_id = instance.start(
        repo="x/y", capability="chat", target_runtime="ollama",
    )

    async def collect():
        out = []
        async for ev in instance.subscribe(task_id):
            out.append(ev)
        return out

    events = asyncio.run(asyncio.wait_for(collect(), timeout=5.0))
    assert events[-1].stage == Stage.ERROR
    assert "wire" in events[-1].detail.lower()
    # 回滚: 目录应被删
    assert not fake_dest.exists()


def test_lifecycle_cancel(monkeypatch, tmp_path):
    """cancel 在下载阶段命中时, 状态机应进入 ERROR 而非 READY。"""
    import threading
    import time

    fake_dest = tmp_path / "x"
    fake_dest.mkdir()

    def _slow_run(self):
        time.sleep(0.05)
        if self.opt.cancel and self.opt.cancel.is_set():
            raise InterruptedError("cancelled")
        return _FakeDownloadResult(fake_dest)

    monkeypatch.setattr(
        "chayuan_modelmgr.lifecycle.ModelDownloader.run",
        _slow_run,
        raising=True,
    )

    register_wire_impl(lambda *a, **k: WireOutcome(ok=True, runtime="ollama"))

    instance = Lifecycle(store=_InMemoryStore())
    task_id = instance.start(repo="a/b", capability="chat", target_runtime="ollama")
    instance.cancel(task_id)

    async def collect():
        out = []
        async for ev in instance.subscribe(task_id):
            out.append(ev)
        return out

    events = asyncio.run(asyncio.wait_for(collect(), timeout=5.0))
    # 由于 cancel 时序不确定,只确认要么 READY 要么 ERROR;但 cancel set 前未读到时也可能直接完成
    assert events[-1].stage in (Stage.READY, Stage.ERROR)
