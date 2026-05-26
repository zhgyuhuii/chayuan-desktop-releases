"""94-2:infinity_inventory 解析 + capability_grouped 真实 inventory 集成。"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _parse_record 字段兼容
# ---------------------------------------------------------------------------

def test_parse_record_capabilities_field():
    """0.0.75+ 用 ``capabilities``: list。"""
    from chayuan.server.config_panel.infinity_inventory import _parse_record

    m = _parse_record({
        "id": "jinaai/jina-clip-v1",
        "capabilities": ["embed", "image_embed"],
        "owned_by": "jina",
    })
    assert m is not None
    assert m.model_id == "jinaai/jina-clip-v1"
    assert "clip" in m.capabilities
    assert "embedding" in m.capabilities
    assert m.owned_by == "jina"


def test_parse_record_task_field():
    """老版本可能用 ``task``: str。"""
    from chayuan.server.config_panel.infinity_inventory import _parse_record

    m = _parse_record({
        "id": "BAAI/bge-m3",
        "task": "embedding",
    })
    assert m is not None
    assert m.capabilities == ("embedding",)


def test_parse_record_no_capability_falls_back_to_name():
    """字段全缺 → 名字关键词推断。"""
    from chayuan.server.config_panel.infinity_inventory import _parse_record

    m = _parse_record({"id": "google/siglip2-base-patch16-224"})
    assert m is not None
    assert "clip" in m.capabilities


def test_parse_record_rerank_keyword():
    from chayuan.server.config_panel.infinity_inventory import _parse_record
    m = _parse_record({"id": "BAAI/bge-reranker-v2-m3"})
    assert m is not None
    assert "rerank" in m.capabilities
    # bge-reranker 不应同时被归为 embedding
    assert "embedding" not in m.capabilities


def test_parse_record_empty_id_returns_none():
    from chayuan.server.config_panel.infinity_inventory import _parse_record
    assert _parse_record({"id": ""}) is None
    assert _parse_record({}) is None
    assert _parse_record(None) is None


def test_parse_record_unknown_capability_value_falls_back():
    """capability 是没识别的值(如 'classify')→ 走名字推断。"""
    from chayuan.server.config_panel.infinity_inventory import _parse_record
    m = _parse_record({
        "id": "openai/clip-vit-base-patch32",
        "capabilities": ["classify"],   # 不在 _INFINITY_CAP_NORMALIZE
    })
    assert m is not None
    assert "clip" in m.capabilities  # 名字带 clip


# ---------------------------------------------------------------------------
# fetch_infinity_models HTTP + 缓存
# ---------------------------------------------------------------------------

def _stub_httpx_response(json_data, status=200):
    fake_resp = MagicMock()
    fake_resp.status_code = status
    fake_resp.json = MagicMock(return_value=json_data)
    fake_resp.text = ""
    fake_httpx = MagicMock()
    fake_httpx.get = MagicMock(return_value=fake_resp)
    return fake_httpx


def test_fetch_infinity_models_parses_response():
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    fake = _stub_httpx_response({
        "data": [
            {"id": "jinaai/jina-clip-v1", "capabilities": ["embed", "image_embed"]},
            {"id": "BAAI/bge-m3", "capabilities": ["embed"]},
            {"id": "BAAI/bge-reranker-v2-m3", "capabilities": ["rerank"]},
        ],
    })
    with patch.dict(sys.modules, {"httpx": fake}):
        models = mod.fetch_infinity_models("http://127.0.0.1:7997")
    assert len(models) == 3
    ids = {m.model_id for m in models}
    assert ids == {"jinaai/jina-clip-v1", "BAAI/bge-m3", "BAAI/bge-reranker-v2-m3"}


def test_fetch_infinity_models_caches_within_ttl():
    """5s 内重复调用不发起新 HTTP。"""
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    fake = _stub_httpx_response({"data": [{"id": "x/y"}]})
    with patch.dict(sys.modules, {"httpx": fake}):
        mod.fetch_infinity_models("http://x")
        mod.fetch_infinity_models("http://x")
        mod.fetch_infinity_models("http://x")
    assert fake.get.call_count == 1
    mod.invalidate_inventory_cache()


def test_fetch_infinity_models_cache_per_url():
    """不同 base_url 各自缓存。"""
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    fake = _stub_httpx_response({"data": []})
    with patch.dict(sys.modules, {"httpx": fake}):
        mod.fetch_infinity_models("http://a:7997")
        mod.fetch_infinity_models("http://b:7997")
    assert fake.get.call_count == 2
    mod.invalidate_inventory_cache()


def test_fetch_infinity_models_invalidate_forces_re_fetch():
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    fake = _stub_httpx_response({"data": []})
    with patch.dict(sys.modules, {"httpx": fake}):
        mod.fetch_infinity_models("http://x")
        mod.invalidate_inventory_cache("http://x")
        mod.fetch_infinity_models("http://x")
    assert fake.get.call_count == 2


def test_fetch_infinity_models_returns_empty_on_5xx():
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    fake = _stub_httpx_response({"data": []}, status=503)
    with patch.dict(sys.modules, {"httpx": fake}):
        out = mod.fetch_infinity_models("http://x")
    assert out == []


def test_fetch_infinity_models_returns_empty_on_connection_error():
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()
    fake_httpx = MagicMock()
    fake_httpx.get = MagicMock(side_effect=ConnectionError("refused"))
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        assert mod.fetch_infinity_models("http://x") == []


def test_fetch_infinity_models_empty_url_returns_empty():
    from chayuan.server.config_panel import infinity_inventory as mod
    assert mod.fetch_infinity_models("") == []
    assert mod.fetch_infinity_models("   ") == []


def test_fetch_infinity_models_falls_back_to_no_v1_endpoint():
    """94-2:用户的 Infinity 没有 ``/v1`` 前缀(直接 ``/models``)→ 自动 fallback。"""
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    calls = []
    def _fake_get(url, timeout=None):
        calls.append(url)
        resp = MagicMock()
        if url.endswith("/v1/models"):
            resp.status_code = 404
            resp.text = "not found"
            return resp
        if url.endswith("/models"):
            resp.status_code = 200
            resp.json = MagicMock(return_value={
                "data": [{"id": "jinaai/jina-clip-v1", "task": "image-embedding"}]
            })
            return resp
        resp.status_code = 500
        return resp

    fake_httpx = MagicMock()
    fake_httpx.get = _fake_get
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        models = mod.fetch_infinity_models("http://127.0.0.1:7997")
    assert len(models) == 1
    assert models[0].model_id == "jinaai/jina-clip-v1"
    # 确认两个 endpoint 都试过
    assert any(c.endswith("/v1/models") for c in calls)
    assert any(c.endswith("/models") for c in calls)
    mod.invalidate_inventory_cache()


def test_fetch_infinity_models_uses_v1_when_both_available():
    """``/v1/models`` 在前 → 它通就直接用,不再试 ``/models``。"""
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    calls = []
    def _fake_get(url, timeout=None):
        calls.append(url)
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value={
            "data": [{"id": "x/y", "capabilities": ["embed"]}]
        })
        return resp
    fake_httpx = MagicMock()
    fake_httpx.get = _fake_get
    with patch.dict(sys.modules, {"httpx": fake_httpx}):
        mod.fetch_infinity_models("http://x:7997")
    # 只调了一次,且是 /v1/models
    assert len(calls) == 1
    assert calls[0].endswith("/v1/models")
    mod.invalidate_inventory_cache()


def test_fetch_infinity_models_handles_bare_list_response():
    """有的版本 ``/models`` 直接返 ``[{...}, ...]`` 而非 ``{"data": [...]}``。"""
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    fake = _stub_httpx_response(
        [{"id": "a/b", "capabilities": ["embed"]}]
    )
    with patch.dict(sys.modules, {"httpx": fake}):
        models = mod.fetch_infinity_models("http://x")
    assert len(models) == 1
    assert models[0].model_id == "a/b"
    mod.invalidate_inventory_cache()


def test_fetch_infinity_models_handles_models_top_level_key():
    """有的版本响应顶层是 ``{"models": [...]}`` 而非 ``data``。"""
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    fake = _stub_httpx_response(
        {"models": [{"id": "a/b", "capabilities": ["rerank"]}]}
    )
    with patch.dict(sys.modules, {"httpx": fake}):
        models = mod.fetch_infinity_models("http://x")
    assert len(models) == 1
    assert "rerank" in models[0].capabilities
    mod.invalidate_inventory_cache()


# ---------------------------------------------------------------------------
# get_infinity_models_by_capability
# ---------------------------------------------------------------------------

def test_get_models_by_capability_groups_correctly():
    from chayuan.server.config_panel import infinity_inventory as mod
    mod.invalidate_inventory_cache()

    fake = _stub_httpx_response({
        "data": [
            {"id": "jinaai/jina-clip-v1", "capabilities": ["embed", "image_embed"]},
            {"id": "BAAI/bge-m3", "capabilities": ["embed"]},
            {"id": "BAAI/bge-reranker-v2-m3", "capabilities": ["rerank"]},
        ],
    })
    with patch.dict(sys.modules, {"httpx": fake}):
        by_cap = mod.get_infinity_models_by_capability("http://x")

    clip_ids = {m.model_id for m in by_cap["clip"]}
    embed_ids = {m.model_id for m in by_cap["embedding"]}
    rerank_ids = {m.model_id for m in by_cap["rerank"]}
    assert "jinaai/jina-clip-v1" in clip_ids
    # jina-clip 同时有 embed + image_embed,所以 embedding 里也有
    assert "jinaai/jina-clip-v1" in embed_ids
    assert "BAAI/bge-m3" in embed_ids
    assert "BAAI/bge-m3" not in clip_ids
    assert "BAAI/bge-reranker-v2-m3" in rerank_ids


# ---------------------------------------------------------------------------
# _add_infinity_inventory 集成 _capability_grouped
# ---------------------------------------------------------------------------

def test_add_infinity_inventory_external_priority():
    """配了外置 endpoint → 用外置 url 拉模型。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    out = {"chat": {}, "embedding": {}, "clip": {}, "rerank": {}}

    fake_models_by_cap = {
        "clip": [MagicMock(model_id="jinaai/jina-clip-v1")],
        "embedding": [MagicMock(model_id="BAAI/bge-m3")],
        "rerank": [],
    }

    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value={"url": "http://10.0.0.5:7997", "enabled": True},
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.is_local_infinity_running",
        return_value=False,
    ), patch(
        "chayuan.server.config_panel.infinity_inventory.get_infinity_models_by_capability",
        return_value=fake_models_by_cap,
    ):
        mod._add_infinity_inventory(out)

    # 找含"外置 · Infinity"的 group label
    clip_groups = out["clip"]
    assert any(
        "外置 · Infinity" in label
        for label in clip_groups
    )
    # 且包含 jina-clip-v1
    found = False
    for label, items in clip_groups.items():
        for mid, _ in items:
            if mid == "jinaai/jina-clip-v1":
                found = True
    assert found


