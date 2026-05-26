"""93-3:_capability_grouped 在本地 pip Infinity running 时给 clip 加候选。"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_capability_grouped_adds_local_pip_clip_models():
    """本地 pip running + Infinity /v1/models 列了 jina-clip → clip 组多一项。

    94-2:由 hf-cache 关键词推断改为直接调 Infinity /v1/models 拉真实清单。
    """
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from unittest.mock import MagicMock

    fake_models = {
        "clip": [
            MagicMock(model_id="jinaai/jina-clip-v1"),
            MagicMock(model_id="google/siglip-base-patch16-224"),
        ],
        "embedding": [],
        "rerank": [],
    }
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value=None,
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.is_local_infinity_running",
        return_value=True,
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip._local_infinity_url",
        return_value="http://127.0.0.1:7997",
    ), patch(
        "chayuan.server.config_panel.infinity_inventory.get_infinity_models_by_capability",
        return_value=fake_models,
    ):
        grouped = mod._capability_grouped()

    clip_groups = grouped.get("clip", {})
    local_grp = clip_groups.get("本地 · Infinity")
    assert local_grp is not None
    ids = {mid for mid, _ in local_grp}
    assert "jinaai/jina-clip-v1" in ids
    assert "google/siglip-base-patch16-224" in ids


def test_capability_grouped_local_pip_routes_bge_m3_to_embedding():
    """bge-m3 是 text-embedding,应该出现在 embedding 而非 clip。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from unittest.mock import MagicMock

    fake_models = {
        "clip": [],
        "embedding": [MagicMock(model_id="BAAI/bge-m3")],
        "rerank": [MagicMock(model_id="BAAI/bge-reranker-v2-m3")],
    }
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value=None,
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.is_local_infinity_running",
        return_value=True,
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip._local_infinity_url",
        return_value="http://127.0.0.1:7997",
    ), patch(
        "chayuan.server.config_panel.infinity_inventory.get_infinity_models_by_capability",
        return_value=fake_models,
    ):
        grouped = mod._capability_grouped()

    embed_local = grouped.get("embedding", {}).get("本地 · Infinity") or []
    rerank_local = grouped.get("rerank", {}).get("本地 · Infinity") or []
    assert "BAAI/bge-m3" in {mid for mid, _ in embed_local}
    assert "BAAI/bge-reranker-v2-m3" in {mid for mid, _ in rerank_local}


def test_capability_grouped_no_local_pip_when_not_running():
    """没外置 + 本地 pip 没起 → 不加 Infinity 分组。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value=None,
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.is_local_infinity_running",
        return_value=False,
    ):
        grouped = mod._capability_grouped()
    clip_groups = grouped.get("clip", {})
    assert "本地 · Infinity" not in clip_groups
    assert "本地 · Infinity (pip)" not in clip_groups


def test_capability_grouped_swallows_module_error():
    """local_infinity_pip 模块本身抛 → 不破坏 _capability_grouped。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    with patch(
        "chayuan.server.config_panel.local_infinity_pip.is_local_infinity_running",
        side_effect=RuntimeError("module broken"),
    ):
        grouped = mod._capability_grouped()
    # 没崩
    assert isinstance(grouped, dict)


def test_lookup_platform_returns_infinity_local_pip_for_cached_model():
    """93-3 + 94-2:本地 pip Infinity 加载了某 model → platform=infinity-local-pip。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from unittest.mock import MagicMock

    fake_model = MagicMock(model_id="jinaai/jina-clip-v1")
    with patch(
        "chayuan.server.utils.get_config_models", return_value={},
    ), patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value=None,
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.is_local_infinity_running",
        return_value=True,
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip._local_infinity_url",
        return_value="http://127.0.0.1:7997",
    ), patch(
        "chayuan.server.config_panel.infinity_inventory.fetch_infinity_models",
        return_value=[fake_model],
    ):
        plat = mod._lookup_platform_for_model("jinaai/jina-clip-v1", "image2text")
    assert plat == "infinity-local-pip"


def test_lookup_platform_returns_none_when_pip_not_running():
    """本地 pip 没在跑 → 不返 infinity-local-pip,回到 None。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    with patch(
        "chayuan.server.utils.get_config_models", return_value={},
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.is_local_infinity_running",
        return_value=False,
    ):
        plat = mod._lookup_platform_for_model("jinaai/jina-clip-v1", "image2text")
    assert plat is None


def test_infinity_base_url_local_pip_uses_local_port(monkeypatch):
    """93-3:platform=infinity-local-pip → base_url 用本地默认 7997。"""
    from chayuan.server.image_source import embedder
    monkeypatch.delenv("CHAYUAN_LOCAL_INFINITY_PORT", raising=False)
    url = embedder._infinity_base_url("infinity-local-pip")
    assert url == "http://127.0.0.1:7997"


def test_infinity_base_url_local_pip_respects_env_port(monkeypatch):
    from chayuan.server.image_source import embedder
    monkeypatch.setenv("CHAYUAN_LOCAL_INFINITY_PORT", "9001")
    url = embedder._infinity_base_url("infinity-local-pip")
    assert url == "http://127.0.0.1:9001"


def test_infinity_base_url_docker_path_unaffected_by_local_pip():
    """platform=infinity-local(docker)→ 仍走 external / 默认 37997。"""
    from chayuan.server.image_source import embedder
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value=None,
    ):
        url = embedder._infinity_base_url("infinity-local")
    assert "37997" in url or "127.0.0.1" in url
