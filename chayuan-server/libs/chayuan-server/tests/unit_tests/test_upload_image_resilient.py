"""图像上传容错 + HTTP 桥接 embedder 测试。

核心契约:
  * 用户诉求:"即使图像嵌入模型不能用也要能上传图像"
    → embedder 失败时,文件仍落盘,响应 code=0 + warning,前端能列出文件
  * Infinity inventory 命中的 model → get_embedder 返 _HttpBackedEmbedder,
    走 HTTP 而非本地 PyTorch
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _try_http_backed_embedder
# ---------------------------------------------------------------------------

def test_try_http_backed_embedder_returns_bridge_when_external_loaded():
    """外置 Infinity 加载了模型 → 返 _HttpBackedEmbedder。"""
    from chayuan.server.image_source import embedder

    fake_model = MagicMock(model_id="openai/clip-vit-base-patch32")
    fake_cli = MagicMock()
    fake_cli.healthcheck = MagicMock(return_value=True)
    fake_cli.dim = 512
    fake_cli.base_url = "http://127.0.0.1:7997"

    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value={"url": "http://127.0.0.1:7997", "enabled": True},
    ), patch(
        "chayuan.server.config_panel.infinity_inventory.fetch_infinity_models",
        return_value=[fake_model],
    ), patch(
        "chayuan.server.image_source.embedder_clients.infinity_http.InfinityHttpClient",
        return_value=fake_cli,
    ):
        bridge = embedder._try_http_backed_embedder("openai/clip-vit-base-patch32")
    assert bridge is not None
    assert bridge.name == "openai/clip-vit-base-patch32"
    assert bridge._client is fake_cli


def test_try_http_backed_embedder_returns_none_when_model_not_in_inventory():
    """Infinity inventory 不含该 model → 返 None,让原 PyTorch 路径处理。"""
    from chayuan.server.image_source import embedder

    other = MagicMock(model_id="some-other/model")
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value={"url": "http://x:7997", "enabled": True},
    ), patch(
        "chayuan.server.config_panel.infinity_inventory.fetch_infinity_models",
        return_value=[other],
    ):
        bridge = embedder._try_http_backed_embedder("openai/clip-vit-base-patch32")
    assert bridge is None


def test_try_http_backed_embedder_returns_none_when_no_runtime():
    """没配外置 + 本地 pip 没起 → None。"""
    from chayuan.server.image_source import embedder

    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value=None,
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.is_local_infinity_running",
        return_value=False,
    ):
        bridge = embedder._try_http_backed_embedder("any/model")
    assert bridge is None


def test_try_http_backed_embedder_swallows_module_error():
    """external_runtimes / inventory 抛 → 不阻断,返 None。"""
    from chayuan.server.image_source import embedder

    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        side_effect=RuntimeError("yaml broken"),
    ):
        bridge = embedder._try_http_backed_embedder("any/model")
    assert bridge is None


def test_get_embedder_uses_http_bridge_when_available():
    """get_embedder 入口:Infinity inventory 命中 → 返桥接,跳过 PyTorch。"""
    from chayuan.server.image_source import embedder

    fake_bridge = MagicMock()
    fake_bridge._client = MagicMock(base_url="http://127.0.0.1:7997")
    embedder._invalidate_embedder_cache(None)
    with patch.object(embedder, "_try_http_backed_embedder",
                      return_value=fake_bridge):
        emb = embedder.get_embedder("openai/clip-vit-base-patch32")
    assert emb is fake_bridge
    embedder._invalidate_embedder_cache(None)


# ---------------------------------------------------------------------------
# _HttpBackedEmbedder embed_image / embed_text 同步桥接
# ---------------------------------------------------------------------------

def test_http_backed_embed_image_uses_sync_httpx_post():
    """新实现:embed_image 走同步 httpx.post,不再 asyncio.run。"""
    from chayuan.server.image_source import embedder
    import sys

    fake_cli = MagicMock()
    fake_cli.dim = 4
    fake_cli.base_url = "http://x:7997"

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json = MagicMock(return_value={
        "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
    })
    fake_httpx = MagicMock()
    fake_httpx.post = MagicMock(return_value=fake_resp)

    bridge = embedder._HttpBackedEmbedder(fake_cli, "x/y")
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        vec = bridge.embed_image(b"\xff\xd8\xff\xe0fakejpg")
    assert len(vec) == 4
    # 校验 payload 形态
    posted = fake_httpx.post.call_args
    assert posted[0][0] == "http://x:7997/embeddings"
    body = posted[1]["json"]
    assert body["model"] == "x/y"
    assert body["input"][0].startswith("data:image/jpeg;base64,")


def test_http_backed_embed_text_uses_sync_httpx_post():
    from chayuan.server.image_source import embedder
    import sys

    fake_cli = MagicMock()
    fake_cli.dim = 4
    fake_cli.base_url = "http://x:7997"
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json = MagicMock(return_value={
        "data": [{"embedding": [1.0, 0.0, 0.0, 0.0]}]
    })
    fake_httpx = MagicMock()
    fake_httpx.post = MagicMock(return_value=fake_resp)

    bridge = embedder._HttpBackedEmbedder(fake_cli, "x/y")
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        vec = bridge.embed_text("hello")
    assert len(vec) == 4
    body = fake_httpx.post.call_args[1]["json"]
    assert body["input"] == ["hello"]


def test_http_backed_embed_image_works_inside_running_event_loop():
    """关键回归:在已有 event loop 中调用同步 embed_image 不再炸。

    用户报错:``asyncio.run() cannot be called from a running event loop``
    —— 修复后改用同步 httpx,不动 asyncio。
    """
    import asyncio
    import sys
    from chayuan.server.image_source import embedder

    fake_cli = MagicMock()
    fake_cli.dim = 4
    fake_cli.base_url = "http://x:7997"
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json = MagicMock(return_value={
        "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
    })
    fake_httpx = MagicMock()
    fake_httpx.post = MagicMock(return_value=fake_resp)

    bridge = embedder._HttpBackedEmbedder(fake_cli, "x/y")

    async def _outer():
        # 这里我们处于 running event loop 内
        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            return bridge.embed_image(b"\xff\xd8\xff\xe0fake")

    vec = asyncio.run(_outer())
    assert len(vec) == 4


def test_http_backed_embed_image_5xx_raises_runtime_error():
    """Infinity 返 5xx → 抛 RuntimeError 给上层 image_routes 标 vector_status=error。"""
    from chayuan.server.image_source import embedder
    import sys

    fake_cli = MagicMock()
    fake_cli.base_url = "http://x:7997"
    fake_cli.dim = 0
    fake_resp = MagicMock()
    fake_resp.status_code = 503
    fake_resp.text = "service unavailable"
    fake_httpx = MagicMock()
    fake_httpx.post = MagicMock(return_value=fake_resp)

    bridge = embedder._HttpBackedEmbedder(fake_cli, "x/y")
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        with pytest.raises(RuntimeError) as exc:
            bridge.embed_image(b"\xff\xd8\xff\xe0fake")
    assert "503" in str(exc.value)


def test_http_backed_embed_image_connection_error_raises_runtime_error():
    from chayuan.server.image_source import embedder
    import sys

    fake_cli = MagicMock()
    fake_cli.base_url = "http://x:7997"
    fake_cli.dim = 0
    fake_httpx = MagicMock()
    fake_httpx.post = MagicMock(side_effect=ConnectionError("refused"))

    bridge = embedder._HttpBackedEmbedder(fake_cli, "x/y")
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        with pytest.raises(RuntimeError) as exc:
            bridge.embed_image(b"\xff\xd8\xff\xe0fake")
    assert "Infinity 不可达" in str(exc.value)


def test_http_backed_b64_data_url_small_image_passthrough():
    """小图(< 60KB)不缩,保留原 mime。"""
    from chayuan.server.image_source import embedder

    fake_cli = MagicMock(); fake_cli.dim = 0; fake_cli.base_url = "x"
    bridge = embedder._HttpBackedEmbedder(fake_cli, "m")
    # 小图保留原 jpeg
    assert bridge._b64_data_url(b"\xff\xd8\xff\xe0jpg").startswith(
        "data:image/jpeg;base64,"
    )
    assert bridge._b64_data_url(b"\x89PNG\r\n\x1a\npng").startswith(
        "data:image/png;base64,"
    )


def test_http_backed_b64_data_url_shrinks_large_image():
    """大图必走 PIL 缩图 → 转 JPEG;输出 base64 < Infinity 限制。"""
    pytest.importorskip("PIL")
    from chayuan.server.image_source import embedder
    from PIL import Image
    import io
    import os

    # 造一张噪声图(纯色压缩太狠);1024×1024 random RGB → JPEG 通常 200KB+
    raw = os.urandom(1024 * 1024 * 3)
    img = Image.frombytes("RGB", (1024, 1024), raw)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    big_blob = buf.getvalue()
    assert len(big_blob) > 60_000, f"测试图太小:{len(big_blob)}"

    fake_cli = MagicMock(); fake_cli.dim = 0; fake_cli.base_url = "x"
    bridge = embedder._HttpBackedEmbedder(fake_cli, "m")
    url = bridge._b64_data_url(big_blob)
    assert url.startswith("data:image/jpeg;base64,")

    # base64 部分长度应当小于 Infinity 上限
    b64_part = url.split(",", 1)[1]
    assert len(b64_part) <= bridge._MAX_INPUT_CHARS, (
        f"缩图后 base64 仍超限制:{len(b64_part)} > {bridge._MAX_INPUT_CHARS}"
    )


def test_http_backed_b64_data_url_handles_rgba_png():
    """RGBA / palette 模式自动转 RGB,不抛。"""
    pytest.importorskip("PIL")
    from chayuan.server.image_source import embedder
    from PIL import Image
    import io

    img = Image.new("RGBA", (1024, 1024), color=(0, 255, 0, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    big_blob = buf.getvalue()
    if len(big_blob) <= 60_000:
        # 极小可能 PNG 压缩到很小,加点噪声让它够大
        import os
        big_blob = big_blob + os.urandom(60_000)

    fake_cli = MagicMock(); fake_cli.dim = 0; fake_cli.base_url = "x"
    bridge = embedder._HttpBackedEmbedder(fake_cli, "m")
    url = bridge._b64_data_url(big_blob)
    # 输出应当是 jpeg(_shrink_image 强制转)
    assert url.startswith("data:image/jpeg;base64,") or \
           url.startswith("data:image/png;base64,")


def test_http_backed_shrink_image_falls_back_to_original_when_pil_fails():
    """PIL 处理失败 → 返回原 blob,不抛。"""
    from chayuan.server.image_source import embedder
    fake_cli = MagicMock(); fake_cli.dim = 0; fake_cli.base_url = "x"
    bridge = embedder._HttpBackedEmbedder(fake_cli, "m")
    # 给一个无效图片 bytes
    out = bridge._shrink_image(b"not an image at all")
    assert out == b"not an image at all"



def test_http_backed_is_available_delegates_to_healthcheck():
    from chayuan.server.image_source import embedder

    fake_cli = MagicMock()
    fake_cli.healthcheck = MagicMock(return_value=True)
    fake_cli.dim = 0
    bridge = embedder._HttpBackedEmbedder(fake_cli, "x/y")
    assert bridge.is_available() is True

    fake_cli.healthcheck = MagicMock(side_effect=ConnectionError())
    assert bridge.is_available() is False


def test_http_backed_capabilities_is_crossmodal():
    """CLIP 类是跨模态。"""
    from chayuan.server.image_source import embedder

    fake_cli = MagicMock(); fake_cli.dim = 0
    bridge = embedder._HttpBackedEmbedder(fake_cli, "x/y")
    assert bridge.capabilities.image is True
    assert bridge.capabilities.text is True
    assert bridge.capabilities.crossmodal is True


def test_http_backed_read_bytes_from_path(tmp_path):
    from chayuan.server.image_source import embedder
    p = tmp_path / "x.bin"
    p.write_bytes(b"\xde\xad\xbe\xef")
    bridge = embedder._HttpBackedEmbedder(MagicMock(dim=0), "m")
    assert bridge._read_bytes(str(p)) == b"\xde\xad\xbe\xef"


def test_http_backed_read_bytes_from_bytes_passthrough():
    from chayuan.server.image_source import embedder
    bridge = embedder._HttpBackedEmbedder(MagicMock(dim=0), "m")
    assert bridge._read_bytes(b"raw") == b"raw"


# ---------------------------------------------------------------------------
# upload_image_endpoint:embedder 失败也返 200 + 文件落盘
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_image_returns_200_when_embedder_missing(tmp_path):
    """新契约:embedder 不可用 → 文件仍落盘,响应 code=0 + warning。"""
    from chayuan.server.api_server import image_routes as mod
    from unittest.mock import AsyncMock

    # mock 文件
    fake_file = MagicMock()
    fake_file.filename = "1.jpg"
    fake_file.content_type = "image/jpeg"
    fake_file.read = AsyncMock(return_value=b"\xff\xd8\xff\xe0fake")

    # mock connector — add_image 抛"未知图像模型"异常
    fake_conn = MagicMock()
    fake_conn.add_image = MagicMock(
        side_effect=KeyError("openai/clip-vit-base-patch32"),
    )

    fake_user = {"id": 1, "role": "admin", "is_guest": False}
    img_dir = tmp_path / "images_root"

    with patch.object(mod, "_get_source_or_404", return_value={"kind": "image"}), \
         patch.object(mod, "_build_image_connector", return_value=fake_conn), \
         patch("chayuan.server.image_source.store._image_indexes_root",
               return_value=img_dir), \
         patch.object(mod, "can_write", return_value=True), \
         patch.object(mod, "_looks_like_missing_embedder", return_value=True), \
         patch.object(mod, "_suggest_image_embedder",
                      return_value={
                          "repo": "jinaai/jina-clip-v1",
                          "capability": "image-embedding",
                          "runtime": "infinity",
                          "marketplace_deeplink": "/admin#x",
                      }):
        ret = await mod.upload_image_endpoint(
            source_id=1, files=[fake_file], tags="", user=fake_user,
        )

    # 不再抛 422,而是 200 + warning
    assert ret["code"] == 0
    assert ret["data"]["added"][0]["filename"] == "1.jpg"
    assert ret["data"]["added"][0]["vector_status"] == "missing"
    # 文件确实落盘
    saved = list((img_dir / "1_files").glob("*1.jpg"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"\xff\xd8\xff\xe0fake"
    # warning 字段
    assert ret["warning"]["code"] == "image_embedder_missing"
    assert ret["warning"]["suggested_model"] == "jinaai/jina-clip-v1"


@pytest.mark.asyncio
async def test_upload_image_returns_200_when_embedder_works(tmp_path):
    """正常路径回归:embedder OK → vector_status=indexed。"""
    from chayuan.server.api_server import image_routes as mod
    from unittest.mock import AsyncMock

    fake_file = MagicMock()
    fake_file.filename = "ok.jpg"
    fake_file.content_type = "image/jpeg"
    fake_file.read = AsyncMock(return_value=b"\xff\xd8\xff\xe0fake")

    fake_conn = MagicMock()
    fake_conn.add_image = MagicMock(return_value=42)

    fake_user = {"id": 1, "role": "admin", "is_guest": False}
    img_dir = tmp_path / "images_root"

    with patch.object(mod, "_get_source_or_404", return_value={"kind": "image"}), \
         patch.object(mod, "_build_image_connector", return_value=fake_conn), \
         patch("chayuan.server.image_source.store._image_indexes_root",
               return_value=img_dir), \
         patch.object(mod, "can_write", return_value=True):
        ret = await mod.upload_image_endpoint(
            source_id=1, files=[fake_file], tags="", user=fake_user,
        )

    assert ret["code"] == 0
    assert ret["data"]["added"][0]["image_id"] == 42
    assert ret["data"]["added"][0]["vector_status"] == "indexed"
    assert "warning" not in ret


@pytest.mark.asyncio
async def test_upload_image_partial_success(tmp_path):
    """混合场景:一张成功一张 embedder 失败 → 都收录到 added,各自标 status。"""
    from chayuan.server.api_server import image_routes as mod
    from unittest.mock import AsyncMock

    files = []
    for i, name in enumerate(["good.jpg", "bad.jpg"]):
        f = MagicMock()
        f.filename = name
        f.content_type = "image/jpeg"
        f.read = AsyncMock(return_value=b"\xff\xd8\xff\xe0" + name.encode())
        files.append(f)

    # 第一次 add_image 成功,第二次抛 KeyError
    fake_conn = MagicMock()
    fake_conn.add_image = MagicMock(
        side_effect=[42, KeyError("missing-model")],
    )

    fake_user = {"id": 1, "role": "admin", "is_guest": False}
    img_dir = tmp_path / "images_root"

    with patch.object(mod, "_get_source_or_404", return_value={"kind": "image"}), \
         patch.object(mod, "_build_image_connector", return_value=fake_conn), \
         patch("chayuan.server.image_source.store._image_indexes_root",
               return_value=img_dir), \
         patch.object(mod, "can_write", return_value=True), \
         patch.object(mod, "_looks_like_missing_embedder", return_value=True), \
         patch.object(mod, "_suggest_image_embedder",
                      return_value={
                          "repo": "jinaai/jina-clip-v1",
                          "capability": "image-embedding",
                          "runtime": "infinity",
                          "marketplace_deeplink": "",
                      }):
        ret = await mod.upload_image_endpoint(
            source_id=1, files=files, tags="", user=fake_user,
        )

    assert ret["code"] == 0
    statuses = {it["filename"]: it["vector_status"]
                for it in ret["data"]["added"]}
    assert statuses["good.jpg"] == "indexed"
    assert statuses["bad.jpg"] == "missing"
    # 仍带 warning(因为有部分失败)
    assert "warning" in ret
