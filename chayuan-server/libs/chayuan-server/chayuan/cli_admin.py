"""察元AI助手运维类子命令：status、user-info、update。

实现要点：
- 所有修改都写入 `basic_settings.yaml`，并用 ruamel 尽量保留原格式与注释；
- 密码永不落盘明文，仅保存 PBKDF2-SHA256 散列（见 server.config_panel.auth）；
- 任意配置更改后均需重启 `chayuan start` 才能生效。
"""
from __future__ import annotations

import getpass
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from chayuan.pydantic_settings_file import import_yaml
from chayuan.server.config_panel.auth import (
    generate_login_path,
    hash_password,
    normalize_login_path,
    validate_login_path,
)
from chayuan.server.utils import (
    api_address,
    config_panel_address,
    config_panel_login_url,
)
from chayuan.settings import CHAYUAN_ROOT, Settings
from chayuan.utils import build_logger

logger = build_logger()

_STATUS_CHOICES = ("core", "config")


def _basic_settings_path() -> Path:
    return Path(CHAYUAN_ROOT) / "basic_settings.yaml"


def _load_basic_doc() -> Tuple[Any, Path]:
    path = _basic_settings_path()
    if not path.is_file():
        raise click.ClickException(
            f"未找到配置文件：{path}。请先执行 `chayuan init` 生成默认配置。"
        )
    y = import_yaml()
    with open(path, encoding="utf-8") as f:
        doc = y.load(f)
    if doc is None:
        doc = {}
    return doc, path


def _save_basic_doc(doc: Any, path: Path) -> None:
    y = import_yaml()
    with open(path, "w", encoding="utf-8") as f:
        y.dump(doc, f)


def _probe_tcp(host: str, port: Any, timeout: float = 1.0) -> bool:
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    h = (host or "127.0.0.1").strip()
    if h == "0.0.0.0":
        h = "127.0.0.1"
    try:
        with socket.create_connection((h, p), timeout=timeout):
            return True
    except OSError:
        return False


def _status_line(name: str, ok: bool, detail: str) -> None:
    mark = "🟢" if ok else "🔴"
    click.echo(f"{mark} [{name:<6}] {detail}")


def _server_dict(name: str) -> Dict[str, Any]:
    val = getattr(Settings.basic_settings, name, None) or {}
    return dict(val) if isinstance(val, Mapping) else {}


@click.command(
    "status",
    help="查看服务状态：core=API(62581)，config=配置面板(8502)。不带参数时两项都查。",
)
@click.argument(
    "target",
    required=False,
    type=click.Choice(_STATUS_CHOICES, case_sensitive=False),
)
def status_cmd(target: Optional[str]):
    Settings.set_auto_reload(False)
    targets: List[str] = [str(target).lower()] if target else list(_STATUS_CHOICES)

    if "core" in targets:
        api = _server_dict("API_SERVER")
        host, port = str(api.get("host", "127.0.0.1")), api.get("port", 62581)
        ok = _probe_tcp(host, port)
        _status_line("core", ok, f"API  {host}:{port} {'运行中' if ok else '未运行'}")

    if "config" in targets:
        c = _server_dict("CONFIG_SERVER")
        host, port = str(c.get("host", "127.0.0.1")), c.get("port", 8502)
        ok = _probe_tcp(host, port)
        _status_line("config", ok, f"面板 {host}:{port} {'运行中' if ok else '未运行'}")

    Settings.set_auto_reload(True)


