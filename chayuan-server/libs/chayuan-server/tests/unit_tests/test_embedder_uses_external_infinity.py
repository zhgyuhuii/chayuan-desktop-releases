"""92-4:embedder._infinity_base_url 优先用 external_runtimes 配置。"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_infinity_base_url_prefers_external_runtime():
    """external_runtimes 配了 → 优先用。"""
    from chayuan.server.image_source import embedder

    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value={"url": "http://gpu-server.local:7997",
                      "health_path": "/health", "enabled": True},
    ):
        url = embedder._infinity_base_url("infinity-local")
    assert url == "http://gpu-server.local:7997"


def test_infinity_base_url_strips_trailing_slash():
    from chayuan.server.image_source import embedder
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value={"url": "http://x:7997/", "enabled": True},
    ):
        url = embedder._infinity_base_url("")
    assert url == "http://x:7997"


def test_infinity_base_url_falls_back_to_env_var(monkeypatch):
    """无外置配置 → 走 env var。"""
    from chayuan.server.image_source import embedder
    monkeypatch.setenv("CHAYUAN_INFINITY_BASE_URL", "http://envvar:9999")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value=None,
    ):
        url = embedder._infinity_base_url("")
    assert url == "http://envvar:9999"


def test_infinity_base_url_default_when_nothing_configured(monkeypatch):
    """全空 → 默认 127.0.0.1:37997。"""
    from chayuan.server.image_source import embedder
    monkeypatch.delenv("CHAYUAN_INFINITY_BASE_URL", raising=False)
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value=None,
    ):
        url = embedder._infinity_base_url("")
    assert url == "http://127.0.0.1:37997"


def test_infinity_base_url_swallows_external_runtimes_error(monkeypatch):
    """external_runtimes 抛 → 不阻断,走 env / 默认。"""
    from chayuan.server.image_source import embedder
    monkeypatch.delenv("CHAYUAN_INFINITY_BASE_URL", raising=False)
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        side_effect=RuntimeError("yaml broken"),
    ):
        url = embedder._infinity_base_url("")
    assert url == "http://127.0.0.1:37997"


def test_pick_client_uses_external_url_for_infinity_platform():
    """完整链路:platform=infinity-* + external 配置 → InfinityHttpClient
    使用 external 的 base_url。
    """
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        InfinityHttpClient,
    )
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value={"url": "http://10.0.0.5:7997", "enabled": True},
    ):
        cli = embedder.pick_client("jinaai/jina-clip-v1", "infinity-local")
    assert isinstance(cli, InfinityHttpClient)
    assert cli.base_url == "http://10.0.0.5:7997"
    cli.close()
