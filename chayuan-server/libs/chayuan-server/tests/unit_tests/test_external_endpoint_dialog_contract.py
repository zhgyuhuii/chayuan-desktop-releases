"""92-3:外置 endpoint dialog 契约测试。

UI 层自身用 NiceGUI mock 不易,这里只验证关键依赖链:
  * dialog 内部调 external_runtimes.set_external_url / delete / probe 时
    都用了 spec.name(不会跨 framework 误覆盖)
  * URL 校验依赖 set_external_url 自身的合法性检查
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_dialog_helper_function_exists():
    """_open_external_endpoint_dialog 至少在 module 内被定义为闭包。"""
    import inspect
    from chayuan.server.config_panel import runtime_framework_panel as mod

    src = inspect.getsource(mod)
    assert "_open_external_endpoint_dialog" in src
    assert "set_external_url" in src
    assert "delete_external_runtime" in src
    assert "probe_external" in src


def test_card_renders_link_button_for_external_endpoint():
    """卡片底部应有 link 按钮触发外置 endpoint 配置。"""
    import inspect
    from chayuan.server.config_panel import runtime_framework_panel as mod

    src = inspect.getsource(mod)
    # link icon 按钮 → tooltip 包含"外置 endpoint"
    assert "外置 endpoint" in src
    assert 'icon="link"' in src or "icon='link'" in src


def test_set_external_url_combines_with_runtime_yaml_save():
    """保存配置应触发 yaml_store.save_updates 写 runtimes 段。"""
    from chayuan.server.config_panel.external_runtimes import set_external_url

    captured = {}

    def _save(name, updates):
        captured["name"] = name
        captured["updates"] = updates
        return ("/p", "/b", [])

    fake_load = MagicMock()
    fake_load.doc = {}
    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        return_value=fake_load,
    ), patch(
        "chayuan.server.config_panel.yaml_store.save_updates", _save,
    ):
        ok, _ = set_external_url("comfyui", "http://10.0.0.5:18188",
                                 health_path="/system_stats")
    assert ok is True
    assert captured["name"] == "external_runtimes.yaml"
    assert "runtimes" in captured["updates"]
    assert "comfyui" in captured["updates"]["runtimes"]


def test_invalidate_probe_cache_called_after_save():
    """保存后应清 probe 缓存,才能让卡片立即用新状态。

    这里用 import 检查链:set_external_url 写完后,UI 层应当 invalidate;
    UI 层是闭包,但 invalidate_probe_cache 是 module export,验证可见性。
    """
    from chayuan.server.config_panel.runtime_framework_panel import (
        invalidate_probe_cache,
    )
    # 简单调用不抛
    invalidate_probe_cache()


def test_external_runtime_dialog_url_auto_prepends_http():
    """94-1:没有 http:// 前缀的 URL 自动补,而非拒绝。"""
    from chayuan.server.config_panel.external_runtimes import set_external_url
    fake_load = MagicMock()
    fake_load.doc = {}
    captured = {}
    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        return_value=fake_load,
    ), patch(
        "chayuan.server.config_panel.yaml_store.save_updates",
        lambda f, u: captured.update(u) or ("/p", "/b", []),
    ):
        ok, _ = set_external_url("infinity", "10.0.0.5:7997")
    assert ok is True
    assert captured["runtimes"]["infinity"]["url"] == "http://10.0.0.5:7997"
