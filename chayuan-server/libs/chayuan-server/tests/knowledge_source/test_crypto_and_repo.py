"""加密与 Repository 层的核心契约测试。

- crypto：encrypt → decrypt 可逆；空值不加噪声；invalid 密文 decrypt 返回空串不崩
- repository：create_source / create_connection 幂等；row_to_connection_spec 正确解密
"""
from __future__ import annotations

import os

import pytest


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------

def test_encrypt_empty_returns_empty():
    from chayuan.server.knowledge_source import crypto
    assert crypto.encrypt("") == ""
    assert crypto.encrypt(None) == ""  # type: ignore[arg-type]


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("CHAYUAN_SOURCE_SECRET_KEY", "unit-test-fixed-key-v1")
    # 强制重建 Fernet 单例
    from chayuan.server.knowledge_source import crypto
    monkeypatch.setattr(crypto, "_FERNET_SINGLETON", None, raising=False)

    plaintext = "P@ssw0rd#@! 中文密码"
    ct = crypto.encrypt(plaintext)
    assert ct
    assert plaintext not in ct  # 密文不含明文
    pt = crypto.decrypt(ct)
    assert pt == plaintext


def test_decrypt_garbage_returns_empty(monkeypatch):
    monkeypatch.setenv("CHAYUAN_SOURCE_SECRET_KEY", "unit-test-fixed-key-v1")
    from chayuan.server.knowledge_source import crypto
    monkeypatch.setattr(crypto, "_FERNET_SINGLETON", None, raising=False)
    assert crypto.decrypt("not-a-real-ciphertext") == ""
    assert crypto.decrypt("") == ""


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

def test_create_source_is_idempotent_on_name(ks_db):
    from chayuan.server.db.repository.knowledge_source_repository import (
        create_source,
    )
    sid1 = create_source(name="alpha", kind="vector")
    sid2 = create_source(name="alpha", kind="vector")
    assert sid1 == sid2, "同 name 必须返回同 id 做幂等"


def test_connection_password_roundtrip(ks_db):
    from chayuan.server.db.repository.knowledge_source_repository import (
        create_connection,
        get_connection,
        row_to_connection_spec,
    )
    cid = create_connection(
        dialect="mysql", host="h", port=3306,
        database="db", username="u", password="P@ss!",
        options={"k": "v"}, allowed={"tables": ["users"]}, owner_id=1,
    )
    row = get_connection(cid)
    assert row is not None
    # 落盘的是密文
    assert row.password_enc and "P@ss!" not in row.password_enc
    # 解密后的 spec 拿得到明文
    spec = row_to_connection_spec(row)
    assert spec.password == "P@ss!"
    assert spec.allowed_tables == ["users"]
    assert spec.options == {"k": "v"}


def test_grant_access_upsert(ks_db):
    from chayuan.server.db.repository.knowledge_source_repository import (
        create_source, grant_source_access, list_source_grants,
    )
    sid = create_source(name="svc1", kind="sql")
    grant_source_access(sid, 100, role="reader")
    grant_source_access(sid, 100, role="editor")  # upsert
    grants = list_source_grants(sid)
    assert len(grants) == 1
    assert grants[0]["role"] == "editor"


def test_batch_grant(ks_db):
    from chayuan.server.db.repository.knowledge_source_repository import (
        create_source, grant_source_access_batch, list_source_grants,
    )
    sids = [create_source(name=f"s{i}", kind="sql") for i in range(3)]
    added = grant_source_access_batch(sids, [10, 20], role="reader", granted_by=1)
    assert added == 6
    # 每个 source 下都有 2 条授权
    for sid in sids:
        assert len(list_source_grants(sid)) == 2


def test_sql_training_sample_idempotent(ks_db):
    from chayuan.server.db.repository.sql_training_repository import (
        add_sample, list_samples,
    )
    sid = 1
    _id1, created1 = add_sample(
        source_id=sid, kind="pair",
        question="最贵的商品", sql="SELECT * FROM products ORDER BY price DESC LIMIT 1",
    )
    _id2, created2 = add_sample(
        source_id=sid, kind="pair",
        question="最贵的商品", sql="SELECT * FROM products ORDER BY price DESC LIMIT 1",
    )
    assert created1 is True
    assert created2 is False, "相同内容应幂等"
    assert _id1 == _id2

    samples = list_samples(sid, kind="pair")
    assert len(samples) == 1
