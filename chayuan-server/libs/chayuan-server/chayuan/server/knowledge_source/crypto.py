"""连接密钥加解密。

所有 `knowledge_source_connection.password_enc` / `options_enc` 都走这里，
避免明文密码落 DB。密钥来源优先级（热到冷）：

1. 环境变量 CHAYUAN_SOURCE_SECRET_KEY —— 运维统一注入
2. basic_settings.CHAYUAN_SOURCE_SECRET_KEY
3. basic_settings.JWT_SECRET（回退；生产必须改）
4. 进程启动时生成临时密钥（仅适用 dev；重启会让历史密文失效 → 告警）

Fernet 要求 32 字节 urlsafe base64；不是合法 Fernet key 时自动做 SHA-256
派生并补齐。若 cryptography 未安装，降级为 base64 + XOR 混淆（仅防"误看"，
不是真正的密码学安全），并在日志里 WARN。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger("chayuan.knowledge_source.crypto")

# 尝试加载 cryptography.Fernet；未装则走降级
try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore

    _HAS_FERNET = True
except Exception:  # noqa: BLE001
    _HAS_FERNET = False
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore


_FERNET_SINGLETON = None
_FALLBACK_KEY_BYTES: Optional[bytes] = None


def _persisted_key_path() -> Optional[str]:
    """返回兜底持久化密钥文件路径。
    解析顺序:
      1. <CHAYUAN_ROOT>/data/.cy_source_secret_key (主路径,跟工程数据同根)
      2. ~/.chayuan/.cy_source_secret_key (CHAYUAN_ROOT 不可用时的兜底,
         保证用户主目录稳定)

    历史 BUG:之前从 Settings.basic_settings.CHAYUAN_ROOT 取 — 但 CHAYUAN_ROOT
    实际是 chayuan.settings 的 module-level 常量,BasicSettings 类上根本没
    这个属性,getattr 静默返 None → 密钥从未真正落盘 → 每次重启 _raw_key_bytes
    走 os.urandom 重新生成 → 历史 password_enc 全部解不开。
    """
    from pathlib import Path

    candidates: list[Path] = []
    try:
        from chayuan.settings import CHAYUAN_ROOT as _CR  # module-level 常量
        if _CR:
            candidates.append(Path(str(_CR)) / "data")
    except Exception:  # noqa: BLE001
        pass

    # 兜底:用户家目录,跨不同 cwd / 部署位置仍然稳
    try:
        candidates.append(Path.home() / ".chayuan")
    except Exception:  # noqa: BLE001
        pass

    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            return str(d / ".cy_source_secret_key")
        except Exception:  # noqa: BLE001
            continue
    return None


def _read_persisted_key() -> Optional[bytes]:
    p = _persisted_key_path()
    if not p:
        return None
    try:
        with open(p, "rb") as f:
            data = f.read().strip()
        return data or None
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("读取兜底密钥失败:%r", e)
        return None


def _write_persisted_key(raw: bytes) -> bool:
    p = _persisted_key_path()
    if not p:
        return False
    try:
        with open(p, "wb") as f:
            f.write(raw)
        # POSIX 权限收紧(Windows 上 chmod 是 best-effort)
        try:
            os.chmod(p, 0o600)
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("写兜底密钥失败:%r", e)
        return False


def _raw_key_bytes() -> bytes:
    key = os.environ.get("CHAYUAN_SOURCE_SECRET_KEY") or ""
    if not key:
        try:
            from chayuan.settings import Settings
            key = (
                getattr(Settings.basic_settings, "CHAYUAN_SOURCE_SECRET_KEY", None)
                or getattr(Settings.basic_settings, "JWT_SECRET", None)
                or ""
            )
        except Exception:  # noqa: BLE001
            key = ""
    if not key:
        # dev 兜底:优先读磁盘上一次生成并写入的随机密钥(self-heal 持久化),
        # 文件不存在再 os.urandom 一次并写盘,保证后续重启能解密历史密文。
        global _FALLBACK_KEY_BYTES
        if _FALLBACK_KEY_BYTES is None:
            persisted = _read_persisted_key()
            if persisted is not None:
                _FALLBACK_KEY_BYTES = persisted
            else:
                _FALLBACK_KEY_BYTES = os.urandom(32)
                wrote = _write_persisted_key(_FALLBACK_KEY_BYTES)
                sys.stderr.write(
                    "[chayuan][knowledge_source] ⚠️  未配置 CHAYUAN_SOURCE_SECRET_KEY / JWT_SECRET。"
                    + (
                        f"已自动生成并落盘到 {_persisted_key_path() or '<unknown>'},"
                        "重启可继续解密历史密文。"
                        if wrote else
                        "使用进程级临时密钥(无法落盘);重启后历史密文将无法解密。"
                    )
                    + "生产请在 basic_settings.yaml 或环境变量显式设置持久化密钥。\n"
                )
                sys.stderr.flush()
        else:
            # 上次进程因路径不可用而没落盘;现在有机会就再补一次,避免下次重启
            # 又重新随机。读盘上若已存在同样密钥,_write_persisted_key 是幂等的。
            try:
                p = _persisted_key_path()
                if p and not os.path.exists(p):
                    _write_persisted_key(_FALLBACK_KEY_BYTES)
            except Exception:  # noqa: BLE001
                pass
        return _FALLBACK_KEY_BYTES
    return key.encode("utf-8")


def _fernet() -> "Fernet":
    """返回单例 Fernet。未装 cryptography 时返回 None。"""
    global _FERNET_SINGLETON
    if not _HAS_FERNET:
        return None  # type: ignore[return-value]
    if _FERNET_SINGLETON is not None:
        return _FERNET_SINGLETON
    raw = _raw_key_bytes()
    # 统一用 SHA-256 派生 32 字节 → urlsafe_b64，保证一定是合法 Fernet key
    digest = hashlib.sha256(raw).digest()
    key = base64.urlsafe_b64encode(digest)
    _FERNET_SINGLETON = Fernet(key)
    return _FERNET_SINGLETON


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    if not key:
        return data
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def encrypt(plaintext: str) -> str:
    """加密；返回 ASCII 串。空串原样返回（避免给"无密码"连接加一堆噪声）。"""
    if plaintext in (None, ""):
        return ""
    data = plaintext.encode("utf-8")
    f = _fernet()
    if f is not None:
        return f.encrypt(data).decode("ascii")
    logger.warning(
        "cryptography 未安装，数据源密码使用弱混淆（仅防误看）；请安装 cryptography"
    )
    obf = _xor_bytes(data, hashlib.sha256(_raw_key_bytes()).digest())
    return "obf:" + base64.urlsafe_b64encode(obf).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """解密；解密失败返回空串并打 error 日志（不抛，避免 UI 列表直接 500）。"""
    if not ciphertext:
        return ""
    try:
        if ciphertext.startswith("obf:"):
            obf = base64.urlsafe_b64decode(ciphertext[4:])
            return _xor_bytes(obf, hashlib.sha256(_raw_key_bytes()).digest()).decode(
                "utf-8", errors="replace"
            )
        f = _fernet()
        if f is None:
            logger.error("cryptography 未安装但密文非降级格式，无法解密")
            return ""
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error("数据源密码解密失败：InvalidToken（密钥已更换？）")
        return ""
    except Exception as e:  # noqa: BLE001
        logger.error("数据源密码解密异常：%r", e)
        return ""
