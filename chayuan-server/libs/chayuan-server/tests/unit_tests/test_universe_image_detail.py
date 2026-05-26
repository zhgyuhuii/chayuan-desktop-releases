"""universe detail image 子节点应返 state/progress/has_text_vector 字段。"""
from __future__ import annotations

import tempfile
import pytest


@pytest.fixture(autouse=True)
def _tmp_root(monkeypatch):
    d = tempfile.mkdtemp(prefix="chayuan_test_univ_")
    monkeypatch.setenv("CHAYUAN_ROOT", d)
    from chayuan.server.image_source import store as s
    s._STORES.clear()
    yield d


def test_universe_detail_returns_state_fields(monkeypatch):
    from chayuan.server.image_source.store import get_store
    s = get_store("univ_kb")
    s.insert_placeholder(
        item_id="img_u1", filename="x.png", mime_type="image/png",
        size_bytes=1, path="/tmp/x",
    )
    s.update("img_u1", state="ocr_and_embedding", progress=42)

    from chayuan.server.api_server import knowledge_universe_routes as kur
    monkeypatch.setattr(kur, "_resolve_store_name_for_image",
                        lambda raw: "univ_kb")

    payload = {"kind": "image", "sub_kind": "image", "ku_id": "src:7", "meta": {}}
    items = kur._image_items_for_universe(7, payload)
    assert items
    it = next(i for i in items if i["id"] == "img_u1")
    assert it["state"] == "ocr_and_embedding"
    assert it["progress"] == 42
    assert "has_text_vector" in it
