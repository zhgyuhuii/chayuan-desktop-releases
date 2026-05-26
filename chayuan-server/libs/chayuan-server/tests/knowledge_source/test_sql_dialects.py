"""SQL 方言 URL builder / 驱动探测的单测。

这类测试特别重要：一个拼错的 URL 会让生产环境的数据源突然连不上；每个方言
必须有对应守护。
"""
from __future__ import annotations

import pytest

from chayuan.server.knowledge_source.base import ConnectionSpec, ConnectorError


def _spec(dialect: str, **kw) -> ConnectionSpec:
    defaults = dict(host="10.0.0.1", port=0, database="mydb",
                     username="u", password="p@ss w#d", options={})
    defaults.update(kw)
    return ConnectionSpec(dialect=dialect, **defaults)


# ---------------------------------------------------------------------------
# 通用：URL 中密码/用户名必须被 urlencode（@ / # / 空格）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_url_escapes_password(dialect, monkeypatch):
    from chayuan.server.knowledge_source.sql import dialects as D

    # 避免驱动未装导致 pick_driver 抛异常
    monkeypatch.setattr(D, "_pick_driver", lambda prof: prof.drivers[0])
    url = D.build_sqlalchemy_url(_spec(dialect, password="p@ss w#d"))
    # urlencode 后 @=%40, #=%23, 空格=+
    assert "%40" in url  # @
    assert "%23" in url  # #
    assert "+" in url or "%20" in url  # 空格
    # 明文密码不应出现
    assert "p@ss w#d" not in url


def test_sqlite_uses_pysqlite_and_abs_path(tmp_path):
    from chayuan.server.knowledge_source.sql import dialects as D
    db = tmp_path / "x.db"
    url = D.build_sqlalchemy_url(_spec("sqlite", database=str(db)))
    assert url.startswith("sqlite")
    assert str(db).replace("\\", "/") in url.replace("\\", "/")


def test_sqlite_memory_url():
    from chayuan.server.knowledge_source.sql import dialects as D
    url = D.build_sqlalchemy_url(_spec("sqlite", database=":memory:"))
    assert url == "sqlite:///:memory:"


def test_unknown_dialect_raises():
    from chayuan.server.knowledge_source.sql import dialects as D
    with pytest.raises(ConnectorError) as ei:
        D.get_profile("foobar")
    assert ei.value.code == "dialect_unsupported"


def test_mssql_pyodbc_builds_odbc_connect(monkeypatch):
    from chayuan.server.knowledge_source.sql import dialects as D
    # 强制选 pyodbc
    monkeypatch.setattr(D, "_pick_driver", lambda prof: "pyodbc")
    url = D.build_sqlalchemy_url(_spec("mssql", port=1433))
    assert url.startswith("mssql+pyodbc:///?odbc_connect=")
    # 关键字段被包含进 odbc_connect（已 quote_plus 编码）
    assert "DRIVER%3D" in url or "DRIVER=" in url or "ODBC" in url


def test_doris_goes_through_mysql_proto(monkeypatch):
    from chayuan.server.knowledge_source.sql import dialects as D
    monkeypatch.setattr(D, "_pick_driver", lambda prof: "pymysql")
    url = D.build_sqlalchemy_url(_spec("doris", port=9030))
    assert url.startswith("mysql+pymysql://")
    assert ":9030/" in url


def test_clickhouse_requires_sqlalchemy_pkg(monkeypatch):
    from chayuan.server.knowledge_source.sql import dialects as D
    # 模拟 clickhouse_sqlalchemy 未装
    import importlib
    real_import = importlib.import_module

    def fake_import(name, *args, **kwargs):
        if name == "clickhouse_sqlalchemy":
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(ConnectorError) as ei:
        D.build_sqlalchemy_url(_spec("clickhouse"))
    assert ei.value.code == "driver_missing"


def test_normalize_dialect_aliases():
    from chayuan.server.knowledge_source.registry import normalize_dialect
    assert normalize_dialect("PostgreSQL") == "postgres"
    assert normalize_dialect("mongodb") == "mongo"
    assert normalize_dialect("Elasticsearch") == "es"
    assert normalize_dialect("SQLServer") == "mssql"
    assert normalize_dialect("mysql") == "mysql"


def test_all_supported_dialects_covers_core():
    from chayuan.server.knowledge_source.registry import all_supported_dialects
    d = all_supported_dialects()
    # 至少涵盖 SQL 7 方言 + Mongo + ES + image
    assert {
        "mysql", "postgres", "sqlite", "mssql", "oracle",
        "clickhouse", "doris", "mongo", "es", "image",
    }.issubset(set(d.keys()))
