"""infinity_server.py 端点 contract 测试 (Plan 3D)。"""
from __future__ import annotations

import base64
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from chayuan.server.image_source.infinity_server import (
    _DEFAULT_CONFIG, _register_routes,
)
from chayuan.server.modality._runtime_server_base import make_runtime_app


@pytest.fixture
def app_pair(monkeypatch):
    """构造测试 app + mock get_embedder 返 fake_embedder。

    fake_embedder.embed_text / embed_image 都返单个 list[float](BaseImageEmbedder API)。
    """
    fake_embedder = mock.MagicMock()
    # embed_text 单调用返单 vec
    fake_embedder.embed_text = mock.MagicMock(side_effect=lambda t: [0.1, 0.2])
    fake_embedder.embed_image = mock.MagicMock(side_effect=lambda b: [0.5, 0.6])

    monkeypatch.setattr(
        "chayuan.server.image_source.embedder.get_embedder",
        lambda *a, **kw: fake_embedder,
    )

    a = make_runtime_app(
        framework="infinity",
        title="test",
        default_config=dict(_DEFAULT_CONFIG),
        register_routes=_register_routes,
    )
    return a, fake_embedder


def test_embeddings_text_input(app_pair):
    a, fake = app_pair
    c = TestClient(a)
    r = c.post("/embeddings", json={"input": ["hello", "world"], "model": "siglip2-base"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 2
    assert body["data"][0]["index"] == 0
    assert body["data"][0]["embedding"] == [0.1, 0.2]
    assert body["data"][1]["index"] == 1
    assert body["data"][1]["embedding"] == [0.1, 0.2]


def test_embeddings_image_input(app_pair):
    a, fake = app_pair
    c = TestClient(a)
    b64 = base64.b64encode(b"\xff\xd8\xff fake jpeg").decode("ascii")
    r = c.post("/embeddings", json={
        "input": [{"image": f"data:image/jpeg;base64,{b64}"}],
        "model": "clip-vit-base",
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["embedding"] == [0.5, 0.6]


def test_embeddings_lazy_load_failure_returns_503(monkeypatch):
    """get_embedder 抛异常时 /embeddings 返 503。"""
    monkeypatch.setattr(
        "chayuan.server.image_source.embedder.get_embedder",
        mock.MagicMock(side_effect=RuntimeError("model 缺失")),
    )
    a = make_runtime_app(
        framework="infinity",
        title="test",
        default_config=dict(_DEFAULT_CONFIG),
        register_routes=_register_routes,
    )
    c = TestClient(a)
    r = c.post("/embeddings", json={"input": ["x"], "model": "x"})
    assert r.status_code == 503
    assert "image embedder 加载失败" in r.json()["detail"]
