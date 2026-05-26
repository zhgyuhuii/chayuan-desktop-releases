"""AsyncSqlConnector URL 构造 & Registry 路由单测。

不启真实异步驱动；仅验证：
- 7 种方言的 URL 前缀是否正确（防止拼错）
- 特殊情况：Oracle 的 service_name；MSSQL 的 odbc_connect；SQLite :memory:
- Registry 在「驱动未装」时回退同步版；在「驱动已装 + _ASYNC_ENABLED」时选异步版
"""
from __future__ import annotations

import pytest

from chayuan.server.knowledge_source.base import ConnectionSpec


def _s(dialect, **kw):
    d = dict(host="h", port=0, database="mydb",
             username="u", password="p@s s", options={})
    d.update(kw)
    return ConnectionSpec(dialect=dialect, **d)


@pytest.mark.parametrize("dialect,prefix,port", [
    ("mysql", "mysql+asyncmy://", 3306),
    ("postgres", "postgresql+asyncpg://", 5432),
    ("doris", "mysql+asyncmy://", 9030),
])
def test_async_url_prefix_and_port(dialect, prefix, port):
    from chayuan.server.knowledge_source.sql.async_connector import _build_async_url
    url = _build_async_url(_s(dialect))
    assert url.startswith(prefix)
    assert f":{port}/" in url
    assert "%40" in url  # @ 被 urlencode
    assert "p@s s" not in url  # 明文密码不应出现


def test_async_url_sqlite_memory():
    from chayuan.server.knowledge_source.sql.async_connector import _build_async_url
    assert _build_async_url(_s("sqlite", database=":memory:")) == "sqlite+aiosqlite:///:memory:"


def test_async_url_sqlite_file(tmp_path):
    from chayuan.server.knowledge_source.sql.async_connector import _build_async_url
    db = tmp_path / "x.db"
    url = _build_async_url(_s("sqlite", database=str(db)))
    assert url.startswith("sqlite+aiosqlite:///")


def test_async_url_oracle_service_name():
    from chayuan.server.knowledge_source.sql.async_connector import _build_async_url
    url = _build_async_url(_s("oracle", port=1521,
                                options={"service_name": "XEPDB1"}))
    assert url.startswith("oracle+oracledb://")
    assert "service_name=XEPDB1" in url


def test_async_url_mssql_embeds_odbc_connect():
    from chayuan.server.knowledge_source.sql.async_connector import _build_async_url
    url = _build_async_url(_s("mssql", port=1433))
    assert url.startswith("mssql+aioodbc:///?odbc_connect=")


def test_async_url_clickhouse_asynch():
    from chayuan.server.knowledge_source.sql.async_connector import _build_async_url
    url = _build_async_url(_s("clickhouse", port=8123))
    assert url.startswith("clickhouse+asynch://")


def test_async_url_unknown_dialect():
    from chayuan.server.knowledge_source.base import ConnectorError
    from chayuan.server.knowledge_source.sql.async_connector import _build_async_url
    with pytest.raises(ConnectorError):
        _build_async_url(_s("nosuch"))


# ---------------------------------------------------------------------------
# Registry 路由：驱动未装 → 同步；装了 → 异步
# ---------------------------------------------------------------------------

def test_registry_falls_back_to_sync_when_async_driver_missing(monkeypatch):
    from chayuan.server.knowledge_source import registry
    # 让所有 async driver 检测返回 False
    monkeypatch.setattr(registry, "_async_driver_available", lambda d: False)
    cls = registry.get_connector_class("mysql")
    # 回退同步
    assert cls.__name__ == "SqlConnector"


def test_registry_picks_async_when_driver_installed(monkeypatch):
    from chayuan.server.knowledge_source import registry
    monkeypatch.setattr(registry, "_async_driver_available", lambda d: True)
    registry.set_async_enabled(True)
    cls = registry.get_connector_class("mysql")
    assert cls.__name__ == "AsyncSqlConnector"


def test_registry_async_disabled_globally(monkeypatch):
    from chayuan.server.knowledge_source import registry
    monkeypatch.setattr(registry, "_async_driver_available", lambda d: True)
    registry.set_async_enabled(False)
    try:
        cls = registry.get_connector_class("mysql")
        assert cls.__name__ == "SqlConnector"
    finally:
        registry.set_async_enabled(True)  # 复原


def test_registry_async_for_mongo_and_es(monkeypatch):
    from chayuan.server.knowledge_source import registry
    monkeypatch.setattr(registry, "_async_driver_available", lambda d: True)
    registry.set_async_enabled(True)
    assert registry.get_connector_class("mongo").__name__ == "AsyncMongoConnector"
    assert registry.get_connector_class("es").__name__ == "AsyncEsConnector"