def test_add_infinity_inventory_local_pip_fallback():
    """没配外置但本地 pip running → 用本地 url 拉模型,group label = '本地 · Infinity'。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    out = {"chat": {}, "embedding": {}, "clip": {}, "rerank": {}}

    fake_models = {
        "clip": [MagicMock(model_id="jinaai/jina-clip-v1")],
        "embedding": [], "rerank": [],
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
        mod._add_infinity_inventory(out)

    assert "本地 · Infinity" in out["clip"]


def test_add_infinity_inventory_no_runtime_running_no_groups():
    """两条路径都不可达 → out 不变。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod

    out = {"clip": {}}
    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value=None,
    ), patch(
        "chayuan.server.config_panel.local_infinity_pip.is_local_infinity_running",
        return_value=False,
    ):
        mod._add_infinity_inventory(out)
    assert out == {"clip": {}}


def test_lookup_platform_returns_external_when_external_loaded():
    """94-2:外置 Infinity 加载了某 model → platform=infinity-external。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    fake_model = MagicMock(model_id="jinaai/jina-clip-v1")

    with patch(
        "chayuan.server.utils.get_config_models", return_value={},
    ), patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value={"url": "http://10.0.0.5:7997", "enabled": True},
    ), patch(
        "chayuan.server.config_panel.infinity_inventory.fetch_infinity_models",
        return_value=[fake_model],
    ):
        plat = mod._lookup_platform_for_model("jinaai/jina-clip-v1", "image2text")
    assert plat == "infinity-external"


def test_lookup_platform_returns_local_pip_when_only_pip_loaded():
    from chayuan.server.config_panel import runtime_framework_panel as mod
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


def test_infinity_base_url_external_platform_uses_external_url():
    """94-2:platform=infinity-external → base_url 用 external_runtimes 的 url。"""
    from chayuan.server.image_source import embedder

    with patch(
        "chayuan.server.config_panel.external_runtimes.get_external_runtime",
        return_value={"url": "http://gpu-server:7997", "enabled": True},
    ):
        url = embedder._infinity_base_url("infinity-external")
    assert url == "http://gpu-server:7997"
