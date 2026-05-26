"""71 题 e2e 验证 — _save_all 写 yaml → _capability_grouped 能否读到 deepseek。

这是把整条数据链跑一遍,排除"代码 bug 没修复"的可能性。
若本测试通过,问题一定在运行时(进程没重启 / 浏览器 cache / yaml 文件实际异常)。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    """切到 tmp_path 作 CHAYUAN_ROOT,避免污染真实环境。"""
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    # chayuan.settings 内 CHAYUAN_ROOT 是模块级变量,需重 patch
    import chayuan.settings as _s
    monkeypatch.setattr(_s, "CHAYUAN_ROOT", tmp_path)
    return tmp_path


def test_save_all_then_capability_grouped_includes_deepseek(isolated_root, monkeypatch):
    """模拟用户:配 deepseek (api_key + enabled),💾 保存,然后 _capability_grouped 应能看到 deepseek。"""
    from chayuan.server.config_panel import model_config as mc
    from chayuan.server.config_panel.runtime_framework_panel import (
        _capability_grouped,
    )

    # 1. 找到 deepseek 的 ProviderMeta
    target_meta = next(m for m in mc.PROVIDER_CATALOG if m.pid == "deepseek")
    assert target_meta.default_models, "deepseek catalog 应有 llm_models 默认"

    # 2. 构造一个用户输入(api_key + enabled,inventory 空)
    state = mc._PlatformState(
        pid=target_meta.pid,
        meta=target_meta,
        platform_type=target_meta.platform_type,
        api_base_url=target_meta.default_api_base,
        api_key="sk-test-deepseek-key",  # 用户填的
    )
    state.enabled = True
    assert not state.has_enabled_model(), "前置:模型清单应为空(用户没点拉模型)"

    # 3. 调 _save_all(行为:67 题 auto-seed catalog 默认模型 + 写 yaml)
    # 不动 model_platform_repository(它需要真实 DB,这里跳过 bump_platform_version)
    monkeypatch.setattr(
        mc.yaml_store, "mirror_namespace_to_db", lambda name, doc: None,
    )
    monkeypatch.setattr(mc, "_clear_draft_state", lambda pid: None)

    # ui.timer 在非 NiceGUI 上下文会抛 — _save_all 内部 try/except 会退回同步
    kept, skipped = mc._save_all([state])
    assert kept == 1, f"deepseek 应被写入,实际 kept={kept}"

    # 4. 验证 yaml 文件真的写了
    yaml_path = isolated_root / "model_settings.yaml"
    assert yaml_path.exists(), "yaml 文件应已写盘"
    text = yaml_path.read_text(encoding="utf-8")
    assert "deepseek" in text, f"yaml 应有 deepseek 条目,实际内容前 500 字符:\n{text[:500]}"

    # 5. 调 _capability_grouped(读 yaml + 过滤 enabled+api_key)
    grouped = _capability_grouped()

    # 6. 验证 deepseek 出现在 chat capability 下,group_label = "云 · deepseek"
    chat_groups = grouped.get("chat", {})
    expected_label = "云 · deepseek"
    assert expected_label in chat_groups, (
        f"_capability_grouped 应有 '{expected_label}' 分组,"
        f"实际所有 chat 分组: {list(chat_groups.keys())}"
    )
    chat_models = [mid for mid, _disp in chat_groups[expected_label]]
    # catalog 默认 deepseek-chat / deepseek-reasoner 应在
    catalog_defaults = target_meta.default_models.get("llm_models", [])
    for default_mid in catalog_defaults:
        assert default_mid in chat_models, (
            f"catalog 默认模型 {default_mid} 应在分组里,实际: {chat_models}"
        )

    print(
        f"\n✅ E2E 通过:deepseek → 云·deepseek 分组 → {chat_models}\n"
        f"   说明代码链路 OK,真机看不到 = 进程没重启 / 浏览器 cache 旧"
    )


def test_save_all_writes_correct_yaml_structure(isolated_root, monkeypatch):
    """yaml 文件结构验证:MODEL_PLATFORMS 列表中 deepseek 字段完整。"""
    from chayuan.server.config_panel import model_config as mc
    from chayuan.server.config_panel import yaml_store

    target_meta = next(m for m in mc.PROVIDER_CATALOG if m.pid == "deepseek")
    state = mc._PlatformState(
        pid="deepseek",
        meta=target_meta,
        platform_type="openai",
        api_base_url="https://api.deepseek.com/v1",
        api_key="sk-real-key",
    )
    state.enabled = True

    monkeypatch.setattr(
        mc.yaml_store, "mirror_namespace_to_db", lambda name, doc: None,
    )
    monkeypatch.setattr(mc, "_clear_draft_state", lambda pid: None)
    mc._save_all([state])

    # 重读 yaml 验证 MODEL_PLATFORMS 结构
    load = yaml_store.load_yaml("model_settings.yaml")
    assert isinstance(load.doc, dict)
    platforms = load.doc.get("MODEL_PLATFORMS")
    assert isinstance(platforms, list)
    deepseek_entries = [p for p in platforms if p.get("platform_name") == "deepseek"]
    assert len(deepseek_entries) == 1
    entry = deepseek_entries[0]
    assert entry["api_key"] == "sk-real-key"
    assert entry["api_base_url"] == "https://api.deepseek.com/v1"
    # 67 题:启用但 inventory 空 → catalog 默认 seed
    assert entry.get("llm_models"), (
        f"deepseek.llm_models 应被 catalog 默认 seed,实际: {entry.get('llm_models')}"
    )


def test_capability_grouped_filters_disabled_provider(isolated_root, monkeypatch):
    """sanity:disabled 厂商不会出现在 grouped(防止过度兜底)。"""
    from chayuan.server.config_panel import model_config as mc
    from chayuan.server.config_panel.runtime_framework_panel import (
        _capability_grouped,
    )

    target_meta = next(m for m in mc.PROVIDER_CATALOG if m.pid == "deepseek")
    state = mc._PlatformState(
        pid="deepseek",
        meta=target_meta,
        platform_type="openai",
        api_base_url=target_meta.default_api_base,
        api_key="sk-key",
    )
    state.enabled = False  # 禁用

    monkeypatch.setattr(
        mc.yaml_store, "mirror_namespace_to_db", lambda name, doc: None,
    )
    monkeypatch.setattr(mc, "_clear_draft_state", lambda pid: None)
    kept, _ = mc._save_all([state])
    assert kept == 0  # 不写入

    grouped = _capability_grouped()
    chat_groups = grouped.get("chat", {})
    assert "云 · deepseek" not in chat_groups
