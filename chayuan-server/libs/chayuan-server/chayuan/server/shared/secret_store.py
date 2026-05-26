"""Secret 持久化兼容层。

设计目标
--------
- 默认不要求额外 master key：App 管理直接使用 AppKey + AppSecret 访问；
- 未设置 ``CHAYUAN_MASTER_KEY`` / ``CHAYUAN_ROOT/.master.key`` 时按明文读写 yaml，
  不输出运维告警；
- 保留 ``enc:v1:`` 历史密文兼容：如果部署曾经启用过 master key，仍可解密读取。

Master key 解析顺序
-------------------
1. 环境变量 ``CHAYUAN_MASTER_KEY``
2. 文件 ``CHAYUAN_ROOT/.master.key``
3. 都没有 → 明文读写

加密格式
--------
- 明文：``"xxx"``
- 密文：``"enc:v1:<base64 ciphertext>"``

前缀 ``enc:v1:`` 用来：
  (a) 区分密文 / 明文；
  (b) 留版本号，将来换算法（比如 Argon2id + AES-256-GCM）也能无缝迁移。
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Optional


logger = logging.getLogger("chayuan.secret_store")


_PREFIX_V1 = "enc:v1:"


class _SecretStoreImpl:
    """进程级单例（get_store() 统一入口）。"""

    def __init__(self) -> None:
        self._key: Optional[bytes] = None
        self._fernet = None  # 懒初始化，首次 encrypt/decrypt 时再 import cryptography
        self._key_source: str = "none"

    # ---- key resolution ----

    def _resolve_key(self) -> Optional[bytes]:
        if self._key is not None:
            return self._key

        raw = os.environ.get("CHAYUAN_MASTER_KEY", "").strip()
        if raw:
            self._key = _coerce_key(raw)
            self._key_source = "env"
            return self._key

        root = _resolve_root()
        if root is None:
            return None

        path = root / ".master.key"
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8").strip()
                self._key = _coerce_key(content)
                self._key_source = f"file:{path}"
                return self._key
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "读取 master key 文件失败 %s：%r；按降级到明文处理。",
                    path, e,
                )
        return None

    def _ensure_fernet(self):
        if self._fernet is not None:
            return self._fernet
        key = self._resolve_key()
        if key is None:
            return None
        try:
            from cryptography.fernet import Fernet  # type: ignore
            self._fernet = Fernet(key)
            return self._fernet
        except Exception as e:  # noqa: BLE001
            logger.warning("构造 Fernet 失败：%r；降级到明文。", e)
            return None

    # ---- public API ----

    @property
    def enabled(self) -> bool:
        return self._ensure_fernet() is not None

    @property
    def key_source(self) -> str:
        self._resolve_key()
        return self._key_source

    def is_encrypted(self, value: str) -> bool:
        return isinstance(value, str) and value.startswith(_PREFIX_V1)

    def encrypt(self, plain: str) -> str:
        """若已是密文原样返回；若拿不到 key 原样返回明文。"""
        if not plain:
            return plain
        if self.is_encrypted(plain):
            return plain
        fernet = self._ensure_fernet()
        if fernet is None:
            return plain
        token = fernet.encrypt(plain.encode("utf-8")).decode("ascii")
        return f"{_PREFIX_V1}{token}"

    def decrypt(self, value: str) -> str:
        """若未加密（无前缀）原样返回；若带前缀但 key 不可用，返回 ``""`` 并告警。"""
        if not isinstance(value, str) or not self.is_encrypted(value):
            return value
        fernet = self._ensure_fernet()
        if fernet is None:
            logger.error(
                "遇到密文但无可用 master key。该 secret 本次访问将返回空字符串；"
                "请设置 CHAYUAN_MASTER_KEY 后重启，或在面板里重新轮换 secret。"
            )
            return ""
        token = value[len(_PREFIX_V1):]
        try:
            return fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except Exception as e:  # noqa: BLE001
            logger.error("Fernet.decrypt 失败：%r（master key 可能与加密时不一致）", e)
            return ""

    # ---- 可选：首次启用时生成本机密钥 ----

    def ensure_local_key_file(self) -> Optional[Path]:
        """明文主动启用时调用：如果环境没设、文件也不存在，生成 ``.master.key``。

        注意：这个方法**不会**被模块自动调用，避免生产误生成；
        只在用户主动点面板「启用加密」或跑 ``chayuan security init`` 时被调用。
        """
        if self._resolve_key() is not None:
            return None
        root = _resolve_root()
        if root is None:
            return None
        root.mkdir(parents=True, exist_ok=True)
        path = root / ".master.key"
        if path.exists():
            return path
        from cryptography.fernet import Fernet  # type: ignore
        key = Fernet.generate_key()
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(key)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        logger.warning(
            "已生成 master key 文件 %s（权限 0600）。"
            "生产环境建议把内容 export 到 CHAYUAN_MASTER_KEY 并删除文件。",
            path,
        )
        # 重置缓存以便下次调用立刻生效
        self._key = None
        self._fernet = None
        return path


def _resolve_root() -> Optional[Path]:
    """动态找当前 CHAYUAN_ROOT：env 优先，然后退到 settings（可能被 import 缓存）。"""
    root_env = os.environ.get("CHAYUAN_ROOT", "").strip()
    if root_env:
        return Path(root_env).expanduser()
    try:
        from chayuan.settings import CHAYUAN_ROOT as _ROOT
        return Path(_ROOT)
    except Exception:  # noqa: BLE001
        return None


def _coerce_key(raw: str) -> bytes:
    """把用户传的 key 归一化为 Fernet 接受的 32 字节 url-safe base64。"""
    raw = raw.strip()
    if len(raw) == 44 and raw.endswith("="):
        # 已经是 Fernet 标准格式
        return raw.encode("ascii")
    # 兼容：用户传了 hex 或纯 base64；都当 token 生成一遍 Fernet 标准 key
    import base64
    if len(raw) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw):
        return base64.urlsafe_b64encode(bytes.fromhex(raw))
    # 兜底：把传入字符串取 SHA-256 再编码
    import hashlib
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


# ---- 模块级单例 ----

_STORE: Optional[_SecretStoreImpl] = None


def get_store() -> _SecretStoreImpl:
    global _STORE
    if _STORE is None:
        _STORE = _SecretStoreImpl()
    return _STORE


# 便捷导出
def encrypt(plain: str) -> str:
    return get_store().encrypt(plain)


def decrypt(value: str) -> str:
    return get_store().decrypt(value)


def is_encrypted(value: str) -> bool:
    return get_store().is_encrypted(value)


def status() -> dict:
    """供面板 / doctor 读取当前加密状态。"""
    s = get_store()
    return {
        "enabled": s.enabled,
        "key_source": s.key_source,
    }


def _reset_for_tests() -> None:
    """测试专用：强制重置单例 + 清 env；不在生产调用。"""
    global _STORE
    _STORE = None
