"""89-8:④ tab clip 行右侧运行位置标识 _resolve_clip_runtime_badge。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_badge_online_when_infinity_healthy():
    from chayuan.server.config_panel import runtime_framework_panel as mod

    fake_cli = MagicMock()
    fake_cli.kind = "infinity"
    fake_cli.healthcheck = MagicMock(return_value=True)
    fake_cli.base_url = "http://127.0.0.1:37997"

    with patch("chayuan.server.image_source.embedder.get_client",
               return_value=fake_cli), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("j/c", "infinity-local")):
        state, color, tip = mod._resolve_clip_runtime_badge()
    assert state == "online"
    assert color == "#22c55e"
    assert "Infinity" in tip
    assert "37997" in tip


def test_badge_degraded_when_inproc_but_platform_is_infinity():
    """期望走 Infinity 但实际跑 in-proc → 降级。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    fake_cli = MagicMock()
    fake_cli.kind = "inproc"
    fake_cli.healthcheck = MagicMock(return_value=True)

    with patch("chayuan.server.image_source.embedder.get_client",
               return_value=fake_cli), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("j/c", "infinity-local")):
        state, color, _tip = mod._resolve_clip_runtime_badge()
    assert state == "degraded"
    assert color == "#f59e0b"


def test_badge_online_when_inproc_no_platform():
    """platform=None 表示用户就想跑本地 → 算 online。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    fake_cli = MagicMock()
    fake_cli.kind = "inproc"
    fake_cli.healthcheck = MagicMock(return_value=True)

    with patch("chayuan.server.image_source.embedder.get_client",
               return_value=fake_cli), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("siglip", None)):
        state, color, tip = mod._resolve_clip_runtime_badge()
    assert state == "online"
    assert "本地" in tip


def test_badge_missing_when_get_client_raises():
    """get_client 抛 EmbedderUnavailable → 未配置(红色)。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    with patch("chayuan.server.image_source.embedder.get_client",
               side_effect=EmbedderUnavailable("nothing available")), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("ghost/x", "infinity-local")):
        state, color, _tip = mod._resolve_clip_runtime_badge()
    assert state == "missing"
    assert color == "#ef4444"


def test_badge_configured_when_user_picked_cloud_vendor_inproc_fallback():
    """92 题:用户在 ④ tab 选了云厂商(如 qwen-vl-max @ bailian)+ 走 inproc
    fallback → 显示 ●已配置 蓝色,不再红色。
    """
    from chayuan.server.config_panel import runtime_framework_panel as mod

    fake_cli = MagicMock()
    fake_cli.kind = "inproc"
    fake_cli.healthcheck = MagicMock(return_value=True)

    with patch("chayuan.server.image_source.embedder.get_client",
               return_value=fake_cli), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("qwen-vl-max", "bailian")):
        state, color, tip = mod._resolve_clip_runtime_badge()
    assert state == "configured"
    assert color == "#2563eb"
    assert "qwen-vl-max" in tip
    assert "bailian" in tip


def test_badge_configured_even_when_get_client_raises_cloud_vendor():
    """92 题:云厂商 + get_client 抛 → 仍算 configured(用户已选),不弹红。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    with patch("chayuan.server.image_source.embedder.get_client",
               side_effect=EmbedderUnavailable("siglip not in cache")), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("qwen-vl-max", "bailian")):
        state, color, tip = mod._resolve_clip_runtime_badge()
    assert state == "configured"
    assert color == "#2563eb"
    assert "qwen-vl-max" in tip


def test_badge_degraded_when_healthcheck_fails():
    """client.healthcheck()=False → 降级。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    fake_cli = MagicMock()
    fake_cli.kind = "infinity"
    fake_cli.healthcheck = MagicMock(return_value=False)
    fake_cli.base_url = "http://x"

    with patch("chayuan.server.image_source.embedder.get_client",
               return_value=fake_cli), \
         patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("j/c", "infinity-local")):
        state, _color, _tip = mod._resolve_clip_runtime_badge()
    assert state == "degraded"


def test_badge_missing_when_resolve_default_fails():
    from chayuan.server.config_panel import runtime_framework_panel as mod

    with patch("chayuan.server.image_source.embedder.resolve_default",
               side_effect=RuntimeError("yaml broken")):
        state, _color, _tip = mod._resolve_clip_runtime_badge()
    assert state == "missing"
