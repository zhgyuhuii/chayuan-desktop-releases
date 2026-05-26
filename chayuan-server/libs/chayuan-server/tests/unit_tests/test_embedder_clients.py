"""89-1/89-2/89-3:embedder_clients 模块测试。

覆盖:
  * Protocol 静态形状
  * InfinityHttpClient: batch encode 序列化、健康检查、错误处理
  * InProcEmbedderClient: 模型未知回退、async batch 并发
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

def test_image_embedder_client_protocol_attrs():
    """Protocol 必须暴露这几个属性 + 方法名。"""
    from chayuan.server.image_source.embedder_clients.base import (
        ImageEmbedderClient,
    )
    needed_attrs = ["name", "model_id", "kind", "dim"]
    needed_methods = ["encode_image", "encode_text", "healthcheck", "close"]
    for a in needed_attrs:
        assert a in ImageEmbedderClient.__annotations__
    for m in needed_methods:
        assert m in ImageEmbedderClient.__dict__


def test_embedder_unavailable_is_runtime_error():
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )
    assert issubclass(EmbedderUnavailable, RuntimeError)


# ---------------------------------------------------------------------------
# InfinityHttpClient — base64 编码 / batch / 错误处理
# ---------------------------------------------------------------------------

def test_b64_data_url_jpeg_magic():
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        _b64_data_url,
    )
    jpg = b"\xff\xd8\xff\xe0fakejpeg"
    url = _b64_data_url(jpg)
    assert url.startswith("data:image/jpeg;base64,")


def test_b64_data_url_png_magic():
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        _b64_data_url,
    )
    png = b"\x89PNG\r\n\x1a\nfakepng"
    url = _b64_data_url(png)
    assert url.startswith("data:image/png;base64,")


def test_b64_data_url_unknown_falls_back_to_jpeg():
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        _b64_data_url,
    )
    url = _b64_data_url(b"\x00\x00\x00unknown")
    assert url.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_infinity_http_encode_image_serializes_payload():
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        InfinityHttpClient,
    )

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value={
        "data": [{"embedding": [0.1, 0.2, 0.3]},
                 {"embedding": [0.4, 0.5, 0.6]}]
    })

    cli = InfinityHttpClient(
        base_url="http://localhost:7997", model_id="jinaai/jina-clip-v1",
    )
    cli._client = MagicMock()

    async def _post(*args, **kwargs):
        # 校验 payload 形状
        body = kwargs.get("json")
        assert body["model"] == "jinaai/jina-clip-v1"
        assert isinstance(body["input"], list)
        assert all(s.startswith("data:image/") for s in body["input"])
        return fake_response

    cli._client.post = _post

    vectors = await cli.encode_image([b"\xff\xd8\xff\xe0img1", b"\x89PNG\r\nimg2"])
    assert len(vectors) == 2
    assert vectors[0] == [0.1, 0.2, 0.3]
    assert cli.dim == 3   # 首次 encode 后 dim 填入


@pytest.mark.asyncio
async def test_infinity_http_encode_text_serializes_payload():
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        InfinityHttpClient,
    )

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json = MagicMock(return_value={
        "data": [{"embedding": [0.0] * 768}]
    })

    cli = InfinityHttpClient(
        base_url="http://x", model_id="m",
    )
    cli._client = MagicMock()

    async def _post(*args, **kwargs):
        body = kwargs.get("json")
        assert body["input"] == ["hello"]
        return fake_resp

    cli._client.post = _post
    vectors = await cli.encode_text(["hello"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 768


@pytest.mark.asyncio
async def test_infinity_http_429_raises_unavailable():
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        InfinityHttpClient,
    )
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    fake_resp = MagicMock()
    fake_resp.status_code = 429
    fake_resp.text = "rate limited"

    cli = InfinityHttpClient(base_url="http://x", model_id="m")
    cli._client = MagicMock()

    async def _post(*args, **kwargs):
        return fake_resp

    cli._client.post = _post

    with pytest.raises(EmbedderUnavailable):
        await cli.encode_image([b"\xff\xd8\xff\xe0img"])


@pytest.mark.asyncio
async def test_infinity_http_connection_failure_raises_unavailable():
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        InfinityHttpClient,
    )
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    cli = InfinityHttpClient(base_url="http://x", model_id="m")
    cli._client = MagicMock()

    async def _post(*args, **kwargs):
        raise ConnectionError("network down")

    cli._client.post = _post
    with pytest.raises(EmbedderUnavailable):
        await cli.encode_text(["hi"])


@pytest.mark.asyncio
async def test_infinity_http_empty_inputs_return_empty():
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        InfinityHttpClient,
    )
    cli = InfinityHttpClient(base_url="http://x", model_id="m")
    cli._client = MagicMock()
    # 完全不应触发 HTTP 调用
    cli._client.post = MagicMock(side_effect=AssertionError("不应该被调用"))
    assert await cli.encode_image([]) == []
    assert await cli.encode_text([]) == []


def test_infinity_http_healthcheck_returns_false_on_no_server():
    """无服务时 healthcheck 不抛,返 False。"""
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        InfinityHttpClient,
    )
    cli = InfinityHttpClient(
        base_url="http://127.0.0.1:1",  # 已知无服务端口
        model_id="m",
    )
    assert cli.healthcheck() is False


def test_infinity_http_close_idempotent():
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        InfinityHttpClient,
    )
    cli = InfinityHttpClient(base_url="http://x", model_id="m")
    cli._client = None  # 已关闭
    cli.close()  # 不抛
    cli.close()  # 再次幂等


# ---------------------------------------------------------------------------
# InProcEmbedderClient — 适配现有 BaseImageEmbedder
# ---------------------------------------------------------------------------

def _stub_embedder_with_dim(dim: int = 4):
    """造一个 BaseImageEmbedder 假对象。"""
    fake = MagicMock()
    fake.is_available = MagicMock(return_value=True)
    fake.embed_image = MagicMock(return_value=[0.1] * dim)
    fake.embed_text = MagicMock(return_value=[0.2] * dim)
    return fake


@pytest.mark.asyncio
async def test_inproc_encode_image_async_gather():
    from chayuan.server.image_source.embedder_clients import inproc as mod

    fake_spec = MagicMock()
    fake_spec.dim = 4
    fake_emb = _stub_embedder_with_dim(4)

    with patch("chayuan.server.image_source.loaders.create_embedder",
               lambda spec: fake_emb), \
         patch("chayuan.server.image_source.embedder.SUPPORTED_MODELS",
               {"test/model": fake_spec}), \
         patch("chayuan.server.image_source.embedder.default_model_name",
               return_value="test/model"):
        from chayuan.server.image_source.embedder_clients.inproc import (
            InProcEmbedderClient,
        )
        cli = InProcEmbedderClient("test/model")
        vectors = await cli.encode_image([b"img1", b"img2", b"img3"])

    assert len(vectors) == 3
    for v in vectors:
        assert len(v) == 4
    assert fake_emb.embed_image.call_count == 3


def test_inproc_unknown_model_raises_unavailable():
    """92 题修复:模型不在 SUPPORTED_MODELS → 直接抛 EmbedderUnavailable。

    旧行为是回退到 default,但 default 自身可能就是错的(用户在 ④ tab 选了
    云 API 视觉对话模型如 qwen-vl-max),导致 KeyError 死循环。改为直接抛
    让上层 fallback 用内置兜底模型而非用户配的错误 model_id。
    """
    from chayuan.server.image_source.embedder_clients import inproc as mod
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    with patch("chayuan.server.image_source.embedder.SUPPORTED_MODELS",
               {"the-default": MagicMock()}):
        from chayuan.server.image_source.embedder_clients.inproc import (
            InProcEmbedderClient,
        )
        with pytest.raises(EmbedderUnavailable) as exc:
            InProcEmbedderClient("qwen-vl-max")
    assert "qwen-vl-max" in str(exc.value)
    assert "the-default" in str(exc.value)  # 列出可选项


def test_inproc_healthcheck_reflects_underlying_is_available():
    from chayuan.server.image_source.embedder_clients import inproc as mod

    fake_spec = MagicMock()
    fake_spec.dim = 4
    fake_emb = _stub_embedder_with_dim(4)
    fake_emb.is_available = MagicMock(return_value=False)

    with patch("chayuan.server.image_source.loaders.create_embedder",
               lambda spec: fake_emb), \
         patch("chayuan.server.image_source.embedder.SUPPORTED_MODELS",
               {"x/m": fake_spec}), \
         patch("chayuan.server.image_source.embedder.default_model_name",
               return_value="x/m"):
        from chayuan.server.image_source.embedder_clients.inproc import (
            InProcEmbedderClient,
        )
        cli = InProcEmbedderClient("x/m")
    assert cli.healthcheck() is False


def test_inproc_create_embedder_failure_raises_unavailable():
    from chayuan.server.image_source.embedder_clients import inproc as mod
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    fake_spec = MagicMock()
    fake_spec.dim = 0

    def _fail(spec):
        raise RuntimeError("torch not installed")

    with patch("chayuan.server.image_source.loaders.create_embedder", _fail), \
         patch("chayuan.server.image_source.embedder.SUPPORTED_MODELS",
               {"x/m": fake_spec}), \
         patch("chayuan.server.image_source.embedder.default_model_name",
               return_value="x/m"):
        from chayuan.server.image_source.embedder_clients.inproc import (
            InProcEmbedderClient,
        )
        with pytest.raises(EmbedderUnavailable):
            InProcEmbedderClient("x/m")
