"""模型设置 v2 ④ 默认模型/对话参数 — 纯函数单测(57 题 P3)。

测 ``_load_chat_params`` / ``_save_chat_params`` 的 yaml 序列化 / 反序列化。
不依赖 NiceGUI 渲染。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from chayuan.server.config_panel.model_settings.defaults_subpage import (
    _CHAT_PARAM_DEFS,
    _load_chat_params,
    _save_chat_params,
)


# ---------------------------------------------------------------------------
# _load_chat_params
# ---------------------------------------------------------------------------


def test_load_returns_defaults_when_yaml_missing(monkeypatch):
    """yaml 不存在或读失败 → 返默认值(用户首次进入 UI 不报错)。"""
    class _Empty:
        doc = None
    with patch(
        "chayuan.server.config_panel.model_settings.defaults_subpage.yaml_store.load_yaml",
        return_value=_Empty(),
    ):
        out = _load_chat_params()
    assert out["HISTORY_LEN"] == 3
    assert out["TEMPERATURE"] == 0.7
    assert out["MAX_TOKENS"] is None


def test_load_picks_existing_yaml_values(monkeypatch):
    """yaml 中已有值 → 用 yaml 值。"""
    class _Doc:
        doc = {"HISTORY_LEN": 7, "TEMPERATURE": 0.3, "MAX_TOKENS": 2048,
               "OTHER_KEY": "ignored"}
    with patch(
        "chayuan.server.config_panel.model_settings.defaults_subpage.yaml_store.load_yaml",
        return_value=_Doc(),
    ):
        out = _load_chat_params()
    assert out == {"HISTORY_LEN": 7, "TEMPERATURE": 0.3, "MAX_TOKENS": 2048}


def test_load_partial_yaml_uses_defaults_for_missing(monkeypatch):
    """yaml 只有部分 key → 缺失项走默认。"""
    class _Doc:
        doc = {"HISTORY_LEN": 5}
    with patch(
        "chayuan.server.config_panel.model_settings.defaults_subpage.yaml_store.load_yaml",
        return_value=_Doc(),
    ):
        out = _load_chat_params()
    assert out["HISTORY_LEN"] == 5
    assert out["TEMPERATURE"] == 0.7  # 默认
    assert out["MAX_TOKENS"] is None  # 默认


# ---------------------------------------------------------------------------
# _save_chat_params
# ---------------------------------------------------------------------------


def test_save_int_and_float_are_typed(monkeypatch):
    """保存时 int / float 字段会被转型(防 widget 把数字当字符串)。"""
    captured = {}

    def _fake_save(name, updates):
        captured["name"] = name
        captured["updates"] = updates

    with patch(
        "chayuan.server.config_panel.model_settings.defaults_subpage.yaml_store.save_updates",
        side_effect=_fake_save,
    ):
        _save_chat_params({"HISTORY_LEN": "10", "TEMPERATURE": "0.5",
                           "MAX_TOKENS": "1024"})

    assert captured["name"] == "model_settings.yaml"
    u = captured["updates"]
    assert u["HISTORY_LEN"] == 10 and isinstance(u["HISTORY_LEN"], int)
    assert u["TEMPERATURE"] == 0.5 and isinstance(u["TEMPERATURE"], float)
    assert u["MAX_TOKENS"] == 1024


def test_save_max_tokens_empty_becomes_none():
    """MAX_TOKENS 留空 → 写 None(跟随模型默认)。"""
    captured = {}

    def _fake_save(name, updates):
        captured.update(updates=updates)

    with patch(
        "chayuan.server.config_panel.model_settings.defaults_subpage.yaml_store.save_updates",
        side_effect=_fake_save,
    ):
        _save_chat_params({"HISTORY_LEN": 3, "TEMPERATURE": 0.7,
                           "MAX_TOKENS": ""})
    assert captured["updates"]["MAX_TOKENS"] is None


def test_save_only_writes_known_keys():
    """传入未声明的 key 不会被写入(防意外注入)。"""
    captured = {}

    def _fake_save(name, updates):
        captured.update(updates=updates)

    with patch(
        "chayuan.server.config_panel.model_settings.defaults_subpage.yaml_store.save_updates",
        side_effect=_fake_save,
    ):
        _save_chat_params({"HISTORY_LEN": 5, "TEMPERATURE": 0.8,
                           "MAX_TOKENS": None,
                           "MALICIOUS": "drop database"})
    assert "MALICIOUS" not in captured["updates"]


# ---------------------------------------------------------------------------
# _CHAT_PARAM_DEFS sanity
# ---------------------------------------------------------------------------


def test_chat_param_defs_contract():
    """字段定义稳定(每条 8 元元组),否则 _load/_save 会出 IndexError。"""
    for entry in _CHAT_PARAM_DEFS:
        assert len(entry) == 8, f"_CHAT_PARAM_DEFS 字段数变了: {entry}"
        key, label, type_str, *_ = entry
        assert isinstance(key, str) and key
        assert type_str in ("int", "float", "int_optional"), (
            f"未知字段类型 {type_str!r} for {key}"
        )
