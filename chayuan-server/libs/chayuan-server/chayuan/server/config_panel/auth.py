"""配置面板登录密码散列与校验（PBKDF2-SHA256，仅用 stdlib）。

存储格式（单行字符串，便于写入 yaml）:
    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import string
from typing import Tuple

_ALGO = "pbkdf2_sha256"
_DEFAULT_ITERATIONS = 260_000
_SALT_BYTES = 16

_LOGIN_PATH_RE = re.compile(r"^[a-z0-9_-]{3,32}$")
# 保留词；这些路径 NiceGUI 已占用（或我们自己用于 dashboard / logout / 根），
# 不允许被登录随机路径复用。
_LOGIN_PATH_RESERVED = frozenset(
    {
        "",
        "/",
        "dashboard",
        "logout",
        "static",
        "_nicegui",
        "login",  # 预留「常规 /login」以便需要切换成静态路径时使用
    }
)


def hash_password(password: str, iterations: int = _DEFAULT_ITERATIONS) -> str:
    """生成加盐 PBKDF2-SHA256 散列字符串。"""
    if not isinstance(password, str) or password == "":
        raise ValueError("密码不能为空")
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${dk.hex()}"


def _split(stored: str) -> Tuple[str, int, bytes, bytes]:
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != _ALGO:
        raise ValueError("密码散列格式无效")
    algo, iter_s, salt_hex, hash_hex = parts
    return algo, int(iter_s), bytes.fromhex(salt_hex), bytes.fromhex(hash_hex)


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验密码。stored 格式不正确或密码空均返回 False。"""
    if not password or not stored:
        return False
    try:
        _, iterations, salt, expected = _split(stored)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)


def generate_session_secret(n_bytes: int = 32) -> str:
    """生成 NiceGUI `storage_secret` 所需的随机密钥（hex）。"""
    return secrets.token_hex(n_bytes)


def generate_login_path(length: int = 8) -> str:
    """随机生成由小写字母组成、`length` 位长的登录路径段。"""
    alphabet = string.ascii_lowercase
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_login_path(raw: str) -> str:
    """将用户输入的路径归一化为合法的"纯路径段"。

    接受形如 `"/abc"`、`"abc/"`、`"abc"` 等输入，最终返回 `"abc"`（不带斜杠）。
    不会做合法性检查，仅处理格式；合法性请用 `validate_login_path`。
    """
    if raw is None:
        return ""
    s = str(raw).strip().strip("/")
    return s


def validate_login_path(path_segment: str) -> None:
    """校验登录路径段。不合法时抛 `ValueError` 且附上原因。"""
    if path_segment in _LOGIN_PATH_RESERVED:
        raise ValueError(
            f"路径 '{path_segment}' 为保留词（dashboard/logout/static/_nicegui/login 等），"
            "请换一个。"
        )
    if not _LOGIN_PATH_RE.fullmatch(path_segment):
        raise ValueError(
            "路径只能包含小写字母、数字、'-'、'_'，长度 3-32 位；且不可以 '/' 开头结尾。"
        )
