"""密码哈希：优先用 bcrypt；缺包时回落 hashlib.scrypt。

- 永远不存明文。
- hash 字符串前缀区分算法：
    * `$bcrypt$<bcrypt_hash>`
    * `$scrypt$<n>$<r>$<p>$<salt_hex>$<derived_hex>`
- `verify_password()` 可以识别历史任意前缀，方便跨版本升级。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Tuple

logger = logging.getLogger("chayuan.auth.password")

_BCRYPT_OK = False
try:
    import bcrypt  # type: ignore
    _BCRYPT_OK = True
except ImportError:
    pass

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_SALT_BYTES = 16


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password must be non-empty string")
    if len(password) > 1024:
        raise ValueError("password too long")

    if _BCRYPT_OK:
        digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        return "$bcrypt$" + digest.decode("utf-8")

    salt = os.urandom(_SCRYPT_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "$scrypt${n}${r}${p}${salt}${derived}".format(
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        salt=salt.hex(), derived=derived.hex(),
    )


def verify_password(password: str, stored: str) -> bool:
    if not stored or not isinstance(stored, str):
        return False
    if not password:
        return False

    try:
        if stored.startswith("$bcrypt$"):
            if not _BCRYPT_OK:
                logger.warning("bcrypt hash encountered but bcrypt package missing")
                return False
            return bcrypt.checkpw(password.encode("utf-8"), stored[len("$bcrypt$"):].encode("utf-8"))

        if stored.startswith("$scrypt$"):
            _, _alg, n_s, r_s, p_s, salt_hex, derived_hex = stored.split("$", 6)
            n, r, p = int(n_s), int(r_s), int(p_s)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(derived_hex)
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt, n=n, r=r, p=p, dklen=len(expected),
            )
            return hmac.compare_digest(expected, actual)
    except Exception:  # noqa: BLE001
        logger.debug("verify_password exception", exc_info=True)
        return False

    logger.warning("unrecognized password hash format")
    return False


def algorithm_name() -> str:
    return "bcrypt" if _BCRYPT_OK else "scrypt"
