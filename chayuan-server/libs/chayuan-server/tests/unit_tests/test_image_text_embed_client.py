"""文本向量客户端 — 跟随用户配置的默认文本向量模型,不绑定特定模型。"""
from __future__ import annotations

import pytest


class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = ""
    def json(self): return self._payload


@pytest.mark.asyncio
async def test_embed_text_happy(monkeypatch):
    from chayuan.server.image_source import text_embed_client
    import httpx

    captured = {}
    async def _fake_post(self, url, json, headers=None, timeout=30.0):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        assert json["input"] == ["hello"]
        return _FakeResp(200, {"data": [{"embedding": [0.1] * 1024}]})
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    result = await text_embed_client.embed_text(
        "hello", base_url="http://127.0.0.1:62581",
        model="any-text-embed-model",
    )
    assert result.vector is not None
    assert len(result.vector) == 1024
    assert result.error is None
    assert result.model == "any-text-embed-model"


@pytest.mark.asyncio
async def test_embed_text_normalizes_url_with_or_without_v1(monkeypatch):
    """base_url 带不带 /v1 都应该 POST 到 /v1/embeddings。"""
    from chayuan.server.image_source import text_embed_client
    import httpx

    seen_urls = []
    async def _fake_post(self, url, json, headers=None, timeout=30.0):
        seen_urls.append(url)
        return _FakeResp(200, {"data": [{"embedding": [0.0]}]})
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    await text_embed_client.embed_text(
        "x", base_url="http://127.0.0.1:62581", model="m",
    )
    await text_embed_client.embed_text(
        "x", base_url="http://127.0.0.1:62581/v1", model="m",
    )
    await text_embed_client.embed_text(
        "x", base_url="http://127.0.0.1:62581/v1/", model="m",
    )
    assert all(u.endswith("/v1/embeddings") for u in seen_urls)
    assert all(u.count("/v1") == 1 for u in seen_urls)


@pytest.mark.asyncio
async def test_embed_text_passes_api_key_in_header(monkeypatch):
    from chayuan.server.image_source import text_embed_client
    import httpx

    captured = {}
    async def _fake_post(self, url, json, headers=None, timeout=30.0):
        captured["headers"] = dict(headers or {})
        return _FakeResp(200, {"data": [{"embedding": [0.0]}]})
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    await text_embed_client.embed_text(
        "x", base_url="http://x", model="m", api_key="sk-secret",
    )
    assert captured["headers"].get("Authorization") == "Bearer sk-secret"


@pytest.mark.asyncio
async def test_embed_text_service_unavailable(monkeypatch):
    from chayuan.server.image_source import text_embed_client
    import httpx

    async def _fake_post(self, url, json, headers=None, timeout=30.0):
        return _FakeResp(503, {"detail": "model not loaded"})
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    result = await text_embed_client.embed_text(
        "hello", base_url="http://127.0.0.1:62581", model="m",
    )
    assert result.vector is None
    assert "503" in (result.error or "")


@pytest.mark.asyncio
async def test_embed_text_empty_input():
    from chayuan.server.image_source import text_embed_client
    result = await text_embed_client.embed_text(
        "", base_url="http://127.0.0.1:62581", model="m",
    )
    assert result.vector is None
    assert "empty" in (result.error or "").lower()


def test_resolve_endpoint_uses_user_default(monkeypatch):
    """resolve_endpoint 必须读 utils.get_default_embedding + get_model_info,
    用户配什么模型就用什么。"""
    from chayuan.server.image_source import text_embed_client

    monkeypatch.setattr(
        "chayuan.server.utils.get_default_embedding",
        lambda: "user-picked-embed-model",
    )
    monkeypatch.setattr(
        "chayuan.server.utils.get_model_info",
        lambda model_name=None, **kw: {
            "api_base_url": "https://api.openai.com/v1",
            "api_key": "sk-xx",
            "platform_name": "openai",
        },
    )
    base_url, model, api_key = text_embed_client.resolve_endpoint()
    assert model == "user-picked-embed-model"
    assert base_url == "https://api.openai.com/v1"
    assert api_key == "sk-xx"


def test_resolve_endpoint_returns_none_when_unconfigured(monkeypatch):
    from chayuan.server.image_source import text_embed_client

    def _raise():
        raise RuntimeError("no model configured")
    monkeypatch.setattr(
        "chayuan.server.utils.get_default_embedding", _raise,
    )
    base_url, model, api_key = text_embed_client.resolve_endpoint()
    assert (base_url, model) == (None, None)
