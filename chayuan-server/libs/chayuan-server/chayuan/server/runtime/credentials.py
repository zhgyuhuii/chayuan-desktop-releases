"""自动凭据生成 + 持久化。

为什么不直接写到 yaml？
========================

* yaml 的"用户敏感字段"有日常被 git diff / dump 的诉求；密码暴露面太大。
* 多次 `chayuan init` 不希望覆盖运行时已经在用的密码（毁掉 docker volume
  里既存的数据库角色）。

折中方案：
* 凭据落到 **`<CHAYUAN_ROOT>/runtime.json`**（chmod 0600），与 yaml 分离；
* yaml / 环境变量 / docker-compose 都从 ``runtime.json`` 反查；
* 同一个 ``service_name`` 多次 ``ensure_credentials`` 返回相同的 user/pwd，
  避免半路改密码导致后端服务起不来。

公开 API：
* :class:`Credentials` —— ``user`` / ``password`` 不可变记录
* :func:`ensure_credentials` —— 主入口
* :func:`mask_password_in_url` —— 把 ``scheme://u:pw@host`` 中的密码替成 `****`
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import string
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlparse, urlunparse

logger = logging.getLogger("chayuan.runtime.credentials")

# 凭据用字符集刻意排除：
#   - URL/shell 元字符：@ : / ? # & = ; ' " | * < > $ ` % +
#   - 易混淆字符：0/O、1/l/I
# 这样生成出来的 password 直接塞进 ``redis://chayuan:<pwd>@host`` /
# 命令行 / yaml 都无需再做转义。
_PWD_ALPHABET = "".join(
    c for c in (string.ascii_letters + string.digits + "-_.~")
    if c not in "0Ol1I"
)


@dataclass(frozen=True)
class Credentials:
    """一组用户名 + 密码。"""

    user: str
    password: str


_LOCK = threading.Lock()


def _gen_password(length: int = 24) -> str:
    """生成对 URL / shell 安全的高熵密码。"""
    return "".join(secrets.choice(_PWD_ALPHABET) for _ in range(length))


def _default_user(service_name: str) -> str:
    """统一约定：service 名为前缀的小写用户名（PG 不允许 - / 大写时安全）。"""
    base = re.sub(r"[^a-zA-Z0-9_]", "_", service_name.lower())
    return f"chayuan_{base}" if base else "chayuan"


def ensure_credentials(
    service_name: str,
    *,
    runtime_info=None,           # type: ignore[assignment]  # forward decl, see runtime_info.py
    user: Optional[str] = None,
    password_length: int = 24,
    no_auth: bool = False,
) -> Credentials:
    """获取或创建 ``service_name`` 的凭据。

    流程：

    1. 若 ``no_auth=True`` → 返回 ``Credentials("", "")``，调用方应跳过把它写
       入 yaml / 环境变量。
    2. ``runtime.json`` 中已有该 service 的 ``user``/``password`` → 直接用
       （重启稳定）。
    3. 否则生成一个高熵密码，回写 ``runtime.json``，``chmod 0600``。

    Args:
        service_name: 唯一名（``postgres`` / ``redis`` / ``minio`` / ...）
        runtime_info: 可选注入；默认懒加载 :func:`get_runtime_info`，但允许
            测试传一个内存版替身。
        user: 可选指定用户名；不传走 :func:`_default_user`。
        password_length: 新密码长度（≥ 12）。
        no_auth: 该服务声明无需鉴权（如本地 Redis 默认 noauth），返回空凭据。
    """
    if no_auth:
        return Credentials("", "")

    if runtime_info is None:
        # 局部延迟导入避免循环依赖
        from chayuan.server.runtime.runtime_info import get_runtime_info
        runtime_info = get_runtime_info()

    with _LOCK:
        existing = runtime_info.get_credentials(service_name)
        if existing and existing.get("user") and existing.get("password"):
            return Credentials(
                user=str(existing["user"]),
                password=str(existing["password"]),
            )

        creds = Credentials(
            user=user or _default_user(service_name),
            password=_gen_password(max(12, int(password_length))),
        )
        runtime_info.set_credentials(service_name, creds.user, creds.password)
        try:
            os.chmod(runtime_info.path, 0o600)
        except OSError:
            # 只读文件系统 / 权限不允许：不抛错，仅 warn。Windows 上 chmod 通常 noop。
            pass

        logger.info(
            "[credentials] service=%s 首次生成凭据并写入 runtime.json (user=%s, password=****)",
            service_name, creds.user,
        )
        return creds


_URL_PWD_RE = re.compile(r"^(?P<scheme>[a-zA-Z][\w+.-]*://)(?P<user>[^:@/]+):(?P<pwd>[^@/]+)(?P<rest>@.*)$")


def mask_password_in_url(url: str, mask: str = "****") -> str:
    """把 ``scheme://user:password@host:port/path`` 中的 ``password`` 替成 mask。

    覆盖大多数 server URL（postgresql / redis / mongodb / minio / amqp / http
    basic auth）。无法解析的字符串原样返回（向上层暴露原文，避免吞掉异常字段）。
    """
    if not url or "://" not in url:
        return url
    m = _URL_PWD_RE.match(url)
    if not m:
        # 标准 urlparse 兜底
        try:
            p = urlparse(url)
            if p.password:
                netloc = (p.username or "") + ":" + mask
                if p.hostname:
                    netloc += "@" + p.hostname
                if p.port:
                    netloc += ":" + str(p.port)
                return urlunparse(p._replace(netloc=netloc))
        except Exception:  # noqa: BLE001
            pass
        return url
    return f"{m.group('scheme')}{m.group('user')}:{mask}{m.group('rest')}"


def url_quote_password(pw: str) -> str:
    """对密码做 URL 安全编码（凭据生成器已避开元字符，这里多一道保险）。"""
    return quote(pw, safe="")


__all__ = [
    "Credentials",
    "ensure_credentials",
    "mask_password_in_url",
    "url_quote_password",
]
