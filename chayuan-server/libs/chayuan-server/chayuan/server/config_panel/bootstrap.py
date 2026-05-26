"""首次 / 不完整状态下补全配置面板凭据。

核心函数 :func:`ensure_panel_credentials` 执行幂等 bootstrap：

- ``PANEL_USERNAME``   缺失 → 设置为 ``admin``
- ``PANEL_PASSWORD_HASH`` 缺失 → 生成 12 位随机密码并 PBKDF2 散列落盘
- ``PANEL_SESSION_SECRET`` 缺失 → 生成 64 字符十六进制
- ``PANEL_LOGIN_PATH``  缺失 → 生成 8 位小写字母

若本轮调用「生成了」新密码（即此前未配），会把明文密码、用户名、登录路径、
完整登录 URL 一并写入 ``<CHAYUAN_ROOT>/initial_credentials.txt``（``chmod 0600``）。
MinIO / Keycloak / Supabase 等项目使用类似模式：首次启动自动生成随机初始凭据，
用户首次登录后应当尽快改掉。

:func:`ensure_panel_credentials` 返回一个 :class:`BootstrapResult`，调用方可决定
如何展示给用户（CLI 打印 / 面板 banner / 日志）。
"""
from __future__ import annotations

import os
import secrets
import stat
import string
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from chayuan.server.config_panel import yaml_store
from chayuan.server.config_panel.auth import (
    generate_login_path,
    generate_session_secret,
    hash_password,
)


_PW_ALPHABET = string.ascii_letters + string.digits
_PW_LEN = 12


@dataclass
class BootstrapResult:
    """bootstrap 操作结果。"""

    yaml_path: Path
    changed_fields: List[str] = field(default_factory=list)
    # 明文密码仅在「本次」生成时非空；已有 hash 时保持空。
    generated_password: str = ""
    generated_username: str = ""
    generated_login_path: str = ""
    credentials_file: Path = None  # type: ignore[assignment]

    @property
    def did_change(self) -> bool:
        return bool(self.changed_fields)

    @property
    def has_initial_credentials(self) -> bool:
        return bool(self.generated_password)


def _random_password(length: int = _PW_LEN) -> str:
    return "".join(secrets.choice(_PW_ALPHABET) for _ in range(length))


def ensure_panel_credentials(*, filename: str = "basic_settings.yaml") -> BootstrapResult:
    """补全缺失的面板凭据；幂等。

    读取 yaml（保留注释、结构），只 patch 空字段，然后原子写回。
    """
    load = yaml_store.load_yaml(filename)
    doc = load.doc
    changes: dict = {}
    result = BootstrapResult(yaml_path=load.path)

    username = (yaml_store.get_by_path(doc, "PANEL_USERNAME") or "")
    if not str(username).strip():
        changes["PANEL_USERNAME"] = "admin"
        result.generated_username = "admin"
        result.changed_fields.append("PANEL_USERNAME")

    # password hash
    pw_hash = (yaml_store.get_by_path(doc, "PANEL_PASSWORD_HASH") or "")
    if not str(pw_hash).strip():
        pw_plain = _random_password()
        changes["PANEL_PASSWORD_HASH"] = hash_password(pw_plain)
        result.generated_password = pw_plain
        result.changed_fields.append("PANEL_PASSWORD_HASH")

    secret = (yaml_store.get_by_path(doc, "PANEL_SESSION_SECRET") or "")
    if not str(secret).strip():
        changes["PANEL_SESSION_SECRET"] = generate_session_secret()
        result.changed_fields.append("PANEL_SESSION_SECRET")

    login_path = (yaml_store.get_by_path(doc, "PANEL_LOGIN_PATH") or "")
    if not str(login_path).strip().strip("/"):
        seg = generate_login_path()
        changes["PANEL_LOGIN_PATH"] = seg
        result.generated_login_path = seg
        result.changed_fields.append("PANEL_LOGIN_PATH")

    if not changes:
        return result

    yaml_store.save_updates(filename, changes)

    if result.generated_password:
        _write_initial_credentials_file(result, doc_after=changes)

    return result


def _write_initial_credentials_file(
    result: BootstrapResult,
    doc_after: dict,
) -> None:
    """把明文初始密码写进 ``<root>/initial_credentials.txt``（``chmod 0600``）。"""
    root = result.yaml_path.parent
    path = root / "initial_credentials.txt"
    result.credentials_file = path

    username = doc_after.get("PANEL_USERNAME") or "admin"
    login_seg = doc_after.get("PANEL_LOGIN_PATH") or result.generated_login_path

    # 尽量拼出登录 URL；没法确定 host/port 时留占位。
    try:
        import importlib

        settings_mod = importlib.import_module("chayuan.settings")
        cfg = dict(getattr(settings_mod.Settings.basic_settings, "CONFIG_SERVER", {}) or {})
        host = cfg.get("public_host") or cfg.get("host") or "127.0.0.1"
        if host == "0.0.0.0":
            host = "127.0.0.1"
        port = cfg.get("port") or 8502
        login_url = f"http://{host}:{port}/{login_seg}"
    except Exception:  # noqa: BLE001
        login_url = f"http://<host>:<port>/{login_seg}"

    body = (
        "# 察元AI助手 · 初始登录凭据（自动生成）\n"
        f"# 生成时间：{datetime.now().isoformat(timespec='seconds')}\n"
        "# 警告：本文件包含明文密码！首次登录后请尽快用 `chayuan update password`\n"
        "#       改密，然后删除本文件。Git 请勿提交。\n"
        "\n"
        f"用户名       : {username}\n"
        f"密码         : {result.generated_password}\n"
        f"登录路径段    : {login_seg}\n"
        f"登录 URL      : {login_url}\n"
    )
    path.write_text(body, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass


def format_cli_banner(result: BootstrapResult) -> str:
    """给 CLI 用的多行提示（只在 did_change 时有意义）。"""
    if not result.did_change:
        return "面板凭据已齐备，无需 bootstrap。"

    lines = ["已自动补全面板配置："]
    for f in result.changed_fields:
        lines.append(f"  - {f}")
    if result.has_initial_credentials:
        lines += [
            "",
            "⚠️  已生成随机初始密码，明文凭据写入：",
            f"     {result.credentials_file}",
            "     权限 0600；首次登录后请尽快 `chayuan update password` 改掉并删除该文件。",
            "",
            f"  用户名     : {result.generated_username or 'admin'}",
            f"  密码       : {result.generated_password}",
        ]
        if result.generated_login_path:
            lines.append(f"  登录路径段 : {result.generated_login_path}")
    return "\n".join(lines)


__all__ = [
    "BootstrapResult",
    "ensure_panel_credentials",
    "format_cli_banner",
]
