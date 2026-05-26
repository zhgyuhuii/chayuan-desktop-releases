"""Config Panel · api_gate 门闸工具单测。

只测纯函数部分（探活 / URL 拼接 / 缓存）；NiceGUI UI 渲染部分不在这里测。
"""
from __future__ import annotations

import time

import pytest


def test_api_base_url_default():
    from chayuan.server.config_panel.api_gate import api_base_url
    # 不假设用户配置，只要 URL 合法
    url = api_base_url()
    assert url.startswith("http://")
    assert ":" in url.split("//", 1)[1]  # 有端口


def test_api_base_url_rewrites_0_0_0_0(monkeypatch):
    from chayuan.server.config_panel import api_gate
    from chayuan.settings import Settings
    # 0.0.0.0 必须被 UI 端重写成 127.0.0.1，否则浏览器/配置面板访问不到
    monkeypatch.setattr(
        Settings.basic_settings, "API_SERVER",
        {"host": "0.0.0.0", "port": 17861}, raising=False,
    )
    url = api_gate.api_base_url()
    assert url == "http://127.0.0.1:17861"


def test_probe_api_status_unreachable_returns_not_ok(monkeypatch):
    """连一个保证没人监听的随机高端口，应返回 ok=False。"""
    from chayuan.server.config_panel.api_gate import (
        invalidate_cache, probe_api_status,
    )
    invalidate_cache()
    # 65432 通常空闲；如果环境里真有服务，loop 找一个更高的
    status = probe_api_status(
        base_url="http://127.0.0.1:65432",
        timeout_sec=1.0, use_cache=False,
    )
    assert status.ok is False
    assert status.url == "http://127.0.0.1:65432"
    assert status.detail  # 必有错因
    assert "65432" not in status.detail  # 别把端口原样吐出（安全）


def _patch_httpx_with_counter(monkeypatch, call_urls):
    """用真正的 httpx.MockTransport 拦截请求；``call_urls`` 记录被访问的 URL。"""
    import httpx

    def handler(request):
        call_urls.append(str(request.url))
        return httpx.Response(200, text='{"status":"ok"}')

    original_client = httpx.Client

    class MockedClient(original_client):  # type: ignore[misc]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "Client", MockedClient)


def test_probe_cache_hits_within_ttl(monkeypatch):
    from chayuan.server.config_panel import api_gate
    api_gate.invalidate_cache()
    urls: list = []
    _patch_httpx_with_counter(monkeypatch, urls)

    # 第 1 次真跑
    s1 = api_gate.probe_api_status(base_url="http://x.test:1", use_cache=True)
    assert s1.ok is True
    # 第 2 次在 TTL 内：应命中缓存，不再调 httpx
    s2 = api_gate.probe_api_status(base_url="http://x.test:1", use_cache=True)
    assert s2.ok is True
    assert len(urls) == 1
    # invalidate 后再调：必须重新跑
    api_gate.invalidate_cache()
    api_gate.probe_api_status(base_url="http://x.test:1", use_cache=True)
    assert len(urls) == 2


def test_probe_different_urls_dont_share_cache(monkeypatch):
    """缓存的 URL 必须参与命中判定——换个 URL 必须重算。"""
    from chayuan.server.config_panel import api_gate
    api_gate.invalidate_cache()
    urls: list = []
    _patch_httpx_with_counter(monkeypatch, urls)

    api_gate.probe_api_status(base_url="http://a.test:1", use_cache=True)
    api_gate.probe_api_status(base_url="http://b.test:1", use_cache=True)
    assert urls == ["http://a.test:1/healthz", "http://b.test:1/healthz"]
