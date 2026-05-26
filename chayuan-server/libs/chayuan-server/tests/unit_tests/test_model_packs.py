"""90-1:一键装套餐(Lite/Standard/Pro)测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 套餐定义
# ---------------------------------------------------------------------------

def test_three_packs_exist():
    from chayuan.server.model_registry.model_packs import (
        ALL_PACKS, get_pack,
    )
    names = [p.name for p in ALL_PACKS]
    assert names == ["lite", "standard", "pro"]
    for n in names:
        assert get_pack(n) is not None


def test_get_pack_returns_none_for_unknown():
    from chayuan.server.model_registry.model_packs import get_pack
    assert get_pack("xxx") is None
    assert get_pack("") is None
    assert get_pack(None) is None


def test_lite_pack_size_under_3gb():
    from chayuan.server.model_registry.model_packs import LITE
    assert LITE.total_size_mb < 3_000


def test_standard_pack_has_chat_embed_image_rerank():
    from chayuan.server.model_registry.model_packs import STANDARD
    caps = {it.capability for it in STANDARD.items}
    assert caps == {"chat", "text-embedding", "image-embedding", "rerank"}


def test_pro_pack_uses_vllm_for_chat():
    from chayuan.server.model_registry.model_packs import PRO
    chat_items = [it for it in PRO.items if it.capability == "chat"]
    assert len(chat_items) == 1
    assert chat_items[0].runtime == "vllm"


def test_all_image_embedding_runtime_is_infinity():
    from chayuan.server.model_registry.model_packs import ALL_PACKS
    for pack in ALL_PACKS:
        for it in pack.items:
            if it.capability == "image-embedding":
                assert it.runtime == "infinity"


def test_list_packs_returns_dict_form():
    from chayuan.server.model_registry.model_packs import list_packs
    out = list_packs()
    assert isinstance(out, list)
    assert len(out) == 3
    for p in out:
        assert "name" in p
        assert "items" in p
        assert isinstance(p["items"], list)


# ---------------------------------------------------------------------------
# install_pack 触发链路
# ---------------------------------------------------------------------------

def test_install_unknown_pack_returns_error():
    from chayuan.server.model_registry.model_packs import install_pack
    ret = install_pack("ghost")
    assert ret["code"] == 1
    assert "unknown pack" in ret["msg"]


def test_install_pack_calls_lifecycle_start_for_each_item():
    from chayuan.server.model_registry import model_packs as mod

    fake_lc = MagicMock()
    counter = {"i": 0}

    def _start(repo, capability, target_runtime):
        counter["i"] += 1
        return f"task-{counter['i']}"

    fake_lc.start = MagicMock(side_effect=_start)

    with patch("chayuan_modelmgr.get_lifecycle", return_value=fake_lc):
        ret = mod.install_pack("lite")

    assert ret["code"] == 0
    assert len(ret["data"]["tasks"]) == len(mod.LITE.items)
    for t in ret["data"]["tasks"]:
        assert t["ok"] is True
        assert t["task_id"].startswith("task-")
    assert fake_lc.start.call_count == len(mod.LITE.items)


def test_install_pack_handles_individual_failure():
    """某条失败不影响其它条;ok=False + error 字段。"""
    from chayuan.server.model_registry import model_packs as mod

    fake_lc = MagicMock()

    def _start(repo, capability, target_runtime):
        if "bge-reranker" in repo:
            raise RuntimeError("hub not reachable")
        return f"ok-{repo}"

    fake_lc.start = MagicMock(side_effect=_start)

    with patch("chayuan_modelmgr.get_lifecycle", return_value=fake_lc):
        ret = mod.install_pack("standard")

    tasks = ret["data"]["tasks"]
    failed = [t for t in tasks if not t["ok"]]
    succeeded = [t for t in tasks if t["ok"]]
    assert len(failed) == 1
    assert "hub not reachable" in failed[0]["error"]
    assert len(succeeded) == 3


def test_install_pack_returns_error_when_modelmgr_missing(monkeypatch):
    """chayuan_modelmgr 未装 → friendly error。"""
    from chayuan.server.model_registry import model_packs as mod
    import sys as _sys

    # 临时让 import 失败
    real = _sys.modules.pop("chayuan_modelmgr", None)
    try:
        with patch.dict(_sys.modules, {"chayuan_modelmgr": None}):
            ret = mod.install_pack("lite")
        assert ret["code"] == 1
        assert "chayuan_modelmgr" in ret["msg"]
    finally:
        if real is not None:
            _sys.modules["chayuan_modelmgr"] = real


# ---------------------------------------------------------------------------
# 路由层
# ---------------------------------------------------------------------------

def test_list_packs_endpoint_returns_three():
    from chayuan.server.api_server import image_routes as mod
    ret = mod.list_model_packs_endpoint(user={"id": 1, "role": "admin"})
    assert ret["code"] == 0
    assert len(ret["data"]) == 3


def test_install_endpoint_rejects_non_admin():
    from chayuan.server.api_server import image_routes as mod
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        mod.install_model_pack_endpoint(
            "lite", user={"id": 2, "role": "user"},
        )
    assert exc.value.status_code == 403


def test_install_endpoint_404_for_unknown():
    from chayuan.server.api_server import image_routes as mod
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        mod.install_model_pack_endpoint(
            "ghost", user={"id": 1, "role": "admin"},
        )
    assert exc.value.status_code == 404


def test_install_endpoint_allows_guest():
    from chayuan.server.api_server import image_routes as mod

    fake_lc = MagicMock()
    fake_lc.start = MagicMock(return_value="t1")
    with patch("chayuan_modelmgr.get_lifecycle", return_value=fake_lc):
        ret = mod.install_model_pack_endpoint(
            "lite", user={"id": -1, "is_guest": True, "role": "user"},
        )
    assert ret["code"] == 0
