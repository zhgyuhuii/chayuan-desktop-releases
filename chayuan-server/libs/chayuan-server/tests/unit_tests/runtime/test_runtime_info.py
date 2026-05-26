"""RuntimeInfo 持久化 + masked() 测试。"""
from __future__ import annotations

import json
from pathlib import Path

from chayuan.server.runtime.runtime_info import RuntimeInfo, ServiceEndpoint


def test_set_and_get_endpoint_roundtrip(tmp_path: Path):
    p = tmp_path / "runtime.json"
    ri = RuntimeInfo(path=p)
    ep = ri.set_endpoint(
        "postgres",
        host="127.0.0.1", port=35432,
        scheme="postgresql",
        user="chayuan_postgres", password="abc123",
        database="chayuan",
        url="postgresql://chayuan_postgres:abc123@127.0.0.1:35432/chayuan",
        kind="postgres",
    )
    assert ep["host"] == "127.0.0.1"
    assert ep["port"] == 35432
    assert ep.user == "chayuan_postgres"

    # 文件确实落了
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["services"]["postgres"]["port"] == 35432
    assert "updated_at" in doc

    # 重新打开能读到
    ri2 = RuntimeInfo(path=p)
    e2 = ri2.get_endpoint("postgres")
    assert e2 is not None
    assert e2["url"].endswith("/chayuan")


def test_masked_hides_password():
    ep = ServiceEndpoint({
        "host": "127.0.0.1", "port": 35432,
        "user": "u", "password": "secret",
        "url": "postgresql://u:secret@127.0.0.1:35432/db",
    })
    m = ep.masked()
    assert m["password"] == "****"
    assert "secret" not in m["url"]
    assert "****" in m["url"]


def test_credentials_persisted(tmp_path: Path):
    p = tmp_path / "runtime.json"
    ri = RuntimeInfo(path=p)
    ri.set_credentials("redis", user="chayuan_redis", password="rdspw")
    ri2 = RuntimeInfo(path=p)
    assert ri2.get_credentials("redis") == {"user": "chayuan_redis", "password": "rdspw"}


def test_remove_endpoint(tmp_path: Path):
    p = tmp_path / "runtime.json"
    ri = RuntimeInfo(path=p)
    ri.set_endpoint("api", host="127.0.0.1", port=62581)
    assert ri.get_endpoint("api") is not None
    ri.remove_endpoint("api")
    assert ri.get_endpoint("api") is None


def test_list_endpoints_sorted_by_name(tmp_path: Path):
    p = tmp_path / "runtime.json"
    ri = RuntimeInfo(path=p)
    ri.set_endpoint("zeta", host="h", port=1)
    ri.set_endpoint("alpha", host="h", port=2)
    names = [e["name"] for e in ri.list_endpoints()]
    assert names == ["alpha", "zeta"]
