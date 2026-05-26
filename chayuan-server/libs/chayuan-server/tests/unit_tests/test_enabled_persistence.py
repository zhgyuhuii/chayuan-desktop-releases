"""74 题:enabled 字段持久化 — 关闭厂商重启后应记住关闭状态。

bug:_make_state_from_saved 历史硬编码 ``s.enabled = True``,无视 yaml/draft
中的 enabled 字段;to_yaml_item 也不写 enabled。修复后:
- to_yaml_item 写 enabled 字段
- _make_state_from_saved 优先读 yaml 的 enabled,fallback True 兼容老 yaml
"""
from __future__ import annotations

import pytest


def test_to_yaml_item_writes_enabled_true():
    from chayuan.server.config_panel.model_config import (
        PROVIDER_CATALOG, _PlatformState,
    )
    meta = next(p for p in PROVIDER_CATALOG if p.pid == "deepseek")
    s = _PlatformState(pid="deepseek", meta=meta, api_key="sk-x")
    s.enabled = True
    item = s.to_yaml_item()
    assert "enabled" in item
    assert item["enabled"] is True


def test_to_yaml_item_writes_enabled_false():
    """启用开关关闭后,enabled 字段应写为 False。"""
    from chayuan.server.config_panel.model_config import (
        PROVIDER_CATALOG, _PlatformState,
    )
    meta = next(p for p in PROVIDER_CATALOG if p.pid == "deepseek")
    s = _PlatformState(pid="deepseek", meta=meta, api_key="sk-x")
    s.enabled = False
    item = s.to_yaml_item()
    assert item["enabled"] is False


def test_make_state_reads_explicit_enabled_false():
    """yaml/draft 中 enabled=False → state 应为禁用。"""
    from chayuan.server.config_panel.model_config import (
        PROVIDER_CATALOG, _make_state_from_saved,
    )
    meta = next(p for p in PROVIDER_CATALOG if p.pid == "deepseek")
    item = {
        "platform_name": "deepseek",
        "platform_type": "openai",
        "api_base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-real",
        "enabled": False,           # 显式禁用
        "llm_models": ["deepseek-chat"],
    }
    s = _make_state_from_saved(meta, item)
    assert s.enabled is False, "显式 enabled=False 应被尊重"


def test_make_state_reads_explicit_enabled_true():
    """yaml/draft 中 enabled=True → state 启用。"""
    from chayuan.server.config_panel.model_config import (
        PROVIDER_CATALOG, _make_state_from_saved,
    )
    meta = next(p for p in PROVIDER_CATALOG if p.pid == "deepseek")
    item = {"platform_name": "deepseek", "enabled": True, "llm_models": []}
    s = _make_state_from_saved(meta, item)
    assert s.enabled is True


def test_make_state_legacy_yaml_without_enabled_defaults_to_true():
    """老 yaml 没 enabled 字段 → 默认 True(向后兼容)。"""
    from chayuan.server.config_panel.model_config import (
        PROVIDER_CATALOG, _make_state_from_saved,
    )
    meta = next(p for p in PROVIDER_CATALOG if p.pid == "deepseek")
    item = {
        "platform_name": "deepseek",
        "api_key": "sk-x",
        "llm_models": ["deepseek-chat"],
        # 没有 enabled 字段
    }
    s = _make_state_from_saved(meta, item)
    assert s.enabled is True, "无 enabled 字段时,fallback 为 True 兼容老 yaml"


def test_make_state_item_none_returns_disabled():
    """item=None(catalog 默认条目)返回禁用状态。"""
    from chayuan.server.config_panel.model_config import (
        PROVIDER_CATALOG, _make_state_from_saved,
    )
    meta = next(p for p in PROVIDER_CATALOG if p.pid == "deepseek")
    s = _make_state_from_saved(meta, None)
    assert s.enabled is False


def test_round_trip_disabled_state(tmp_path, monkeypatch):
    """端到端验证:禁用 → 草稿落盘 → reload → 仍是禁用。"""
    from chayuan.server.config_panel import model_config as mc
    monkeypatch.setattr(mc, "_FILE", "model_settings.yaml")
    monkeypatch.setattr(mc, "_DRAFT_FILE", "model_settings.draft.yaml")
    # 让 yaml_store 写到 tmp_path
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    import chayuan.settings as _s
    monkeypatch.setattr(_s, "CHAYUAN_ROOT", tmp_path)

    meta = next(p for p in mc.PROVIDER_CATALOG if p.pid == "deepseek")
    s = mc._PlatformState(pid="deepseek", meta=meta, api_key="sk-real")
    s.enabled = False  # 用户禁用

    # 直接同步 flush(避免 NiceGUI ui.timer 依赖)
    mc._save_draft_state(s)

    # 验证 draft 文件确实写了 enabled=False
    from chayuan.server.config_panel import yaml_store
    load = yaml_store.load_yaml("model_settings.draft.yaml")
    drafts = (load.doc or {}).get("DRAFTS") or {}
    assert "deepseek" in drafts
    assert drafts["deepseek"]["enabled"] is False

    # 重新构造 state(模拟重启 _build_initial_states 流程)
    s2 = mc._make_state_from_saved(meta, drafts["deepseek"])
    assert s2.enabled is False, "重启后 enabled 应仍为 False"
