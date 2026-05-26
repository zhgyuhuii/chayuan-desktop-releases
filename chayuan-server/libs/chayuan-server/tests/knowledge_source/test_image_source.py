"""图像知识源单测（不依赖 torch / transformers；用 stub embedder）。

覆盖：
- ImageStore：add / remove / search；持久化往返
- ImageConnector：test_connection / introspect / search / search_by_image 路径
- Registry：kind=image 被正确路由到 ImageConnector
- 模型清单与磁盘占用 API
"""
from __future__ import annotations

import os
from typing import Any, List

import numpy as np
import pytest

from chayuan.server.image_source.store import ImageStore
from chayuan.server.image_source.connector import ImageConnector
from chayuan.server.knowledge_source.base import ConnectionSpec
from chayuan.server.knowledge_source.types import NLQuery


# ---------------------------------------------------------------------------
# API 层：kind=image 路径
# ---------------------------------------------------------------------------

def test_create_source_endpoint_accepts_image(ks_db):
    """`/knowledge_source/` 加 kind=image 分支后，API 应 200、落 connection + source
    两张表记录，并复用同一 connection_id。
    """
    from chayuan.server.api_server.knowledge_source_routes import (
        create_source_endpoint,
    )
    from chayuan.server.db.repository.knowledge_source_repository import (
        get_connection, get_source,
    )

    ret = create_source_endpoint(
        name="t_img_src",
        kind="image",
        display_name="图像库",
        description="测试图像源",
        dialect="image", host="", port=0, database="t_img_src",
        username="", password="",
        options={"embedder_model": "google/siglip2-base-patch16-224",
                 "source_name": "t_img_src"},
        allowed={}, visibility="private",
        request=None, user={"id": 1, "role": "admin"},
    )
    assert (ret or {}).get("code") == 0, ret
    data = ret.get("data") or {}
    assert data.get("id"), "source_id 必须落库"
    assert data.get("connection_id"), "connection_id 必须落库"

    # 读回：connection options 应保留 embedder_model
    conn = get_connection(int(data["connection_id"]))
    assert conn is not None
    src = get_source(int(data["id"]))
    assert src is not None
    # get_source 返回 dict
    assert src.get("kind") == "image"
    assert src.get("name") == "t_img_src"


def test_create_source_endpoint_rejects_unknown_kind(ks_db):
    """未知 kind 返回 400（不应静默通过）。"""
    from fastapi import HTTPException
    from chayuan.server.api_server.knowledge_source_routes import (
        create_source_endpoint,
    )
    with pytest.raises(HTTPException) as exc_info:
        create_source_endpoint(
            name="bad", kind="gdrive", display_name="", description="",
            dialect="", host="", port=0, database="", username="", password="",
            options={}, allowed={}, visibility="private",
            request=None, user={"id": 1, "role": "admin"},
        )
    assert exc_info.value.status_code == 400
    assert "image" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# Store 基础
# ---------------------------------------------------------------------------

def test_image_store_add_search_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    # 强制 refresh 单例
    from chayuan.server.image_source import store as _s
    _s.invalidate()
    # 造 3 张 "图"（实际只要 bytes 能落盘即可）
    p1 = tmp_path / "a.bin"; p1.write_bytes(b"AAA")
    p2 = tmp_path / "b.bin"; p2.write_bytes(b"BBB")
    p3 = tmp_path / "c.bin"; p3.write_bytes(b"CCC")

    st = ImageStore("t1")
    id1 = st.add(np.array([1.0, 0.0, 0.0]), path=str(p1))
    id2 = st.add(np.array([0.0, 1.0, 0.0]), path=str(p2))
    id3 = st.add(np.array([0.0, 0.0, 1.0]), path=str(p3))

    assert st.count() == 3
    assert st.dim() == 3

    # cosine 检索：q 与 id1 最近
    hits = st.search(np.array([0.9, 0.1, 0.0]), top_k=2)
    assert len(hits) == 2
    assert hits[0][0]["id"] == id1
    assert hits[0][1] > hits[1][1]

    # 去重：相同 md5 不重复
    dup_id = st.add(np.array([1, 0, 0]), path=str(p1))
    assert dup_id == id1
    assert st.count() == 3

    # 删除
    assert st.remove(id2) is True
    assert st.count() == 2
    assert st.remove("nonexistent") is False


def test_image_store_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    from chayuan.server.image_source import store as _s
    _s.invalidate()
    p = tmp_path / "x.bin"; p.write_bytes(b"hello")
    st = ImageStore("persist")
    id1 = st.add(np.array([0.5, 0.5, 0.7]), path=str(p))
    # 重新实例化（模拟重启）
    st2 = ImageStore("persist")
    assert st2.count() == 1
    assert any(m["id"] == id1 for m in st2._meta)


