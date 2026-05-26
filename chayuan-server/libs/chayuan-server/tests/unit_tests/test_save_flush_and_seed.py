"""67 题:dialog 关闭/保存前 flush pending draft + _save_all 自动 seed 默认模型。

只测纯函数 / 内存逻辑,不依赖 NiceGUI 渲染。
"""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# _flush_pending_draft / _flush_all_pending_drafts
# ---------------------------------------------------------------------------


class _FakeTimer:
    """模拟 NiceGUI ui.timer 的最小接口。"""
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def test_flush_pending_draft_cancels_timer_and_writes(monkeypatch):
    """有 pending timer → 取消 + 同步落盘。"""
    from chayuan.server.config_panel import model_config as mc

    # 准备 fake state
    class _S:
        pid = "test-provider"
    s = _S()

    written: list = []
    monkeypatch.setattr(mc, "_save_draft_state",
                        lambda state: written.append(state.pid))

    # 注入 pending timer
    fake = _FakeTimer()
    mc._DRAFT_FLUSH_TIMERS["test-provider"] = fake

    try:
        mc._flush_pending_draft(s)
        # timer 被取消
        assert fake.cancelled is True
        # 同步写盘
        assert written == ["test-provider"]
        # 字典内已无该 pid
        assert "test-provider" not in mc._DRAFT_FLUSH_TIMERS
    finally:
        mc._DRAFT_FLUSH_TIMERS.pop("test-provider", None)


def test_flush_pending_draft_no_pending_writes_directly(monkeypatch):
    """没 pending timer → 直接同步写盘(不抛)。"""
    from chayuan.server.config_panel import model_config as mc

    class _S:
        pid = "no-pending"
    s = _S()

    written: list = []
    monkeypatch.setattr(mc, "_save_draft_state",
                        lambda state: written.append(state.pid))

    mc._flush_pending_draft(s)
    assert written == ["no-pending"]


def test_flush_pending_draft_skips_empty_pid(monkeypatch):
    from chayuan.server.config_panel import model_config as mc

    class _S:
        pid = ""

    written: list = []
    monkeypatch.setattr(mc, "_save_draft_state",
                        lambda state: written.append(state.pid))
    mc._flush_pending_draft(_S())
    assert written == []  # pid 空 → 不写


def test_flush_all_pending_drafts(monkeypatch):
    """flush_all 处理多个 pid 的 pending timer。"""
    from chayuan.server.config_panel import model_config as mc

    class _S:
        def __init__(self, pid: str) -> None:
            self.pid = pid

    s1 = _S("p1")
    s2 = _S("p2")
    s3 = _S("p3")  # 没 pending timer 的不会被写

    fake1, fake2 = _FakeTimer(), _FakeTimer()
    mc._DRAFT_FLUSH_TIMERS["p1"] = fake1
    mc._DRAFT_FLUSH_TIMERS["p2"] = fake2

    written: list = []
    monkeypatch.setattr(mc, "_save_draft_state",
                        lambda state: written.append(state.pid))

    try:
        mc._flush_all_pending_drafts([s1, s2, s3])
        assert fake1.cancelled and fake2.cancelled
        assert set(written) == {"p1", "p2"}  # 顺序无所谓
        assert "p1" not in mc._DRAFT_FLUSH_TIMERS
        assert "p2" not in mc._DRAFT_FLUSH_TIMERS
    finally:
        mc._DRAFT_FLUSH_TIMERS.pop("p1", None)
        mc._DRAFT_FLUSH_TIMERS.pop("p2", None)


# ---------------------------------------------------------------------------
# _save_all 行为变化:不再过滤"无模型",自动 seed catalog 默认模型
# ---------------------------------------------------------------------------


