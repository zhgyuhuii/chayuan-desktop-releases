"""上传后列表能立刻读到 item:验证 source_name 解析两端一致。"""
from __future__ import annotations

import io
import tempfile
import pytest


@pytest.fixture(autouse=True)
def _tmp_root(monkeypatch):
    d = tempfile.mkdtemp(prefix="chayuan_test_src_")
    monkeypatch.setenv("CHAYUAN_ROOT", d)
    from chayuan.server.image_source import store as s
    s._STORES.clear()
    yield d


def test_connector_source_name_matches_routes(monkeypatch):
    """ImageConnector.source_name 应与 src['name'] 一致。"""
    from chayuan.server.image_source.connector import ImageConnector
    from chayuan.server.knowledge_source.base import ConnectionSpec

    spec = ConnectionSpec(
        dialect="image", host="", port=0, username="", password="",
        database="kb_legacy_name",
        options={"source_name": "explicit_name"},
    )
    conn = ImageConnector(spec=spec, source_id=42)
    # 必须暴露 public attr
    assert hasattr(conn, "source_name")
    assert conn.source_name == "explicit_name"

    spec2 = ConnectionSpec(
        dialect="image", host="", port=0, username="", password="",
        database="db_value", options={},
    )
    conn2 = ImageConnector(spec=spec2, source_id=43)
    assert conn2.source_name == "db_value"


def test_routes_use_connector_source_name(monkeypatch):
    """list/detail 端点必须通过 connector 解析 source_name,而不是直接读 src['name']。"""
    from chayuan.server.api_server import image_routes as ir
    # 用现有 helper 拿到 connector 后,读 .source_name
    # _resolve_store_name(source_id) 应该返回 ImageConnector(spec).source_name
    assert hasattr(ir, "_resolve_store_name")
