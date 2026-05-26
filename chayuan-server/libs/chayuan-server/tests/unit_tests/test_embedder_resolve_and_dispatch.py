"""89-4:embedder.py resolve_default / pick_client / get_client 三级降级。"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# resolve_default 三级优先
# ---------------------------------------------------------------------------

def _stub_yaml_store(doc: dict):
    fake = MagicMock()
    fake.doc = doc
    return fake


def test_resolve_default_priority_1_capability_defaults():
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
        return_value=_stub_yaml_store(doc),
    ):
        mid, plat = embedder.resolve_default()
    assert mid == "jinaai/jina-clip-v1"
    assert plat == "infinity-local"


def test_resolve_default_priority_2_settings_image_embedder(monkeypatch):
    """优先 1 缺失时,读 Settings.kb_settings.IMAGE_EMBEDDER。"""
    from chayuan.server.image_source import embedder

    fake_settings = MagicMock()
    fake_settings.kb_settings.IMAGE_EMBEDDER = "google/siglip-base-patch16-224"
    monkeypatch.delenv("CHAYUAN_IMAGE_EMBEDDER", raising=False)

    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        return_value=_stub_yaml_store({}),
    ), patch("chayuan.settings.Settings", fake_settings):
        mid, plat = embedder.resolve_default()
    assert mid == "google/siglip-base-patch16-224"
    assert plat is None  # Settings 来源 platform 未知


def test_resolve_default_priority_2_env_var(monkeypatch):
    """优先 1/Settings 都缺时,读 CHAYUAN_IMAGE_EMBEDDER 环境变量。"""
    from chayuan.server.image_source import embedder

    fake_settings = MagicMock()
    fake_settings.kb_settings.IMAGE_EMBEDDER = ""
    monkeypatch.setenv("CHAYUAN_IMAGE_EMBEDDER", "OFA-Sys/chinese-clip-vit-base-patch16")

    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        return_value=_stub_yaml_store({}),
    ), patch("chayuan.settings.Settings", fake_settings):
        mid, plat = embedder.resolve_default()
    assert mid == "OFA-Sys/chinese-clip-vit-base-patch16"
    assert plat is None


def test_resolve_default_priority_3_fallback(monkeypatch):
    """三级都没 → siglip2 兜底。"""
    from chayuan.server.image_source import embedder

    fake_settings = MagicMock()
    fake_settings.kb_settings.IMAGE_EMBEDDER = ""
    monkeypatch.delenv("CHAYUAN_IMAGE_EMBEDDER", raising=False)

    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        return_value=_stub_yaml_store({}),
    ), patch("chayuan.settings.Settings", fake_settings):
        mid, plat = embedder.resolve_default()
    assert mid == "google/siglip2-base-patch16-224"
    assert plat is None


def test_resolve_default_yaml_failure_falls_back():
    """yaml_store 抛异常 → 不破坏,继续走优先 2。"""
    from chayuan.server.image_source import embedder

    fake_settings = MagicMock()
    fake_settings.kb_settings.IMAGE_EMBEDDER = "x/y"

    with patch(
        "chayuan.server.config_panel.yaml_store.load_yaml",
        side_effect=FileNotFoundError("model_settings.yaml not found"),
    ), patch("chayuan.settings.Settings", fake_settings):
        mid, _ = embedder.resolve_default()
    assert mid == "x/y"


def test_default_model_name_returns_only_first_field():
    """旧入口仍返字符串。"""
    from chayuan.server.image_source import embedder
    with patch.object(embedder, "resolve_default",
                      return_value=("a/b", "infinity-x")):
        assert embedder.default_model_name() == "a/b"


# ---------------------------------------------------------------------------
# is_infinity_platform
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plat,expected", [
    ("infinity", True),
    ("infinity-local", True),
    ("infinity-prod", True),
    ("INFINITY", True),
    ("Infinity-x", True),
    ("openai", False),
    ("deepseek", False),
    ("", False),
    (None, False),
])
def test_is_infinity_platform(plat, expected):
    from chayuan.server.image_source.embedder import is_infinity_platform
    assert is_infinity_platform(plat) is expected


# ---------------------------------------------------------------------------
# pick_client dispatch
# ---------------------------------------------------------------------------

def test_pick_client_infinity_returns_http_client():
    """platform=infinity → InfinityHttpClient。"""
    from chayuan.server.image_source.embedder import pick_client
    from chayuan.server.image_source.embedder_clients.infinity_http import (
        InfinityHttpClient,
    )
    cli = pick_client("jinaai/jina-clip-v1", "infinity-local")
    assert isinstance(cli, InfinityHttpClient)
    assert cli.model_id == "jinaai/jina-clip-v1"
    cli.close()


def test_pick_client_no_platform_returns_inproc():
    """platform=None → InProcEmbedderClient。"""
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.inproc import (
        InProcEmbedderClient,
    )

    fake_spec = MagicMock()
    fake_spec.dim = 4
    fake_emb = MagicMock()
    fake_emb.is_available = MagicMock(return_value=True)
    fake_emb.embed_image = MagicMock(return_value=[0.0] * 4)

    with patch("chayuan.server.image_source.loaders.create_embedder",
               lambda spec: fake_emb), \
         patch.object(embedder, "SUPPORTED_MODELS", {"x/m": fake_spec}), \
         patch.object(embedder, "default_model_name", return_value="x/m"):
        cli = embedder.pick_client("x/m", None)
        assert isinstance(cli, InProcEmbedderClient)


# ---------------------------------------------------------------------------
# get_client 三级降级
# ---------------------------------------------------------------------------

def test_get_client_returns_cached_if_healthy():
    """缓存命中且 healthy → 直接返。"""
    from chayuan.server.image_source import embedder

    embedder._invalidate_client_cache(None)  # 干净起点

    fake = MagicMock()
    fake.healthcheck = MagicMock(return_value=True)
    fake.kind = "infinity"
    fake.model_id = "j/c"
    fake.close = MagicMock()

    with patch.object(embedder, "resolve_default",
                      return_value=("j/c", "infinity-local")), \
         patch.object(embedder, "pick_client", return_value=fake):
        cli1 = embedder.get_client()
        cli2 = embedder.get_client()
    assert cli1 is cli2  # 缓存复用
    embedder._invalidate_client_cache(None)


def test_get_client_falls_back_to_inproc_when_infinity_dies():
    """primary=Infinity 失败 → 自动降级 inproc。"""
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    embedder._invalidate_client_cache(None)

    fake_inproc = MagicMock()
    fake_inproc.healthcheck = MagicMock(return_value=True)
    fake_inproc.kind = "inproc"
    fake_inproc.model_id = "j/c"
    fake_inproc.close = MagicMock()

    def _pick(model_id, platform):
        if platform and platform.startswith("infinity"):
            raise EmbedderUnavailable("Infinity unreachable")
        return fake_inproc

    with patch.object(embedder, "resolve_default",
                      return_value=("j/c", "infinity-local")), \
         patch.object(embedder, "pick_client", side_effect=_pick), \
         patch("chayuan.server.image_source.embedder_clients.inproc."
               "InProcEmbedderClient", return_value=fake_inproc):
        cli = embedder.get_client()
    assert cli.kind == "inproc"
    embedder._invalidate_client_cache(None)


def test_get_client_raises_when_both_paths_fail():
    """primary + fallback 都失败 → 抛 EmbedderUnavailable。"""
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    embedder._invalidate_client_cache(None)

    with patch.object(embedder, "resolve_default",
                      return_value=("ghost/x", "infinity-local")), \
         patch.object(embedder, "pick_client",
                      side_effect=EmbedderUnavailable("primary down")), \
         patch("chayuan.server.image_source.embedder_clients.inproc."
               "InProcEmbedderClient",
               side_effect=EmbedderUnavailable("inproc also down")):
        with pytest.raises(EmbedderUnavailable):
            embedder.get_client()
    embedder._invalidate_client_cache(None)


def test_get_client_no_fallback_when_primary_already_inproc():
    """platform=None 时 primary 就是 inproc,失败直接抛(不二次 fallback)。"""
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    embedder._invalidate_client_cache(None)

    with patch.object(embedder, "resolve_default",
                      return_value=("ghost/x", None)), \
         patch.object(embedder, "pick_client",
                      side_effect=EmbedderUnavailable("inproc down")):
        with pytest.raises(EmbedderUnavailable):
            embedder.get_client()
    embedder._invalidate_client_cache(None)


def test_invalidate_client_cache_by_model_id():
    """按 model_id 清空特定缓存项。"""
    from chayuan.server.image_source import embedder

    embedder._invalidate_client_cache(None)
    fake1 = MagicMock(); fake1.close = MagicMock()
    fake2 = MagicMock(); fake2.close = MagicMock()
    embedder._CLIENT_CACHE["infinity-local::a"] = fake1
    embedder._CLIENT_CACHE["inproc::b"] = fake2

    embedder._invalidate_client_cache("a")
    assert "infinity-local::a" not in embedder._CLIENT_CACHE
    assert "inproc::b" in embedder._CLIENT_CACHE
    fake1.close.assert_called_once()
    embedder._invalidate_client_cache(None)


def test_invalidate_client_cache_all():
    """不传 model_id → 清全部。"""
    from chayuan.server.image_source import embedder

    embedder._CLIENT_CACHE["k1"] = MagicMock(close=MagicMock())
    embedder._CLIENT_CACHE["k2"] = MagicMock(close=MagicMock())
    embedder._invalidate_client_cache(None)
    assert embedder._CLIENT_CACHE == {}
