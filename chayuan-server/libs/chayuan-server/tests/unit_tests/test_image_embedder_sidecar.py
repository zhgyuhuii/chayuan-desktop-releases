"""回归保护:get_embedder() 走 sidecar 路径时返回的对象**必须**有 embed_image / embed_text。

历史 bug(2026-05-16):sidecar ready 时直接 return InfinityHttpClient,但它只实现 async
encode_* 不是 BaseImageEmbedder 的 embed_*,pipeline / connector 调 emb.embed_image()
立刻 AttributeError。修复:用 _HttpBackedEmbedder 包装。
"""
from __future__ import annotations

import pytest


def test_get_embedder_sidecar_returns_base_image_embedder(monkeypatch):
    from chayuan.server.image_source import embedder as e

    class _FakeStatus:
        state = "ready"
        endpoint = "http://127.0.0.1:62586"

    class _FakeMgr:
        status = _FakeStatus()

    class _FakeRegistry:
        def get(self, cap):
            assert cap == "image-embedding"
            return _FakeMgr()

    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime_registry.get_registry",
        lambda: _FakeRegistry(),
    )

    # 防止本测试无意中触发真实 HTTP 探活
    class _FakeClient:
        def __init__(self, *, base_url, model_id):
            self.base_url = base_url
            self.name = model_id
            self.dim = 512
    monkeypatch.setattr(
        "chayuan.server.image_source.embedder_clients.infinity_http.InfinityHttpClient",
        _FakeClient,
    )

    emb = e.get_embedder("openai/clip-vit-base-patch32")

    # 关键断言:embed_image / embed_text 必须存在 — pipeline / connector 调它们
    assert hasattr(emb, "embed_image"), (
        f"返回 {type(emb).__name__} 缺少 embed_image — 回归到 2026-05-16 bug"
    )
    assert hasattr(emb, "embed_text"), (
        f"返回 {type(emb).__name__} 缺少 embed_text"
    )
    # 必须是 BaseImageEmbedder 子类(即被 _HttpBackedEmbedder 包装过)
    from chayuan.server.image_source.embedder_base import BaseImageEmbedder
    assert isinstance(emb, BaseImageEmbedder)