def test_image_store_dim_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    from chayuan.server.image_source import store as _s
    _s.invalidate()
    p = tmp_path / "x.bin"; p.write_bytes(b"z")
    st = ImageStore("dim_mis")
    st.add(np.array([1, 0, 0, 0]), path=str(p))
    with pytest.raises(ValueError):
        p2 = tmp_path / "y.bin"; p2.write_bytes(b"zz")
        st.add(np.array([1, 0, 0]), path=str(p2))  # 维度不同


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class _StubEmbedder:
    name = "stub"
    dim = 4

    def is_available(self) -> bool:
        return True

    def embed_text(self, text: str) -> np.ndarray:
        # 简单 hash-based 伪向量
        import hashlib
        h = hashlib.md5((text or "").encode()).digest()
        v = np.frombuffer(h[:16], dtype=np.uint8).astype("float32")
        return v / (np.linalg.norm(v) or 1.0)

    def embed_image(self, src: Any) -> np.ndarray:
        # 读文件 bytes hash
        import hashlib
        if isinstance(src, (bytes, bytearray)):
            data = src
        elif hasattr(src, "read"):
            data = src.read()
        else:
            with open(str(src), "rb") as f:
                data = f.read()
        h = hashlib.md5(data).digest()
        v = np.frombuffer(h[:16], dtype=np.uint8).astype("float32")
        return v / (np.linalg.norm(v) or 1.0)


@pytest.fixture
def stub_image_embedder(monkeypatch):
    from chayuan.server.image_source import embedder as _e
    stub = _StubEmbedder()
    # 覆盖 get_embedder：任何 model name 都返回 stub
    monkeypatch.setattr(_e, "get_embedder", lambda name=None: stub)
    # 同时 patch connector 模块里已绑定的符号
    from chayuan.server.image_source import connector as _c
    monkeypatch.setattr(_c, "get_embedder", lambda name=None: stub)
    return stub


def test_connector_test_connection(tmp_path, monkeypatch, stub_image_embedder):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    from chayuan.server.image_source import store as _s
    _s.invalidate()
    spec = ConnectionSpec(dialect="image", database="t_conn",
                          options={"source_name": "t_conn"})
    c = ImageConnector(spec=spec, source_id=1)
    ok, msg = c.test_connection()
    assert ok, msg


def test_connector_add_and_text_search(tmp_path, monkeypatch, stub_image_embedder):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    from chayuan.server.image_source import store as _s
    _s.invalidate()
    spec = ConnectionSpec(dialect="image", database="t_text",
                          options={"source_name": "t_text"})
    c = ImageConnector(spec=spec, source_id=2)

    p1 = tmp_path / "one.bin"; p1.write_bytes(b"one")
    p2 = tmp_path / "two.bin"; p2.write_bytes(b"two")
    c.add_image(str(p1), tags="a")
    c.add_image(str(p2), tags="b")

    import asyncio
    chunks = asyncio.get_event_loop().run_until_complete(
        c.search(NLQuery(query="anything", top_k=2))
    )
    assert len(chunks) == 2
    assert all(ch.source_kind == "image" for ch in chunks)


def test_connector_search_by_image(tmp_path, monkeypatch, stub_image_embedder):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    from chayuan.server.image_source import store as _s
    _s.invalidate()
    spec = ConnectionSpec(dialect="image", database="t_img",
                          options={"source_name": "t_img"})
    c = ImageConnector(spec=spec, source_id=3)
    p1 = tmp_path / "a.bin"; p1.write_bytes(b"AAA")
    p2 = tmp_path / "b.bin"; p2.write_bytes(b"BBB")
    c.add_image(str(p1))
    c.add_image(str(p2))

    # 用 p1 的原文 bytes 搜 → 必须命中自己为 top1（cosine=1.0）
    chunks = c.search_by_image(str(p1), top_k=2)
    assert len(chunks) == 2
    assert chunks[0].score > 0.99
    assert (chunks[0].citation.meta or {}).get("path") == str(p1)


def test_connector_introspect(tmp_path, monkeypatch, stub_image_embedder):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    from chayuan.server.image_source import store as _s
    _s.invalidate()
    spec = ConnectionSpec(dialect="image", database="t_intr",
                          options={"source_name": "t_intr"})
    c = ImageConnector(spec=spec, source_id=4)
    p = tmp_path / "a.bin"; p.write_bytes(b"a")
    c.add_image(str(p))

    snap = c.introspect(sample_rows=2)
    assert snap.source_kind == "image"
    assert snap.tables and snap.tables[0].name.startswith("image_index:")
    assert "1 条" in (snap.tables[0].comment or "")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_image_dialect_routes_to_image_connector():
    from chayuan.server.knowledge_source.registry import get_connector_class
    cls = get_connector_class("image")
    assert cls.__name__ == "ImageConnector"


def test_registry_image_in_all_supported():
    from chayuan.server.knowledge_source.registry import all_supported_dialects
    d = all_supported_dialects()
    assert "image" in d