def test_save_all_seeds_default_models_when_inventory_empty(tmp_path, monkeypatch):
    """启用但 inventory 空 → 用 meta.default_models 自动 seed。"""
    from chayuan.server.config_panel import model_config as mc

    # 用临时 yaml 路径,避免动真实文件
    monkeypatch.setattr(mc, "_FILE", "test_model_settings.yaml")

    # 找 PROVIDER_CATALOG 中第一个有 default_models 的厂商(deepseek 应该有)
    target_meta = next(
        m for m in mc.PROVIDER_CATALOG
        if m.pid == "deepseek" and m.default_models
    )

    # 构造 state:启用但 inventory 空
    state = mc._PlatformState(
        pid=target_meta.pid,
        meta=target_meta,
        platform_type=target_meta.platform_type,
        api_base_url=target_meta.default_api_base,
        api_key="dummy-key",
    )
    state.enabled = True
    assert not state.has_enabled_model(), "前置:state inventory 应为空"

    # mock 写盘相关
    written: dict = {}

    def _fake_load(name):
        class _R:
            doc = {}
            path = tmp_path / "test.yaml"
        return _R()

    def _fake_atomic(*args, **kwargs):
        pass

    def _fake_backup(*args, **kwargs):
        return None

    def _fake_mirror(name, doc):
        written["doc"] = doc

    monkeypatch.setattr(mc.yaml_store, "load_yaml", _fake_load)
    monkeypatch.setattr(mc.yaml_store, "_atomic_write", _fake_atomic)
    monkeypatch.setattr(mc.yaml_store, "_backup", _fake_backup)
    monkeypatch.setattr(mc.yaml_store, "mirror_namespace_to_db", _fake_mirror)
    monkeypatch.setattr(mc, "_clear_draft_state", lambda pid: None)

    kept, skipped = mc._save_all([state])

    # 有 default_models 的 enabled 厂商 → kept,seed 进 inventory
    assert kept == 1
    assert skipped == 0
    assert state.has_enabled_model(), "save_all 后 inventory 应被 seed"
    # llm_models 应包含 catalog 默认值
    saved_models = [m for m in target_meta.default_models.get("llm_models", [])]
    assert saved_models, "deepseek catalog 应该有 llm_models 默认"
    for mid in saved_models:
        assert mid in state.models


def test_save_all_keeps_disabled_excluded(tmp_path, monkeypatch):
    """disabled 厂商不写入 yaml(行为不变)。"""
    from chayuan.server.config_panel import model_config as mc
    monkeypatch.setattr(mc, "_FILE", "test.yaml")

    target_meta = next(m for m in mc.PROVIDER_CATALOG if m.pid == "deepseek")
    state = mc._PlatformState(
        pid=target_meta.pid,
        meta=target_meta,
        platform_type=target_meta.platform_type,
        api_key="key",
    )
    state.enabled = False  # 关键:未启用

    def _noop(*a, **kw): pass
    class _R:
        doc = {}
        path = tmp_path / "x.yaml"
    monkeypatch.setattr(mc.yaml_store, "load_yaml", lambda n: _R())
    monkeypatch.setattr(mc.yaml_store, "_atomic_write", _noop)
    monkeypatch.setattr(mc.yaml_store, "_backup", lambda *a, **k: None)
    monkeypatch.setattr(mc.yaml_store, "mirror_namespace_to_db", _noop)
    monkeypatch.setattr(mc, "_clear_draft_state", lambda pid: None)

    kept, skipped = mc._save_all([state])
    assert kept == 0
    assert skipped == 1


def test_save_all_no_seed_when_user_already_picked(tmp_path, monkeypatch):
    """已有用户选的模型 → 不再 seed catalog 默认(尊重用户选择)。"""
    from chayuan.server.config_panel import model_config as mc
    monkeypatch.setattr(mc, "_FILE", "test.yaml")

    target_meta = next(m for m in mc.PROVIDER_CATALOG if m.pid == "deepseek")
    state = mc._PlatformState(
        pid=target_meta.pid,
        meta=target_meta,
        platform_type=target_meta.platform_type,
        api_key="key",
    )
    state.enabled = True
    # 用户已选了一个非 default_models 中的模型
    state.models["user-chosen-model"] = {"group": "llm_models", "enabled": True}
    initial_models = dict(state.models)

    def _noop(*a, **kw): pass
    class _R:
        doc = {}
        path = tmp_path / "x.yaml"
    monkeypatch.setattr(mc.yaml_store, "load_yaml", lambda n: _R())
    monkeypatch.setattr(mc.yaml_store, "_atomic_write", _noop)
    monkeypatch.setattr(mc.yaml_store, "_backup", lambda *a, **k: None)
    monkeypatch.setattr(mc.yaml_store, "mirror_namespace_to_db", _noop)
    monkeypatch.setattr(mc, "_clear_draft_state", lambda pid: None)

    kept, _ = mc._save_all([state])
    assert kept == 1
    # state.models 应该和初始相同(没 seed catalog 默认进来)
    assert state.models == initial_models
