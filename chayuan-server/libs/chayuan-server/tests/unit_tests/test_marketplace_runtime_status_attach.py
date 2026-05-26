"""89-7:marketplace _attach_runtime_status 5 态联动。"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def _mk_card(mid: str, capability: str, status: str = "available"):
    from chayuan.server.model_registry.marketplace import MarketplaceCard
    return MarketplaceCard(
        id=mid, vendor="x", capability=capability,
        name=mid, status=status,
    )


# ---------------------------------------------------------------------------
# CardStatus 扩展 + 新字段
# ---------------------------------------------------------------------------

def test_marketplace_card_has_runtime_loaded_field():
    from chayuan.server.model_registry.marketplace import MarketplaceCard
    card = MarketplaceCard(
        id="x", vendor="y", capability="image-embedding",
        name="N", status="ready", runtime_loaded=True,
        runtime_url="http://localhost:37997",
    )
    d = card.to_dict()
    assert d["runtime_loaded"] is True
    assert d["runtime_url"] == "http://localhost:37997"


def test_marketplace_card_default_runtime_loaded_false():
    from chayuan.server.model_registry.marketplace import MarketplaceCard
    card = MarketplaceCard(
        id="x", vendor="y", capability="chat", name="N", status="available",
    )
    assert card.runtime_loaded is False
    assert card.runtime_url is None


# ---------------------------------------------------------------------------
# _attach_runtime_status 决策矩阵
# ---------------------------------------------------------------------------

def test_attach_runtime_marks_loaded_as_ready():
    from chayuan.server.model_registry import marketplace as mp

    by_id = {
        "jinaai/jina-clip-v1": _mk_card(
            "jinaai/jina-clip-v1", "image-embedding", "available",
        ),
    }
    snap = {
        "infinity_loaded": ["jinaai/jina-clip-v1"],
        "infinity_url": "http://127.0.0.1:37997",
        "hf_cache": {"jinaai/jina-clip-v1"},
    }
    with patch.object(mp, "_get_runtime_snapshot", return_value=snap):
        mp._attach_runtime_status(by_id)

    card = by_id["jinaai/jina-clip-v1"]
    assert card.status == "ready"
    assert card.runtime_loaded is True
    assert card.runtime_url == "http://127.0.0.1:37997"


def test_attach_runtime_marks_cache_hit_as_downloaded():
    """hf-cache 命中但 Infinity 没加载 → status=downloaded。"""
    from chayuan.server.model_registry import marketplace as mp

    by_id = {
        "BAAI/bge-m3": _mk_card("BAAI/bge-m3", "text-embedding", "available"),
    }
    snap = {
        "infinity_loaded": [],
        "infinity_url": None,
        "hf_cache": {"BAAI/bge-m3"},
    }
    with patch.object(mp, "_get_runtime_snapshot", return_value=snap):
        mp._attach_runtime_status(by_id)

    card = by_id["BAAI/bge-m3"]
    assert card.status == "downloaded"
    assert card.runtime_loaded is False


def test_attach_runtime_skips_chat_capability():
    """对话模型不归 Infinity 管 → 状态保持。"""
    from chayuan.server.model_registry import marketplace as mp

    by_id = {
        "qwen2.5-7b": _mk_card("qwen2.5-7b", "chat", "available"),
    }
    snap = {
        "infinity_loaded": [],
        "infinity_url": None,
        "hf_cache": {"qwen2.5-7b"},  # 即使在 cache 也不改
    }
    with patch.object(mp, "_get_runtime_snapshot", return_value=snap):
        mp._attach_runtime_status(by_id)

    assert by_id["qwen2.5-7b"].status == "available"


def test_attach_runtime_does_not_override_downloading():
    """lifecycle 在跑 → 不能被改成 downloaded/ready。"""
    from chayuan.server.model_registry import marketplace as mp

    by_id = {
        "x/y": _mk_card("x/y", "image-embedding", "downloading"),
    }
    snap = {
        "infinity_loaded": ["x/y"],
        "infinity_url": "http://x",
        "hf_cache": {"x/y"},
    }
    with patch.object(mp, "_get_runtime_snapshot", return_value=snap):
        mp._attach_runtime_status(by_id)
    assert by_id["x/y"].status == "downloading"


def test_attach_runtime_does_not_override_error():
    """error 状态不被覆盖,让用户看到失败。"""
    from chayuan.server.model_registry import marketplace as mp

    by_id = {
        "x/y": _mk_card("x/y", "image-embedding", "error"),
    }
    snap = {
        "infinity_loaded": ["x/y"],
        "infinity_url": "http://x",
        "hf_cache": {"x/y"},
    }
    with patch.object(mp, "_get_runtime_snapshot", return_value=snap):
        mp._attach_runtime_status(by_id)
    assert by_id["x/y"].status == "error"


def test_attach_runtime_no_change_when_neither_loaded_nor_cached():
    from chayuan.server.model_registry import marketplace as mp

    by_id = {
        "ghost/x": _mk_card("ghost/x", "rerank", "available"),
    }
    snap = {
        "infinity_loaded": [],
        "infinity_url": None,
        "hf_cache": set(),
    }
    with patch.object(mp, "_get_runtime_snapshot", return_value=snap):
        mp._attach_runtime_status(by_id)
    assert by_id["ghost/x"].status == "available"
    assert by_id["ghost/x"].runtime_loaded is False


def test_attach_runtime_handles_rerank():
    """重排模型也走 Infinity。"""
    from chayuan.server.model_registry import marketplace as mp

    by_id = {
        "BAAI/bge-reranker-v2-m3": _mk_card(
            "BAAI/bge-reranker-v2-m3", "rerank", "available",
        ),
    }
    snap = {
        "infinity_loaded": ["BAAI/bge-reranker-v2-m3"],
        "infinity_url": "http://127.0.0.1:37997",
        "hf_cache": set(),
    }
    with patch.object(mp, "_get_runtime_snapshot", return_value=snap):
        mp._attach_runtime_status(by_id)
    assert by_id["BAAI/bge-reranker-v2-m3"].status == "ready"


# ---------------------------------------------------------------------------
# 5 秒缓存
# ---------------------------------------------------------------------------

def test_get_runtime_snapshot_caches_within_ttl(monkeypatch):
    from chayuan.server.model_registry import marketplace as mp
    from unittest.mock import MagicMock

    mp._invalidate_runtime_status_snapshot()

    scan_inf = MagicMock(return_value=[{"model_id": "a/b"}])
    scan_hf = MagicMock(return_value=["a/b"])

    with patch(
        "chayuan.server.api_server.image_routes._scan_infinity_loaded",
        scan_inf,
    ), patch(
        "chayuan.server.api_server.image_routes._scan_hf_cache_present",
        scan_hf,
    ):
        mp._get_runtime_snapshot()
        mp._get_runtime_snapshot()
        mp._get_runtime_snapshot()
    assert scan_inf.call_count == 1
    assert scan_hf.call_count == 1
    mp._invalidate_runtime_status_snapshot()


def test_invalidate_snapshot_forces_re_scan():
    from chayuan.server.model_registry import marketplace as mp
    from unittest.mock import MagicMock

    mp._invalidate_runtime_status_snapshot()
    scan_inf = MagicMock(return_value=[])
    scan_hf = MagicMock(return_value=[])

    with patch(
        "chayuan.server.api_server.image_routes._scan_infinity_loaded",
        scan_inf,
    ), patch(
        "chayuan.server.api_server.image_routes._scan_hf_cache_present",
        scan_hf,
    ):
        mp._get_runtime_snapshot()
        mp._invalidate_runtime_status_snapshot()
        mp._get_runtime_snapshot()
    assert scan_inf.call_count == 2
    mp._invalidate_runtime_status_snapshot()