@click.command("user-info", help="查看配置面板访问地址与登录用户名（密码不展示）。")
def user_info_cmd():
    Settings.set_auto_reload(False)
    bs = Settings.basic_settings
    user = (getattr(bs, "PANEL_USERNAME", "") or "").strip()
    has_pw = bool((getattr(bs, "PANEL_PASSWORD_HASH", "") or "").strip())

    login_url = config_panel_login_url()
    login_path = (
        (getattr(bs, "PANEL_LOGIN_PATH", "") or "").strip().strip("/")
    )

    from chayuan.paths import resolve_chayuan_root
    root_info = resolve_chayuan_root()
    src_label = {
        "env": "来自 $CHAYUAN_ROOT",
        "xdg": "来自 $XDG_DATA_HOME（默认）",
        "macos": "macOS 默认",
        "windows": "Windows 默认",
        "linux": "Linux 默认（XDG）",
    }.get(root_info.source, root_info.source)

    click.echo("====== 察元AI助手 · 面板信息 ======")
    click.echo(f"数据目录 CHAYUAN_ROOT : {Settings.CHAYUAN_ROOT}  [{src_label}]")
    click.echo(f"API 地址              : {api_address()}")
    click.echo(f"配置面板(根)          : {config_panel_address()}")
    click.echo(f"配置面板登录 URL      : {login_url}")
    click.echo(f"登录路径段            : {login_path or '（未设置，将在首次启动时自动生成）'}")
    click.echo(f"面板用户名            : {user or '（未设置）'}")
    click.echo(f"面板密码              : {'已设置（已加盐散列存储）' if has_pw else '未设置'}")
    if not (user and has_pw):
        click.echo(
            "\n提示：面板用户名或密码未配置，配置面板将拒绝登录；"
            "请执行 `chayuan update username` 与 `chayuan update password` 完成设置。"
        )
    click.echo("\n说明：修改用户名、密码、端口或登录路径后需重启 `chayuan start -c` 或 `-a` 生效。")
    Settings.set_auto_reload(True)


# ---------- update ----------

def _set_config_port(doc: Dict[str, Any], port: int) -> None:
    c = doc.get("CONFIG_SERVER")
    if not isinstance(c, Mapping):
        c = {}
    c = dict(c)
    c["port"] = port
    doc["CONFIG_SERVER"] = c


@click.group(
    "update",
    invoke_without_command=True,
    help="修改配置面板用户名、密码、端口或登录路径（写入 basic_settings.yaml，需重启生效）。",
)
@click.pass_context
def update_group(ctx: click.Context):
    if ctx.invoked_subcommand is not None:
        return
    click.echo("请选择要修改的配置（直接回车取消）：")
    click.echo("  1) username — 配置面板用户名")
    click.echo("  2) password — 配置面板密码")
    click.echo("  3) port     — 配置面板端口")
    click.echo("  4) path     — 配置面板登录路径（留空则随机 8 位小写字母）")
    choice = click.prompt("序号", type=str, default="", show_default=False).strip()
    if choice == "1":
        ctx.invoke(update_username)
    elif choice == "2":
        ctx.invoke(update_password)
    elif choice == "3":
        ctx.invoke(update_port)
    elif choice == "4":
        ctx.invoke(update_path)
    elif choice == "":
        click.echo("已取消。")
    else:
        raise click.ClickException("无效序号，请输入 1、2、3 或 4。")


@update_group.command("username", help="修改配置面板用户名（写入 basic_settings.yaml）。")
@click.argument("name", required=False)
def update_username(name: Optional[str]):
    doc, path = _load_basic_doc()
    if not name:
        name = click.prompt("新的配置面板用户名").strip()
    if not name:
        raise click.ClickException("用户名不能为空。")
    doc["PANEL_USERNAME"] = name
    _save_basic_doc(doc, path)
    logger.success(f"已更新配置面板用户名：{name}（{path}）")
    click.echo("请重启 `chayuan start -c` 或 `-a` 使改动生效。")


