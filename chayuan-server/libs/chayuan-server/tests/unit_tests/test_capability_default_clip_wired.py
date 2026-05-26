"""89-5:④ tab clip → embedder.resolve_default 接通验证。

要点:
  * _save_capability_default("clip", model_id) 同时写顶层
    DEFAULT_IMAGE_EMBEDDING_MODEL + nested capability_defaults.clip
  * resolve_default 读 capability_defaults.clip 优先
  * resolve_default 读不到 nested 时 fallback 顶层 DEFAULT_IMAGE_EMBEDDING_MODEL
  * 写完后 image embedder client cache 被清空
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _save_capability_default 写 nested
# ---------------------------------------------------------------------------

def test_save_clip_writes_nested_capability_defaults(monkeypatch):
    from chayuan.server.config_panel import runtime_framework_panel as mod

    captured: dict = {}

    def _fake_save_updates(file_, updates):
        captured.update(updates)
        return ("/fake/path", "/fake/bak", [])

    monkeypatch.setattr(
        "chayuan.server.config_panel.yaml_store.save_updates",
        _fake_save_updates,
    )
    monkeypatch.setattr(
        mod, "_lookup_platform_for_model",
        lambda mid, mt: "infinity-local",
    )
    # 屏蔽下游副作用
    monkeypatch.setattr(
        "chayuan.server.image_source.embedder._invalidate_client_cache",
        lambda *a, **k: None,
    )

    ok, msg = mod._save_capability_default("clip", "jinaai/jina-clip-v1")
    assert ok is True
    # 顶层 DEFAULT_IMAGE_EMBEDDING_MODEL 已写
    assert captured["DEFAULT_IMAGE_EMBEDDING_MODEL"] == "jinaai/jina-clip-v1"
    # nested capability_defaults.clip 已写
    nested = captured["capability_defaults"]
    assert nested["clip"]["model_id"] == "jinaai/jina-clip-v1"
    assert nested["clip"]["platform_name"] == "infinity-local"


def test_save_non_clip_does_not_write_nested(monkeypatch):
    """cap != clip 时不写 capability_defaults。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    captured: dict = {}

    def _fake_save_updates(file_, updates):
        captured.update(updates)
        return ("/fake/path", "/fake/bak", [])

    monkeypatch.setattr(
        "chayuan.server.config_panel.yaml_store.save_updates",
        _fake_save_updates,
    )

    ok, _ = mod._save_capability_default("chat", "qwen2.5-7b")
    assert ok is True
    assert "capability_defaults" not in captured


def test_save_clip_falls_back_when_platform_unknown(monkeypatch):
    """反查 platform 失败 → 写空字符串,不抛。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    captured: dict = {}

    def _fake_save_updates(file_, updates):
        captured.update(updates)
        return ("/fake/path", "/fake/bak", [])

    monkeypatch.setattr(
        "chayuan.server.config_panel.yaml_store.save_updates",
        _fake_save_updates,
    )
    monkeypatch.setattr(
        mod, "_lookup_platform_for_model",
        lambda mid, mt: None,
    )
    monkeypatch.setattr(
        "chayuan.server.image_source.embedder._invalidate_client_cache",
        lambda *a, **k: None,
    )

    ok, _ = mod._save_capability_default("clip", "ghost/x")
    assert ok is True
    assert captured["capability_defaults"]["clip"]["platform_name"] == ""


def test_save_clip_invalidates_embedder_cache(monkeypatch):
    """89-5:写完后必须清 embedder client 缓存。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    monkeypatch.setattr(
        "chayuan.server.config_panel.yaml_store.save_updates",
        lambda f, u: ("/p", "/b", []),
    )
    monkeypatch.setattr(
        mod, "_lookup_platform_for_model", lambda *a, **k: "infinity-local",
    )

    invalidate = MagicMock()
    monkeypatch.setattr(
        "chayuan.server.image_source.embedder._invalidate_client_cache",
        invalidate,
    )

    mod._save_capability_default("clip", "jinaai/jina-clip-v1")
    invalidate.assert_called_once()


