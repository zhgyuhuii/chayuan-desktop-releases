"""ImageStore schema 扩展 + 文本向量索引 + 老数据迁移。"""
from __future__ import annotations

import json
import os
import tempfile
import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _tmp_root(monkeypatch):
    d = tempfile.mkdtemp(prefix="chayuan_test_")
    monkeypatch.setenv("CHAYUAN_ROOT", d)
    # 清掉单例缓存
    from chayuan.server.image_source import store as s
    s._STORES.clear()
    yield d


def _new_store(name="kb1"):
    from chayuan.server.image_source.store import ImageStore
    return ImageStore(name)


def test_insert_placeholder_writes_state_queued():
    store = _new_store()
    item = store.insert_placeholder(
        item_id="img_abc", filename="a.png", mime_type="image/png",
        size_bytes=100, path="/tmp/a.png",
    )
    assert item["state"] == "queued"
    assert item["progress"] == 0
    assert item["has_text_vector"] is False
    assert item["ocr_text"] is None


def test_update_state_persists_and_reads_back():
    store = _new_store()
    store.insert_placeholder(
        item_id="img_abc", filename="a.png", mime_type="image/png",
        size_bytes=100, path="/tmp/a.png",
    )
    store.update("img_abc", state="ready", progress=100, ocr_text="hello")
    rec = store.get("img_abc")
    assert rec["state"] == "ready"
    assert rec["progress"] == 100
    assert rec["ocr_text"] == "hello"


def test_add_image_vector_and_search():
    store = _new_store()
    store.insert_placeholder(
        item_id="img_a", filename="a.png", mime_type="image/png",
        size_bytes=1, path="/tmp/a",
    )
    store.add_image_vector("img_a", np.array([1.0, 0.0, 0.0], dtype="float32"))
    hits = store.search_image(np.array([0.99, 0.01, 0.0], dtype="float32"), top_k=5)
    assert len(hits) == 1
    assert hits[0][0]["id"] == "img_a"


def test_add_text_vector_separate_from_image_vector():
    store = _new_store()
    store.insert_placeholder(
        item_id="img_a", filename="a.png", mime_type="image/png",
        size_bytes=1, path="/tmp/a",
    )
    # CLIP 512 维
    store.add_image_vector("img_a", np.ones(512, dtype="float32") / np.sqrt(512))
    # bge-m3 1024 维
    store.add_text_vector("img_a", np.ones(1024, dtype="float32") / np.sqrt(1024))
    rec = store.get("img_a")
    assert rec["has_text_vector"] is True
    text_hits = store.search_text(np.ones(1024, dtype="float32") / np.sqrt(1024), top_k=5)
    assert len(text_hits) == 1
    assert text_hits[0][0]["id"] == "img_a"


def test_remove_clears_both_indices():
    store = _new_store()
    store.insert_placeholder(
        item_id="img_a", filename="a.png", mime_type="image/png",
        size_bytes=1, path="/tmp/a",
    )
    store.add_image_vector("img_a", np.ones(3, dtype="float32"))
    store.add_text_vector("img_a", np.ones(4, dtype="float32"))
    assert store.remove("img_a") is True
    assert store.get("img_a") is None
    assert store.search_image(np.ones(3, dtype="float32"), top_k=5) == []
    assert store.search_text(np.ones(4, dtype="float32"), top_k=5) == []


def test_legacy_metadata_migration(tmp_path, monkeypatch):
    """老 meta.json 没有 state/progress 字段,_load 时填默认 ready。"""
    from chayuan.server.image_source.store import _image_indexes_root
    root = _image_indexes_root() / "legacy_kb"
    root.mkdir(parents=True, exist_ok=True)
    legacy = [{
        "id": "img_legacy_1",
        "path": "/old/a.png",
        "md5": "deadbeef",
        "size_bytes": 100,
        "created_at": 1700000000.0,
    }]
    (root / "meta.json").write_text(json.dumps(legacy), encoding="utf-8")
    # 清单例,强制重新加载
    from chayuan.server.image_source import store as s
    s._STORES.clear()
    store = _new_store("legacy_kb")
    rec = store.get("img_legacy_1")
    assert rec["state"] == "ready"
    assert rec["progress"] == 100
    assert rec["has_text_vector"] is False
    assert rec["ocr_text"] is None