@update_group.command(
    "password",
    help=(
        "修改配置面板密码（PBKDF2-SHA256 加盐散列存储）。"
        "不带参数时交互输入两次校对；"
        "也可直接传参：`chayuan update password <新密码>`（或 `-p <新密码>` / 从 stdin 读取）。"
    ),
)
@click.argument("password", required=False)
@click.option(
    "-p",
    "--password",
    "password_opt",
    default=None,
    help="直接通过命令行传入新密码（用于 CI/脚本；注意会进入 shell 历史）。",
)
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    default=False,
    help="从标准输入读取密码（推荐脚本化用法：`echo 新密码 | chayuan update password --stdin`）。",
)
def update_password(password: Optional[str], password_opt: Optional[str], from_stdin: bool):
    import sys

    doc, path = _load_basic_doc()

    pw: Optional[str] = None
    if password_opt is not None:
        pw = password_opt
    elif password is not None:
        pw = password
    elif from_stdin:
        pw = sys.stdin.readline().rstrip("\n").rstrip("\r")

    if pw is not None:
        if not pw.strip():
            raise click.ClickException("密码不能为空。")
        if len(pw) < 6:
            raise click.ClickException("密码长度不得少于 6 位。")
    else:
        p1 = getpass.getpass("新的配置面板密码: ")
        p2 = getpass.getpass("再次输入密码:     ")
        if p1 != p2:
            raise click.ClickException("两次输入不一致。")
        if not p1.strip():
            raise click.ClickException("密码不能为空。")
        if len(p1) < 6:
            raise click.ClickException("密码长度不得少于 6 位。")
        pw = p1

    doc["PANEL_PASSWORD_HASH"] = hash_password(pw)
    _save_basic_doc(doc, path)
    logger.success(f"已更新配置面板密码散列（{path}）。")
    click.echo("请重启 `chayuan start -c` 或 `-a` 使改动生效。")


@update_group.command("port", help="修改配置面板端口（CONFIG_SERVER.port，默认 8502）。")
@click.argument("port", required=False, type=int)
def update_port(port: Optional[int]):
    doc, path = _load_basic_doc()
    if port is None:
        port = click.prompt("新的配置面板端口", type=int)
    if port < 1 or port > 65535:
        raise click.ClickException("端口必须在 1–65535 之间。")
    _set_config_port(doc, port)
    _save_basic_doc(doc, path)
    logger.success(f"已更新配置面板端口：{port}（{path}）。")
    click.echo("请重启 `chayuan start -c` 或 `-a` 使改动生效。")


@update_group.command(
    "path",
    help=(
        "修改配置面板登录页路径（PANEL_LOGIN_PATH）。"
        "不带参数/传入空字符串时会随机生成 8 位小写字母；"
        "也可显式指定，字符限定 `[a-z0-9_-]{3,32}`。"
    ),
)
@click.argument("new_path", required=False)
def update_path(new_path: Optional[str]):
    doc, cfg_path = _load_basic_doc()

    if new_path is None:
        raw = click.prompt(
            "新的登录路径段（直接回车 = 随机生成 8 位小写字母）",
            type=str,
            default="",
            show_default=False,
        )
    else:
        raw = new_path

    seg = normalize_login_path(raw)

    if not seg:
        seg = generate_login_path()
        source = "随机生成"
    else:
        try:
            validate_login_path(seg)
        except ValueError as e:
            raise click.ClickException(str(e))
        source = "显式指定"

    doc["PANEL_LOGIN_PATH"] = seg
    _save_basic_doc(doc, cfg_path)

    base = config_panel_address()
    logger.success(
        f"已更新配置面板登录路径（{source}）：{seg}"
        f"（完整 URL：{base}/{seg}；{cfg_path}）"
    )
    click.echo("请重启 `chayuan start -c` 或 `-a` 使改动生效。")


# ---------- auth-user（对话界面 webui 登录账号管理） ----------
#
# 这里管的是 ``users`` 表里的业务账户（影响 API 鉴权），
# 与上面 ``user-info / update`` 管的「配置面板 basic-auth」是两套账号，不要混淆。
#
# 典型使用场景：
#   - 首次启动种子 admin 时用的是随机临时密码且只打印了一次，事后找不回密码；
#   - 数据库已存在但 basic_settings 里 ``AUTH_DEFAULT_ADMIN_PASSWORD`` 留空，
#     无法重启后自动重置；
#   - 忘记密码的普通用户账号。


