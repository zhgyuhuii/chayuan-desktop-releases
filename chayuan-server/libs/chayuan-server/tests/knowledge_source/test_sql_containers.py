"""真 MySQL / PostgreSQL 的 Connector 契约测试。

- 需要 Docker 以及 ``testcontainers-python``
- 启动容器约 10-15 秒；CI 建议放到 integration job，PR 默认跳过
- 运行：
    pytest tests/knowledge_source/test_sql_containers.py -v --only-extended
"""
from __future__ import annotations

import pytest


@pytest.mark.requires("testcontainers", "pymysql", "sqlalchemy")
def test_mysql_contract_end_to_end():
    try:
        from testcontainers.mysql import MySqlContainer
    except Exception:
        pytest.skip("testcontainers.mysql 不可用")

    with MySqlContainer("mysql:8.0") as mysql:
        host = mysql.get_container_host_ip()
        port = int(mysql.get_exposed_port(3306))
        url = mysql.get_connection_url()  # mysql+pymysql://...

        # seed
        from sqlalchemy import create_engine, text
        eng = create_engine(url)
        with eng.begin() as cn:
            cn.execute(text("CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(64))"))
            cn.execute(text("INSERT INTO t VALUES (1, 'apple'), (2, 'banana')"))
        eng.dispose()

        from chayuan.server.knowledge_source.base import ConnectionSpec
        from chayuan.server.knowledge_source.sql.connector import SqlConnector
        spec = ConnectionSpec(
            dialect="mysql", host=host, port=port,
            database=mysql.dbname, username=mysql.username, password=mysql.password,
        )
        c = SqlConnector(spec=spec, source_id=1)

        ok, msg = c.test_connection()
        assert ok, msg

        snap = c.introspect(sample_rows=3)
        table_names = {t.name for t in snap.tables}
        assert "t" in table_names
        t_col_names = {col.name for col in
                        next(x for x in snap.tables if x.name == "t").columns}
        assert {"id", "name"}.issubset(t_col_names)


@pytest.mark.requires("testcontainers", "psycopg2", "sqlalchemy")
def test_postgres_contract_end_to_end():
    try:
        from testcontainers.postgres import PostgresContainer
    except Exception:
        pytest.skip("testcontainers.postgres 不可用")

    with PostgresContainer("postgres:15") as pg:
        from sqlalchemy import create_engine, text
        eng = create_engine(pg.get_connection_url())
        with eng.begin() as cn:
            cn.execute(text("CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(64))"))
            cn.execute(text("INSERT INTO t VALUES (1, 'x'), (2, 'y')"))
        eng.dispose()

        host = pg.get_container_host_ip()
        port = int(pg.get_exposed_port(5432))

        from chayuan.server.knowledge_source.base import ConnectionSpec
        from chayuan.server.knowledge_source.sql.connector import SqlConnector
        spec = ConnectionSpec(
            dialect="postgres", host=host, port=port,
            database=pg.dbname, username=pg.username, password=pg.password,
        )
        c = SqlConnector(spec=spec, source_id=1)

        ok, msg = c.test_connection()
        assert ok, msg

        snap = c.introspect(sample_rows=3)
        assert "t" in {t.name for t in snap.tables}


@pytest.mark.requires("testcontainers", "elasticsearch")
def test_es_contract_end_to_end():
    try:
        from testcontainers.elasticsearch import ElasticSearchContainer
    except Exception:
        pytest.skip("testcontainers.elasticsearch 不可用")

    with ElasticSearchContainer("elasticsearch:8.12.0") as es:
        from elasticsearch import Elasticsearch
        url = es.get_url()
        client = Elasticsearch(url)
        client.indices.create(index="items")
        client.index(index="items", document={"name": "apple", "price": 10})
        client.index(index="items", document={"name": "banana", "price": 20})
        client.indices.refresh(index="items")

        from urllib.parse import urlparse
        parsed = urlparse(url)
        from chayuan.server.knowledge_source.base import ConnectionSpec
        from chayuan.server.knowledge_source.es.connector import EsConnector

        spec = ConnectionSpec(
            dialect="es", host=parsed.hostname or "localhost",
            port=int(parsed.port or 9200), database="",
            username="", password="",
            options={"scheme": parsed.scheme or "http", "verify_certs": False},
        )
        c = EsConnector(spec=spec, source_id=1)

        ok, msg = c.test_connection()
        assert ok, msg

        snap = c.introspect(sample_rows=2)
        assert "items" in {t.name for t in snap.tables}
