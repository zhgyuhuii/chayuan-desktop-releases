"""``server/shared/secret_store.py`` + ``apps_store`` 加密集成测试。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_store():
    """每个测试前强制重置 secret_store 单例 + 清 env。"""
    from chayuan.server.shared import secret_store

    prev_env = os.environ.pop("CHAYUAN_MASTER_KEY", None)
    secret_store._reset_for_tests()
    yield
    secret_store._reset_for_tests()
    if prev_env is not None:
        os.environ["CHAYUAN_MASTER_KEY"] = prev_env
    else:
        os.environ.pop("CHAYUAN_MASTER_KEY", None)


def test_no_key_degrades_to_plaintext(monkeypatch, tmp_path):
    from chayuan.server.shared import secret_store

    # 无 env + 无文件 → 明文直通
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    assert secret_store.encrypt("hello") == "hello"
    assert secret_store.decrypt("hello") == "hello"
    assert secret_store.is_encrypted("hello") is False
    assert secret_store.status()["enabled"] is False


def test_env_key_encrypt_decrypt_roundtrip(monkeypatch):
    from cryptography.fernet import Fernet
    from chayuan.server.shared import secret_store

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CHAYUAN_MASTER_KEY", key)

    plain = "super-secret-token-中文"
    enc = secret_store.encrypt(plain)
    assert enc.startswith("enc:v1:")
    assert enc != plain
    assert secret_store.is_encrypted(enc)
    assert secret_store.decrypt(enc) == plain

    # 幂等：对密文再调 encrypt 应原样返回
    assert secret_store.encrypt(enc) == enc

    # 明文 decrypt 原样返回
    assert secret_store.decrypt(plain) == plain

    assert secret_store.status() == {"enabled": True, "key_source": "env"}


def test_hex_key_also_accepted(monkeypatch):
    from chayuan.server.shared import secret_store

    # 64 字符 hex 会被 coerce
    monkeypatch.setenv("CHAYUAN_MASTER_KEY", "ab" * 32)
    enc = secret_store.encrypt("hi")
    assert enc.startswith("enc:v1:")
    assert secret_store.decrypt(enc) == "hi"


def test_decrypt_with_wrong_key_returns_empty(monkeypatch):
    from cryptography.fernet import Fernet
    from chayuan.server.shared import secret_store

    # 用 key1 加密
    key1 = Fernet.generate_key().decode()
    monkeypatch.setenv("CHAYUAN_MASTER_KEY", key1)
    enc = secret_store.encrypt("hi")

    # 换一把 key，decrypt 应该返回空（而非抛）
    secret_store._reset_for_tests()
    key2 = Fernet.generate_key().decode()
    monkeypatch.setenv("CHAYUAN_MASTER_KEY", key2)
    assert secret_store.decrypt(enc) == ""


def test_file_key_used_when_env_absent(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet
    from chayuan.server.shared import secret_store

    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    monkeypatch.delenv("CHAYUAN_MASTER_KEY", raising=False)

    key = Fernet.generate_key()
    (tmp_path / ".master.key").write_bytes(key)

    enc = secret_store.encrypt("hello-file-key")
    assert enc.startswith("enc:v1:")
    assert secret_store.decrypt(enc) == "hello-file-key"
    assert secret_store.status()["key_source"].startswith("file:")


def test_apps_store_encrypts_secret_on_persist(monkeypatch, tmp_path):
    """apps.yaml 物理文件里的 app_secret 应是密文。"""
    from cryptography.fernet import Fernet
    from chayuan.server.shared import secret_store

    monkeypatch.setenv("CHAYUAN_MASTER_KEY", Fernet.generate_key().decode())
    yaml_path = tmp_path / "apps.yaml"
    monkeypatch.setenv("CHAYUAN_APPS_YAML", str(yaml_path))
    # 关 config_center：本测试只关心 yaml 落盘
    monkeypatch.setenv("CHAYUAN_CONFIG_CENTER_DISABLED", "1")

    from chayuan.server.config_panel.apps_store import (
        create_app, get_app, rotate_secret,
    )

    spec = create_app("encrypt-test", scopes=["chat:read"])
    # 内存里拿到的是明文（fernet 解密后）
    assert not secret_store.is_encrypted(spec.app_secret)

    # yaml 文件里存的是密文
    raw = yaml_path.read_text()
    assert spec.app_secret not in raw, "明文 secret 不应出现在 yaml 里"
    assert "enc:v1:" in raw

    # 再从 store 读出来 → 解密回明文
    got = get_app(spec.app_id)
    assert got is not None and got.app_secret == spec.app_secret

    # 轮换：新明文可用，老明文失效
    old = spec.app_secret
    new = rotate_secret(spec.app_id)
    assert new is not None and new.app_secret != old
    raw2 = yaml_path.read_text()
    assert old not in raw2
    assert new.app_secret not in raw2  # 新明文也不应漏


def test_apps_store_plaintext_legacy_still_readable(monkeypatch, tmp_path):
    """老 apps.yaml 没启用加密（无 enc: 前缀），升级部署后还能读、下次保存自动升级。"""
    from cryptography.fernet import Fernet
    from chayuan.server.shared import secret_store

    yaml_path = tmp_path / "apps.yaml"
    monkeypatch.setenv("CHAYUAN_APPS_YAML", str(yaml_path))
    monkeypatch.delenv("CHAYUAN_MASTER_KEY", raising=False)
    # 关 config_center：老 yaml 测试只验 yaml 读写，不走 DB 路径
    monkeypatch.setenv("CHAYUAN_CONFIG_CENTER_DISABLED", "1")

    # 手搓一个老格式 yaml
    yaml_path.write_text(
        "apps:\n"
        "  - app_id: oldid\n"
        "    app_secret: plain-legacy-secret\n"
        "    name: legacy\n"
        "    enabled: true\n"
        "    created_at: '2020-01-01'\n"
        "    callback_url: ''\n"
        "    callback_events: []\n"
        "    scopes: [chat]\n",
        encoding="utf-8",
    )

    from chayuan.server.config_panel.apps_store import get_app, rotate_secret

    # 没 master key 也能读到明文
    spec = get_app("oldid")
    assert spec is not None and spec.app_secret == "plain-legacy-secret"

    # 设置 key 后轮换一下 → yaml 自动升级为密文
    monkeypatch.setenv("CHAYUAN_MASTER_KEY", Fernet.generate_key().decode())
    secret_store._reset_for_tests()

    new = rotate_secret("oldid")
    assert new is not None
    body = yaml_path.read_text()
    assert "plain-legacy-secret" not in body
    assert "enc:v1:" in body