@click.group(
    "auth-user",
    invoke_without_command=True,
    help="管理对话界面（webui / API）登录账号：重置密码 / 列表 / 新建。",
)
@click.pass_context
def auth_user_group(ctx: click.Context):
    if ctx.invoked_subcommand is not None:
        return
    click.echo("可用子命令：")
    click.echo("  chayuan auth-user list                          列出全部账号")
    click.echo("  chayuan auth-user reset-password <username>     重置指定账号密码")
    click.echo("  chayuan auth-user create <username> [--role]    新建账号")


def _require_db_ready() -> None:
    """确保 users 表已存在；否则提示用户先跑一次 `chayuan start` 建表。"""
    try:
        from chayuan.init_database import create_tables
        from chayuan.server.db.migrations import run_migrations

        create_tables()
        run_migrations()
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(
            f"初始化数据库表失败：{e!r}\n请先跑一次 `chayuan start -a` 让建表/迁移走完。"
        )


@auth_user_group.command("list", help="列出全部 webui 登录账号（不显示密码散列）。")
def auth_user_list_cmd():
    _require_db_ready()
    from chayuan.server.auth.service import list_users

    users = list_users(limit=1000, offset=0)
    if not users:
        click.echo("（暂无账号）")
        return
    click.echo(f"{'ID':<4} {'USERNAME':<24} {'ROLE':<8} {'ACTIVE':<8} {'LAST_LOGIN':<20}")
    for u in users:
        last = getattr(u, "last_login_at", None)
        click.echo(
            f"{u.id:<4} {u.username:<24} {(u.role or '-'):<8} "
            f"{'yes' if u.is_active else 'no':<8} {str(last or '-'):<20}"
        )


@auth_user_group.command(
    "reset-password",
    help="重置指定 webui 账号的密码（用于找回丢失的 admin 密码等场景）。",
)
@click.argument("username")
@click.option(
    "-p", "--password", "password",
    default=None,
    help="新密码（≥6 位）；不传则进入交互式二次确认。",
)
def auth_user_reset_password_cmd(username: str, password: Optional[str]):
    _require_db_ready()
    from chayuan.server.auth.service import get_user_by_username, set_password

    u = get_user_by_username(username.strip())
    if u is None:
        raise click.ClickException(f"账号不存在：{username!r}")

    if not password:
        password = click.prompt(
            f"为账号 {u.username!r}（id={u.id}, role={u.role}）设置新密码",
            hide_input=True, confirmation_prompt="再次输入确认",
        )

    if len(password) < 6:
        raise click.ClickException("密码长度必须 ≥ 6 位。")

    try:
        set_password(u.id, password)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"重置失败：{e!r}")

    click.echo(
        f"✅ 已重置账号 {u.username!r} 的密码。"
        f"请立刻到 webui 登录并在「用户名 ▾ → 修改密码」里改成只有你知道的强密码。"
    )


@auth_user_group.command("create", help="新建 webui 登录账号。")
@click.argument("username")
@click.option(
    "-p", "--password", "password",
    default=None,
    help="密码（≥6 位）；不传则进入交互式二次确认。",
)
@click.option(
    "-r", "--role", "role",
    type=click.Choice(["admin", "user"]),
    default="user", show_default=True,
    help="账号角色。",
)
@click.option(
    "-e", "--email", "email",
    default=None,
    help="可选邮箱。",
)
def auth_user_create_cmd(
    username: str,
    password: Optional[str],
    role: str,
    email: Optional[str],
):
    _require_db_ready()
    from chayuan.server.auth.service import create_user, get_user_by_username

    username = username.strip()
    if get_user_by_username(username) is not None:
        raise click.ClickException(f"账号已存在：{username!r}")

    if not password:
        password = click.prompt(
            f"为新账号 {username!r} 设置密码",
            hide_input=True, confirmation_prompt="再次输入确认",
        )
    if len(password) < 6:
        raise click.ClickException("密码长度必须 ≥ 6 位。")

    try:
        u = create_user(username, password, role=role, email=email)
    except Exception as e:  # noqa: BLE001
        raise click.ClickException(f"创建失败：{e!r}")

    click.echo(f"✅ 已创建账号 id={u.id}, username={u.username!r}, role={u.role}。")
