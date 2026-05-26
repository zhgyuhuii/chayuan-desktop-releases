"""89-11:_suggest_image_embedder + 422 deeplink。"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_suggest_uses_resolve_default_when_available():
    """优先用 resolve_default 的结果。"""
    from chayuan.server.api_server import image_routes as mod

    with patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("jinaai/jina-clip-v1", "infinity-local")):
        out = mod._suggest_image_embedder()
    assert out["repo"] == "jinaai/jina-clip-v1"
    assert out["runtime"] == "infinity"
    assert out["capability"] == "image-embedding"
    assert "marketplace_deeplink" in out


def test_suggest_falls_back_to_settings_image_embedder():
    """resolve_default 抛 → 走 Settings.kb_settings.IMAGE_EMBEDDER。"""
    from chayuan.server.api_server import image_routes as mod
    from unittest.mock import MagicMock

    fake_settings = MagicMock()
    fake_settings.kb_settings.IMAGE_EMBEDDER = "google/siglip-base-patch16-224"

    with patch("chayuan.server.image_source.embedder.resolve_default",
               side_effect=RuntimeError("yaml not found")), \
         patch("chayuan.settings.Settings", fake_settings):
        out = mod._suggest_image_embedder()
    assert out["repo"] == "google/siglip-base-patch16-224"


def test_suggest_uses_built_in_jina_clip_when_all_empty():
    """三级都空 → jina-clip-v1 兜底。"""
    from chayuan.server.api_server import image_routes as mod
    from unittest.mock import MagicMock

    fake_settings = MagicMock()
    fake_settings.kb_settings.IMAGE_EMBEDDER = ""

    with patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("", None)), \
         patch("chayuan.settings.Settings", fake_settings):
        out = mod._suggest_image_embedder()
    assert out["repo"] == "jinaai/jina-clip-v1"


def test_suggest_runtime_is_infinity_not_hf_cache():
    """89-13 后 runtime 默认 infinity,不再是 hf-cache。"""
    from chayuan.server.api_server import image_routes as mod
    with patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("x/y", None)):
        out = mod._suggest_image_embedder()
    assert out["runtime"] == "infinity"


def test_suggest_deeplink_includes_capability_model_and_runtime():
    """deeplink 必须带 ?capability=image-embedding&model=...&runtime=infinity。"""
    from chayuan.server.api_server import image_routes as mod
    with patch("chayuan.server.image_source.embedder.resolve_default",
               return_value=("BAAI/bge-vl", "infinity-local")):
        out = mod._suggest_image_embedder()
    link = out["marketplace_deeplink"]
    assert "capability=image-embedding" in link
    assert "model=BAAI/bge-vl" in link
    assert "runtime=infinity" in link
    assert link.startswith("/admin")
