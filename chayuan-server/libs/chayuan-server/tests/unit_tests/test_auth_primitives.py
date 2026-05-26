"""针对 `chayuan.server.auth` 的纯函数单元测试。

这些测试**不依赖数据库 / FastAPI**，只验证 password / tokens 两个底层模块：

- 确保 hash/verify 的往返正确，且错误密码失败；
- 确保 JWT 的签名、过期、类型区分能按预期工作。
"""
from __future__ import annotations

import time

import pytest


# ---------------------------------------------------------------------------
# password
# ---------------------------------------------------------------------------

def test_hash_password_roundtrip():
    from chayuan.server.auth.password import hash_password, verify_password

    pwd = "Hello世界-123"
    h = hash_password(pwd)
    assert h and isinstance(h, str)
    assert h.startswith("$bcrypt$") or h.startswith("$scrypt$")
    assert verify_password(pwd, h) is True


def test_verify_password_rejects_wrong():
    from chayuan.server.auth.password import hash_password, verify_password

    h = hash_password("correct-horse")
    assert verify_password("wrong-horse", h) is False
    assert verify_password("", h) is False
    assert verify_password("correct-horse", "") is False
    assert verify_password("correct-horse", "$unknown$garbage") is False


def test_hash_password_different_salt_each_time():
    from chayuan.server.auth.password import hash_password

    a = hash_password("same-pwd")
    b = hash_password("same-pwd")
    assert a != b  # salt 不同，密文必须不同


@pytest.mark.parametrize("bad", [None, "", 123, b"bytes"])
def test_hash_password_rejects_invalid(bad):
    from chayuan.server.auth.password import hash_password

    with pytest.raises((ValueError, TypeError, AttributeError)):
        hash_password(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------

def _with_secret(monkeypatch):
    """注入固定 JWT_SECRET，避免进程随机秘钥导致 test 间耦合。"""
    from chayuan.settings import Settings
    monkeypatch.setattr(Settings.basic_settings, "JWT_SECRET", "unit-test-secret-" + "x" * 32, raising=False)
    monkeypatch.setattr(Settings.basic_settings, "JWT_ALGORITHM", "HS256", raising=False)
    monkeypatch.setattr(Settings.basic_settings, "JWT_ACCESS_TTL_SECONDS", 120, raising=False)
    monkeypatch.setattr(Settings.basic_settings, "JWT_REFRESH_TTL_SECONDS", 3600, raising=False)


def test_access_token_roundtrip(monkeypatch):
    _with_secret(monkeypatch)
    from chayuan.server.auth.tokens import (
        create_access_token, decode_token, TokenError,
    )

    tok = create_access_token(42, "alice", role="admin")
    decoded = decode_token(tok, expected_type="access")
    assert decoded.user_id == 42
    assert decoded.username == "alice"
    assert decoded.payload.get("role") == "admin"
    assert decoded.token_type == "access"

    with pytest.raises(TokenError):
        decode_token(tok, expected_type="refresh")


def test_refresh_token_roundtrip(monkeypatch):
    _with_secret(monkeypatch)
    from chayuan.server.auth.tokens import create_refresh_token, decode_token

    tok = create_refresh_token(7, "bob")
    decoded = decode_token(tok, expected_type="refresh")
    assert decoded.user_id == 7
    assert decoded.username == "bob"
    assert decoded.token_type == "refresh"


def test_token_expired_raises(monkeypatch):
    _with_secret(monkeypatch)
    from chayuan.server.auth.tokens import create_token, decode_token, TokenError

    tok = create_token({"sub": "1", "username": "x"}, ttl_seconds=-5, token_type="access")
    with pytest.raises(TokenError):
        decode_token(tok)


def test_token_tamper_detected(monkeypatch):
    _with_secret(monkeypatch)
    from chayuan.server.auth.tokens import create_access_token, decode_token, TokenError

    tok = create_access_token(1, "u")
    # 拿到 payload 部分做任意篡改（base64url 中 "a"/"A" 位移一下）
    parts = tok.split(".")
    parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
    tampered = ".".join(parts)
    with pytest.raises(TokenError):
        decode_token(tampered)


def test_decode_malformed_token():
    from chayuan.server.auth.tokens import decode_token, TokenError

    with pytest.raises(TokenError):
        decode_token("")
    with pytest.raises(TokenError):
        decode_token("not.a.jwt.too.many.dots")
    with pytest.raises(TokenError):
        decode_token("only-one-part")


def test_token_secret_switch_invalidates(monkeypatch):
    """换密钥后老 token 必须解析失败 —— 验证签名严格校验。"""
    from chayuan.settings import Settings
    from chayuan.server.auth.tokens import create_access_token, decode_token, TokenError

    monkeypatch.setattr(Settings.basic_settings, "JWT_SECRET", "secret-A" + "x" * 40, raising=False)
    monkeypatch.setattr(Settings.basic_settings, "JWT_ALGORITHM", "HS256", raising=False)
    tok = create_access_token(1, "u")

    monkeypatch.setattr(Settings.basic_settings, "JWT_SECRET", "secret-B" + "y" * 40, raising=False)
    with pytest.raises(TokenError):
        decode_token(tok)
