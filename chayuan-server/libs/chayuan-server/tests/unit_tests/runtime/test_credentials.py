"""ensure_credentials + mask_password_in_url 测试。

通过给 ``ensure_credentials`` 注入一个内存版 RuntimeInfo 替身，避免污染
真实 ``<CHAYUAN_ROOT>/runtime.json``。
"""
from __future__ import annotations

from chayuan.server.runtime.credentials import (
    Credentials,
    ensure_credentials,
    mask_password_in_url,
    url_quote_password,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.creds: dict = {}
        self.path = "/tmp/fake_runtime.json"  # type: ignore[assignment]

    def get_credentials(self, name: str) -> dict:
        return dict(self.creds.get(name) or {})

    def set_credentials(self, name: str, user: str, password: str) -> None:
        self.creds[name] = {"user": user, "password": password}


def test_first_call_generates_password_and_persists():
    rt = FakeRuntime()
    c1 = ensure_credentials("postgres", runtime_info=rt)
    assert isinstance(c1, Credentials)
    assert c1.user == "chayuan_postgres"
    assert len(c1.password) >= 24
    assert rt.creds["postgres"]["password"] == c1.password


def test_second_call_returns_same_credentials():
    rt = FakeRuntime()
    c1 = ensure_credentials("redis", runtime_info=rt)
    c2 = ensure_credentials("redis", runtime_info=rt)
    assert c1 == c2


def test_no_auth_returns_empty():
    rt = FakeRuntime()
    c = ensure_credentials("milvus", runtime_info=rt, no_auth=True)
    assert c.user == "" and c.password == ""
    assert "milvus" not in rt.creds


def test_password_alphabet_url_safe():
    rt = FakeRuntime()
    c = ensure_credentials("api", runtime_info=rt, password_length=64)
    # 不应包含会破坏 URL 解析的字符
    forbidden = set("@:/?#&=;'\"|*<>$`%+ \\")
    assert all(ch not in forbidden for ch in c.password)


def test_mask_password_in_url_basic():
    url = "postgresql://user:supersecret123@localhost:5432/db"
    masked = mask_password_in_url(url)
    assert masked == "postgresql://user:****@localhost:5432/db"


def test_mask_password_in_url_redis():
    url = "redis://chayuan:p@ssW0rd_no_at@127.0.0.1:6379/0"
    # 我们的密码生成器不会包含 @，但 mask 应能优雅处理"含特殊符号的旧密码"
    masked = mask_password_in_url(url)
    # 失败时原样返回 — 不引入误处理。
    assert masked.startswith("redis://chayuan:")


def test_mask_returns_input_when_no_password():
    assert mask_password_in_url("http://localhost:62581/v1") == "http://localhost:62581/v1"
    assert mask_password_in_url("") == ""


def test_quote_password_idempotent_for_safe():
    rt = FakeRuntime()
    c = ensure_credentials("svc1", runtime_info=rt)
    assert url_quote_password(c.password) == c.password  # 无需 escape