# ---------------------------------------------------------------------------
# resolve_default 优先级 1 / 1.5 / 2 / 3
# ---------------------------------------------------------------------------

def _stub_yaml(doc: dict):
    fake = MagicMock()
    fake.doc = doc
    return fake


def test_resolve_default_reads_nested_capability_defaults_clip():
    """优先 1: capability_defaults.clip 命中。"""
    from chayuan.server.image_source import embedder

    doc = {
        "capability_defaults": {
            "clip": {
                "model_id": "jinaai/jina-clip-v1",
                "platform_name": "infinity-local",
            }
        }
    }
    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        return_value=_stub_yaml(doc),
    ):
        mid, plat = embedder.resolve_default()
    assert mid == "jinaai/jina-clip-v1"
    assert plat == "infinity-local"


def test_resolve_default_falls_back_to_top_level_default_image_embedding_model():
    """优先 1.5: nested 没有但顶层 DEFAULT_IMAGE_EMBEDDING_MODEL 有。"""
    from chayuan.server.image_source import embedder

    doc = {
        "DEFAULT_IMAGE_EMBEDDING_MODEL": "OFA-Sys/chinese-clip-vit-base-patch16",
    }
    fake_models = {
        "OFA-Sys/chinese-clip-vit-base-patch16": {"platform_name": "infinity-local"},
    }
    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        return_value=_stub_yaml(doc),
    ), patch("chayuan.server.utils.get_config_models", return_value=fake_models):
        mid, plat = embedder.resolve_default()
    assert mid == "OFA-Sys/chinese-clip-vit-base-patch16"
    assert plat == "infinity-local"


def test_resolve_default_top_level_with_unknown_platform_returns_none():
    """顶层 model_id 在 get_config_models 找不到 → platform=None,走 inproc。"""
    from chayuan.server.image_source import embedder

    doc = {"DEFAULT_IMAGE_EMBEDDING_MODEL": "ghost/x"}
    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        return_value=_stub_yaml(doc),
    ), patch("chayuan.server.utils.get_config_models",
             return_value={}):
        mid, plat = embedder.resolve_default()
    assert mid == "ghost/x"
    assert plat is None


def test_resolve_default_nested_takes_priority_over_top_level():
    """同时存在时 nested 优先。"""
    from chayuan.server.image_source import embedder

    doc = {
        "DEFAULT_IMAGE_EMBEDDING_MODEL": "OFA-Sys/chinese-clip-vit-base-patch16",
        "capability_defaults": {
            "clip": {
                "model_id": "jinaai/jina-clip-v1",
                "platform_name": "infinity-local",
            }
        },
    }
    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        return_value=_stub_yaml(doc),
    ):
        mid, plat = embedder.resolve_default()
    assert mid == "jinaai/jina-clip-v1"


# ---------------------------------------------------------------------------
# _lookup_platform_for_model
# ---------------------------------------------------------------------------

def test_lookup_platform_for_model_returns_name():
    from chayuan.server.config_panel.runtime_framework_panel import (
        _lookup_platform_for_model,
    )
    with patch("chayuan.server.utils.get_config_models",
               return_value={"jina/c": {"platform_name": "infinity-local"}}):
        assert _lookup_platform_for_model("jina/c", "image2text") == "infinity-local"


def test_lookup_platform_for_model_returns_none_when_not_found():
    from chayuan.server.config_panel.runtime_framework_panel import (
        _lookup_platform_for_model,
    )
    with patch("chayuan.server.utils.get_config_models",
               return_value={"other/m": {"platform_name": "x"}}):
        assert _lookup_platform_for_model("ghost/y", "image2text") is None


def test_lookup_platform_for_model_swallows_errors():
    from chayuan.server.config_panel.runtime_framework_panel import (
        _lookup_platform_for_model,
    )
    with patch("chayuan.server.utils.get_config_models",
               side_effect=RuntimeError("yaml broken")):
        assert _lookup_platform_for_model("x/y", "image2text") is None
