"""upload 端点改造:同步插占位,BackgroundTasks 异步处理。"""
from __future__ import annotations

import tempfile
import pytest
from fastapi import BackgroundTasks


@pytest.fixture(autouse=True)
def _tmp_root(monkeypatch):
    d = tempfile.mkdtemp(prefix="chayuan_test_upload_")
    monkeypatch.setenv("CHAYUAN_ROOT", d)
    from chayuan.server.image_source import store as s
    s._STORES.clear()
    yield d


class _FakeFile:
    def __init__(self, name="a.png", content=b"\x89PNG-fake", content_type="image/png"):
        self.filename = name
        self.content_type = content_type
        self._content = content
    async def read(self):
        return self._content


def _stub_source(monkeypatch, source_id=42, store_name="kb1"):
    from chayuan.server.api_server import image_routes as ir
    monkeypatch.setattr(ir, "_get_source_or_404",
                        lambda sid: {"id": sid, "kind": "image", "name": store_name})
    monkeypatch.setattr(ir, "can_write", lambda user, sid: True)
    monkeypatch.setattr(ir, "can_read", lambda user, sid: True)
    class _Conn:
        source_name = store_name
        _source_name = store_name
        _model_name = "clip-test"
        def add_image(self, *a, **kw): raise NotImplementedError
    monkeypatch.setattr(ir, "_build_image_connector", lambda sid: _Conn())
    monkeypatch.setattr(ir, "_resolve_store_name", lambda sid: store_name)


@pytest.mark.asyncio
async def test_upload_returns_queued_items_synchronously(monkeypatch):
    from chayuan.server.api_server import image_routes as ir
    _stub_source(monkeypatch)

    scheduled = []
    class _BG:
        def add_task(self, fn, *a, **kw):
            scheduled.append((fn, a, kw))

    resp = await ir.upload_image_endpoint(
        source_id=42, files=[_FakeFile("a.png"), _FakeFile("b.png", content=b"BBB")],
        tags="", background_tasks=_BG(), user={"id": 1, "role": "admin"},
    )
    items = resp["data"]["added"]
    assert len(items) == 2
    for it in items:
        assert it["state"] == "queued"
        assert it["progress"] == 0
        assert it["image_id"]  # 已分配
    assert len(scheduled) == 2  # 每张图一个后台任务


@pytest.mark.asyncio
async def test_upload_dedup_same_content(monkeypatch):
    from chayuan.server.api_server import image_routes as ir
    _stub_source(monkeypatch)

    scheduled = []
    class _BG:
        def add_task(self, fn, *a, **kw):
            scheduled.append((fn, a, kw))

    same = _FakeFile("a.png", content=b"AAA")
    same2 = _FakeFile("dup.png", content=b"AAA")
    resp = await ir.upload_image_endpoint(
        source_id=42, files=[same, same2],
        tags="", background_tasks=_BG(), user={"id": 1, "role": "admin"},
    )
    items = resp["data"]["added"]
    # 同 hash 去重:返两条但 image_id 相同;且后台只调度一次
    assert items[0]["image_id"] == items[1]["image_id"]
    assert len(scheduled) == 1


def test_status_endpoint_returns_ready_fields(monkeypatch):
    from chayuan.server.api_server import image_routes as ir
    _stub_source(monkeypatch)
    from chayuan.server.image_source.store import get_store
    store = get_store("kb1")
    store.insert_placeholder(
        item_id="img_x", filename="x.png", mime_type="image/png",
        size_bytes=1, path="/tmp/x",
    )
    store.update("img_x", state="ready", progress=100,
                  has_text_vector=True, ocr_text="hello world")

    resp = ir.item_status_endpoint(
        source_id=42, image_id="img_x", user={"id": 1, "role": "admin"},
    )
    data = resp["data"]
    assert data["state"] == "ready"
    assert data["progress"] == 100
    assert data["has_text_vector"] is True
    assert data["ocr_text"] == "hello world"


def test_status_endpoint_404(monkeypatch):
    from chayuan.server.api_server import image_routes as ir
    from fastapi import HTTPException
    _stub_source(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        ir.item_status_endpoint(
            source_id=42, image_id="img_nope", user={"id": 1, "role": "admin"},
        )
    assert exc.value.status_code == 404
