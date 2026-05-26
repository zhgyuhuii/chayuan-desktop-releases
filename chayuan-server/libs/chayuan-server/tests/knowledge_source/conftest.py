"""知识源子系统的公共 fixtures。

目标：每个测试函数得到的是**干净、独立、可重复**的环境：
- CHAYUAN_ROOT 指向临时目录，避免污染用户本机 ~/chayuan_data
- 元数据 DB 切成临时 SQLite（内存级速度 / 每次 session 重建）
- Redis 默认未配置 → 三层缓存走 fail-open；需要测缓存的用例自己起 fakeredis
- LLM 默认 stub 掉，避免单测依赖外部 OpenAI

提供的公共 fixture：
- ``ks_temp_root``   （session）  临时 CHAYUAN_ROOT
- ``ks_db``          （function） 带 migrations 的临时元数据 DB
- ``stub_llm``       （function） 打桩 get_ChatOpenAI；记录调用 + 返回可控 JSON
- ``stub_embeddings``（function） 打桩 get_Embeddings；返回假向量
- ``in_memory_sqlite_source``（function）一条 SQLite 数据源 ready to search
- ``sql_connector_factory``（function）给定 dialect + spec 构建 SqlConnector
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


# ---------------------------------------------------------------------------
# 1) 临时 CHAYUAN_ROOT：必须在 import chayuan 之前设置
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ks_temp_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("chayuan_data")
    os.environ["CHAYUAN_ROOT"] = str(root)
    # 强制 settings 使用该路径
    yield root
    # 测试结束后尽量清理
    try:
        shutil.rmtree(str(root), ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 2) 临时 SQLite 元数据库 + 跑 migrations
# ---------------------------------------------------------------------------

@pytest.fixture
def ks_db(ks_temp_root, monkeypatch):
    """为每个测试建一个独立的 SQLite 元数据库，并跑全部 migration。

    通过 monkeypatch `SQLALCHEMY_DATABASE_URI` 的方式做到"测试专用 DB"——
    与用户本机数据 / 其它测试互不干扰。
    """
    db_path = Path(ks_temp_root) / f"test_meta_{os.getpid()}_{id(monkeypatch)}.db"
    if db_path.exists():
        db_path.unlink(missing_ok=True)

    from chayuan.settings import Settings
    monkeypatch.setattr(
        Settings.basic_settings, "SQLALCHEMY_DATABASE_URI",
        f"sqlite:///{db_path.as_posix()}", raising=False,
    )

    # 重建 engine + SessionLocal，让 session_scope 读到新 DB
    import chayuan.server.db.base as db_base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(db_base, "engine", engine, raising=True)
    monkeypatch.setattr(db_base, "SessionLocal",
                         sessionmaker(autocommit=False, autoflush=False, bind=engine),
                         raising=True)

    # session 模块里也有 SessionLocal 的直接引用
    import chayuan.server.db.session as db_session
    monkeypatch.setattr(db_session, "SessionLocal", db_base.SessionLocal, raising=True)

    # 跑 migrations
    from chayuan.server.db.migrations import run_migrations
    run_migrations()

    yield engine

    try:
        engine.dispose()
    finally:
        db_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 3) LLM / Embedding 打桩
# ---------------------------------------------------------------------------

class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    """记录调用的假 LLM。.invoke([...]) 返回预置 content。"""

    def __init__(self, canned: Any):
        self.canned = canned  # 可 callable(messages)->str，也可静态 str
        self.calls: List[List[Dict[str, str]]] = []

    def invoke(self, messages, **_kw):
        self.calls.append(list(messages))
        if callable(self.canned):
            return _FakeLLMResponse(self.canned(messages))
        return _FakeLLMResponse(str(self.canned))

    def __or__(self, other):  # 让 `prompt | llm` 依旧能跑
        class _Chain:
            def __init__(self, prompt, llm):
                self.prompt = prompt
                self.llm = llm

            def ainvoke(self, payload):
                # Text2SQL pipeline 不走这条；仅为兼容 chat.py 类路径
                return self.llm.invoke([{"role": "user", "content": str(payload)}])
        return _Chain(other, self)


@pytest.fixture
def stub_llm(monkeypatch):
    """用法：
        stub_llm.respond('{"sql": "SELECT 1", "reason": "ok"}')
        ...
    """
    state = {"llm": _FakeLLM('{"sql": "", "reason": "not configured"}')}

    def _factory(model_name=None, temperature=0.0, streaming=False, callbacks=None, **_kw):
        return state["llm"]

    import chayuan.server.utils as _u
    monkeypatch.setattr(_u, "get_ChatOpenAI", _factory, raising=True)

    class _Helper:
        def respond(self, content: Any):
            state["llm"] = _FakeLLM(content)

        @property
        def calls(self):
            return state["llm"].calls

    return _Helper()


class _FakeEmbeddings:
    """哈希式假 embedding：同一文本总得到同向量；语义不重要但能让检索流程跑通。"""

    def _hash_vec(self, text: str, dim: int = 64):
        import hashlib
        import struct
        h = hashlib.sha256((text or "").encode("utf-8")).digest()
        # 扩展 + 归一化
        raw = (h * ((dim * 4) // len(h) + 1))[: dim * 4]
        vec = list(struct.unpack(f"{dim}f", raw))
        import math
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts):
        return [self._hash_vec(t) for t in texts]

    def embed_query(self, text: str):
        return self._hash_vec(text)


@pytest.fixture
def stub_embeddings(monkeypatch):
    emb = _FakeEmbeddings()
    import chayuan.server.utils as _u
    monkeypatch.setattr(_u, "get_Embeddings", lambda *a, **k: emb, raising=True)
    return emb


# ---------------------------------------------------------------------------
# 4) SQLite 数据源工厂（零依赖、最快的端到端测试底座）
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_source_factory(ks_db, tmp_path):
    """创建一个有真实数据的 SQLite 文件；返回 (source_id, db_path, conn_spec)。"""
    import sqlite3
    from chayuan.server.db.repository.knowledge_source_repository import (
        create_connection,
        create_source,
    )
    from chayuan.server.knowledge_source.base import ConnectionSpec

    def _make(name: str = "test_sqlite", seed_sql: Optional[str] = None):
        db_path = tmp_path / f"{name}.db"
        cx = sqlite3.connect(str(db_path))
        cur = cx.cursor()
        cur.executescript(seed_sql or DEFAULT_SEED_SQL)
        cx.commit()
        cx.close()

        conn_id = create_connection(
            dialect="sqlite", host="", port=0,
            database=str(db_path), username="", password="",
            options={}, allowed={}, owner_id=1,
        )
        sid = create_source(
            name=name, kind="sql", display_name=name, description="test sqlite",
            connection_id=conn_id, owner_id=1, visibility="private",
        )
        spec = ConnectionSpec(dialect="sqlite", database=str(db_path))
        return sid, str(db_path), spec

    return _make


DEFAULT_SEED_SQL = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    category TEXT
);
INSERT INTO products (id, name, price, category) VALUES
    (1, 'iPhone 15', 7999.0, 'phone'),
    (2, 'MacBook Pro', 19999.0, 'laptop'),
    (3, 'AirPods', 1299.0, 'audio');

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    product_id INTEGER,
    qty INTEGER,
    created_at TEXT
);
INSERT INTO orders (id, product_id, qty, created_at) VALUES
    (1, 1, 2, '2026-04-01'),
    (2, 2, 1, '2026-04-02'),
    (3, 3, 5, '2026-04-03');
"""


# ---------------------------------------------------------------------------
# 5) fakeredis 支持（可选）：需要验证三层缓存行为时启用
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_redis(monkeypatch):
    """为 knowledge_source.cache 注入 fakeredis；未装 fakeredis 则 skip。"""
    try:
        import fakeredis  # type: ignore
    except Exception:
        pytest.skip("未安装 fakeredis，无法测试缓存层行为")
    client = fakeredis.FakeStrictRedis()
    import chayuan.server.knowledge_source.cache as cache_mod
    monkeypatch.setattr(cache_mod, "_REDIS_CLIENT", client, raising=False)
    monkeypatch.setattr(cache_mod, "_REDIS_CHECKED", True, raising=False)
    monkeypatch.setattr(cache_mod, "_get_redis", lambda: client, raising=True)
    return client
