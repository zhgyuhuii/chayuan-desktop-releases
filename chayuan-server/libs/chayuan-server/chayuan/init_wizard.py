"""`chayuan init` 的交互式向导。

设计思路：
- ``Settings`` 在 ``chayuan.settings`` 被 import 时就通过环境变量 ``CHAYUAN_ROOT`` 固化了
  数据目录，因此「先问路径、再跑 init」必须通过子进程来完成——向导进程只负责交互，
  真正执行初始化、写配置的工作交给带了正确 env 的子进程。
- 子进程统一走 ``python -m chayuan.cli <sub>``，复用现有 ``init --non-interactive`` /
  ``update username / password / port / path`` 的实现，不重复造轮子。
- 流程：
    1. 交互式问 CHAYUAN_ROOT（支持默认值、已存在目录二次确认）；
    2. 问面板用户名、密码、端口、登录路径；
    3. 创建目录、复制默认模板（调 ``init --non-interactive``）；
    4. 依次调 ``chayuan update …`` 写入 basic_settings.yaml；
    5. 打印 export 指引并写 ``<CHAYUAN_ROOT>/activate.sh``；
    6. 询问是否立即 ``chayuan start -c``，是则 spawn 一个脱离当前进程组的后台服务。
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import click


_DEFAULT_USER = "admin"
_DEFAULT_PORT = 8502
_LOGIN_PATH_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _chayuan_pkg_parent() -> Path:
    """返回包含 ``chayuan/`` 包的父目录，供 PYTHONPATH 注入。

    让子进程无论是否 pip-install 了 ``chayuan`` 都能 ``import chayuan.cli``。
    """
    # 当前文件位于 <pkg_parent>/chayuan/init_wizard.py
    return Path(__file__).resolve().parent.parent


def _new_env(root: Path) -> dict:
    env = os.environ.copy()
    env["CHAYUAN_ROOT"] = str(root)
    # 告诉子进程 `paths.resolve_chayuan_root` 不要读 state.json 回退——向导本身正在
    # **写** last_init_root，子进程必须严格以这里显式指定的 root 为准，否则 init 过程
    # 中的 `init --non-interactive` / `update …` 都会指向上一次 init 的旧目录。
    env["CHAYUAN_ROOT_IGNORE_STATE"] = "1"
    # 直接 `python cli.py` 裸跑向导时，子进程用 `-m chayuan.cli` 需要包能被 import。
    pkg_parent = str(_chayuan_pkg_parent())
    prev = env.get("PYTHONPATH", "")
    parts = [pkg_parent, *(p for p in prev.split(os.pathsep) if p)]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _run(cmd: List[str], env: Optional[dict] = None, input_text: Optional[str] = None) -> None:
    """串行执行一个子命令；失败即 Abort。"""
    click.echo(click.style(f"  $ {' '.join(cmd)}", fg="bright_black"))
    try:
        r = subprocess.run(
            cmd, env=env, input=input_text, text=True,
            check=False, stdout=sys.stdout, stderr=sys.stderr,
        )
    except FileNotFoundError as e:
        raise click.ClickException(f"无法执行 {cmd[0]}：{e}")
    if r.returncode != 0:
        raise click.ClickException(
            f"子命令失败（exit={r.returncode}）：{' '.join(cmd)}"
        )


def _random_login_path(length: int = 8) -> str:
    return "".join(secrets.choice(_LOGIN_PATH_ALPHABET) for _ in range(length))


def _dir_is_nonempty(p: Path) -> bool:
    if not p.exists():
        return False
    try:
        return any(p.iterdir())
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 已存在数据目录的处理：覆盖 / 保留 / 取消
# ---------------------------------------------------------------------------
#
# 原来的行为是一个"是否继续 [y/N]"——回答 N 就直接 Abort，回答 Y 就覆盖。
# 实际使用里有一种很常见的场景：用户已经有一份跑得好好的 CHAYUAN_ROOT（自己
# 手工编辑过 yaml、塞过知识库），他想在这个目录上**沿用现有配置**、只把初始化
# 向导后续步骤（account 更新 / activate.sh / 启动）跑完；贸然覆盖会把用户的改动
# 抹掉。所以这里改成三选一：
#
#   1) 覆盖   —— 重新生成默认 yaml 模板（同名文件会被覆盖）
#   2) 保留   —— 沿用现有 yaml，但先校验"齐不齐、能不能被 pydantic 接受"
#   3) 取消   —— 终止初始化
#
# 校验分两层：
#   a) 必需 yaml 文件都存在，且 YAML 语法合法（用 PyYAML 纯解析）；
#   b) 起一个子进程把 Settings 真实 import 一遍，任何 pydantic ValidationError
#      都会透出到 stderr——这样 ``DEPLOYMENT_MODE='' / PANEL_LOGIN_PATH`` 非法
#      之类的坑能在向导阶段就被发现。
# 校验失败时，再弹一个小菜单让用户选：``r``etry 校验 / ``o``verwrite 改成覆盖
# / ``c``ancel 退出，避免一次失败就把向导整体打挂、用户失去已经填过的数据目录
# 输入。

_REQUIRED_YAMLS: tuple = (
    "basic_settings.yaml",
    "kb_settings.yaml",
    "model_settings.yaml",
    "prompt_settings.yaml",
    "tool_settings.yaml",
)


def _prompt_existing_dir_action() -> str:
    """问用户怎么处理已存在的非空 CHAYUAN_ROOT。返回 ``overwrite``/``keep``/``cancel``。"""
    click.echo("  请选择如何处理：")
    click.echo("    1) 覆盖 —— 重新生成默认 yaml 模板（同名文件会被覆盖，其它文件保留）")
    click.echo("    2) 保留 —— 沿用现有配置（会先校验文件是否完整 / 可被正确加载）")
    click.echo("    3) 取消 —— 终止初始化")
    choice = click.prompt(
        "  请选择",
        default="3",
        show_default=True,
        type=click.Choice(["1", "2", "3", "o", "k", "c"], case_sensitive=False),
    ).strip().lower()
    return {"1": "overwrite", "o": "overwrite",
            "2": "keep",      "k": "keep",
            "3": "cancel",    "c": "cancel"}[choice]


def _validate_existing_data_root(root: Path, env: dict) -> List[str]:
    """校验 ``root`` 里的配置是否可以直接被 chayuan 使用；返回错误消息列表（空 = OK）。"""
    errs: List[str] = []

    # (a) 必需文件 + YAML 语法
    for name in _REQUIRED_YAMLS:
        p = root / name
        if not p.is_file():
            errs.append(f"缺失必需文件：{name}")
            continue
        try:
            import yaml as _yaml  # 轻量依赖，chayuan 基础依赖已引入
            with open(p, "r", encoding="utf-8") as f:
                _yaml.safe_load(f)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name} YAML 解析失败：{type(e).__name__}: {e}")

    # 有基础错误就不再走 (b)，省得 pydantic 报一堆冗余级联错误。
    if errs:
        return errs

    # (b) 真实 import Settings，让 pydantic 校验跑一遍
    probe = (
        "from chayuan.settings import Settings\n"
        "Settings.basic_settings\n"
        "Settings.kb_settings\n"
        "Settings.model_settings\n"
        "Settings.prompt_settings\n"
        "Settings.tool_settings\n"
        "print('OK')\n"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", probe],
            env=env, input=None, text=True,
            capture_output=True, check=False,
        )
    except FileNotFoundError as e:
        errs.append(f"无法启动校验子进程：{e}")
        return errs

    if r.returncode != 0 or "OK" not in (r.stdout or ""):
        stderr = (r.stderr or "").strip() or "(无 stderr 输出)"
        # 只取最末 12 行，避免屏幕被 pydantic 的长栈刷爆
        tail = "\n".join(stderr.splitlines()[-12:])
        errs.append("pydantic 加载配置失败：\n" + tail)
    return errs


def _handle_existing_dir(root: Path, env_factory) -> bool:
    """处理非空目录的交互循环。返回 ``True`` 表示"保留现有配置，跳过模板生成"；
    返回 ``False`` 表示走正常覆盖流程。用户选择取消时直接 raise ClickException。
    """
    action = _prompt_existing_dir_action()
    if action == "cancel":
        raise click.ClickException("已取消。")
    if action == "overwrite":
        return False

    # ---- keep：循环校验 / 重试 / 兜底改覆盖 / 取消 ----
    env = env_factory()
    while True:
        click.echo("  正在校验现有配置……")
        errs = _validate_existing_data_root(root, env)
        if not errs:
            click.secho("  ✅ 现有配置校验通过，将跳过默认模板生成。", fg="green")
            return True
        click.secho("  ❌ 现有配置校验未通过：", fg="red")
        for e in errs:
            click.echo(f"    • {e}")
        nxt = click.prompt(
            "  选择下一步：[r] 我已修复，重试校验 / [o] 改为覆盖重新生成 / [c] 取消",
            default="c", show_default=True,
            type=click.Choice(["r", "o", "c"], case_sensitive=False),
        ).strip().lower()
        if nxt == "r":
            continue
        if nxt == "o":
            click.secho("  已切换为覆盖模式。", fg="yellow")
            return False
        raise click.ClickException("已取消。")


def run_init_wizard(
    *,
    xf_endpoint: str = "",
    llm_model: str = "",
    embed_model: str = "",
    recreate_kb: bool = False,
    kb_names: str = "samples",
) -> None:
    """由 cli.init(-i) 入口调用。"""

    click.echo()
    click.secho("==== 察元AI助手 · 初始化向导 ====", fg="cyan", bold=True)
    click.echo("将依次完成：数据目录 → 默认配置 → 面板账号 → （可选）启动面板。\n")

    # ---------- 1. 数据目录 ----------
    # 默认走 chayuan.paths 的平台感知解析，避免 cwd 依赖。
    from chayuan.paths import resolve_chayuan_root
    info = resolve_chayuan_root()
    default_root = info.path
    if info.source == "env":
        hint = "（来自当前 $CHAYUAN_ROOT）"
    else:
        hint = f"（{info.reason}）"
    click.echo(f"  推荐路径{hint}：{default_root}")
    raw = click.prompt(
        "① 数据目录 CHAYUAN_ROOT（所有 yaml 配置、知识库索引、日志均存放于此）",
        default=str(default_root),
        show_default=True,
    ).strip()
    root = Path(raw).expanduser().resolve()

    keep_existing = False
    if _dir_is_nonempty(root):
        click.secho(f"  目录已存在且非空：{root}", fg="yellow")
        # _handle_existing_dir 会弹三选一菜单；选"保留"时还会做一轮 pydantic 校验。
        # 这里把 env 的构造延后到真正需要时（_new_env 里 root 必须已存在），所以传个工厂。
        keep_existing = _handle_existing_dir(root, lambda: _new_env(root))
    root.mkdir(parents=True, exist_ok=True)

    env = _new_env(root)

    # 保留现有配置时，默认不再强制更新面板账号；但留一个可选项给用户。
    do_account_update = True
    if keep_existing:
        do_account_update = click.confirm(
            "  是否覆盖 basic_settings.yaml 里的面板账号 / 端口 / 登录路径？",
            default=False,
        )

    # ---------- 2. 面板账号（可选） ----------
    username = password = login_path = None
    port = _DEFAULT_PORT
    if do_account_update:
        click.echo()
        username = click.prompt("② 面板用户名", default=_DEFAULT_USER, show_default=True).strip() or _DEFAULT_USER

        import getpass
        while True:
            p1 = getpass.getpass("   面板密码（至少 6 位）: ")
            if not p1 or len(p1) < 6:
                click.secho("   密码不能为空且至少 6 位，请重试。", fg="red")
                continue
            p2 = getpass.getpass("   再次输入密码:         ")
            if p1 != p2:
                click.secho("   两次输入不一致，请重试。", fg="red")
                continue
            password = p1
            break

        port_raw = click.prompt("   面板端口", default=_DEFAULT_PORT, show_default=True, type=int)
        port = int(port_raw)
        if not (1 <= port <= 65535):
            raise click.ClickException("端口必须在 1–65535 之间。")

        login_path = click.prompt(
            "   登录路径（回车 = 随机 8 位小写字母；也可自定义，仅允许 [a-z0-9_-]{3,32}）",
            default="", show_default=False,
        ).strip().strip("/")
        if not login_path:
            login_path = _random_login_path()
    else:
        click.secho("② 面板账号：沿用 basic_settings.yaml 中已有配置，跳过询问。", fg="bright_black")

    # ---------- 3. 生成默认配置 ----------
    click.echo()
    if keep_existing:
        click.secho(
            "③ 保留现有配置，跳过默认模板生成。"
            "（如需重新初始化，请在下次 `chayuan init` 时选 [1] 覆盖）",
            fg="bright_black",
        )
    else:
        click.secho("③ 生成默认配置文件（会在子进程里以新 CHAYUAN_ROOT 重新导入 Settings）", fg="cyan")
        init_cmd = [sys.executable, "-m", "chayuan.cli", "init", "--non-interactive"]
        if xf_endpoint:
            init_cmd += ["-x", xf_endpoint]
        if llm_model:
            init_cmd += ["-l", llm_model]
        if embed_model:
            init_cmd += ["-e", embed_model]
        if kb_names and kb_names != "samples":
            init_cmd += ["-k", kb_names]
        if recreate_kb:
            init_cmd += ["-r"]
        _run(init_cmd, env=env)

    # ---------- 4. 写面板账号（可选） ----------
    click.echo()
    if do_account_update:
        click.secho("④ 写面板账号 / 端口 / 登录路径", fg="cyan")
        _run([sys.executable, "-m", "chayuan.cli", "update", "username", username], env=env)
        _run([sys.executable, "-m", "chayuan.cli", "update", "password", "--stdin"],
             env=env, input_text=password + "\n")
        _run([sys.executable, "-m", "chayuan.cli", "update", "port", str(port)], env=env)
        _run([sys.executable, "-m", "chayuan.cli", "update", "path", login_path], env=env)
    else:
        click.secho("④ 跳过 basic_settings.yaml 的账号 / 端口 / 登录路径写入。", fg="bright_black")
        # 保留模式 + 用户选择不更新账号：从 basic_settings.yaml 回读真实 port / login_path，
        # 后面步骤 7 打印「浏览器访问登录页」URL 时需要这俩字段；否则会打默认的 8502 / None。
        try:
            import yaml as _yaml
            with open(root / "basic_settings.yaml", "r", encoding="utf-8") as f:
                _basic = _yaml.safe_load(f) or {}
            _cfg_srv = _basic.get("CONFIG_SERVER") or {}
            port = int(_cfg_srv.get("port") or port)
            login_path = (_basic.get("PANEL_LOGIN_PATH") or "").strip("/") or None
        except Exception as e:  # noqa: BLE001
            click.secho(
                f"  （提示：回读 basic_settings.yaml 失败，后续打印的 URL 仅作参考：{type(e).__name__}: {e}）",
                fg="yellow",
            )

    # ---------- 5. 导出与 activate.sh ----------
    click.echo()
    click.secho("⑤ 环境变量", fg="cyan")
    click.echo(
        f"  为了让之后每次 `chayuan start` 都使用这份数据，请在你的 shell 里 export："
    )
    click.secho(f"    export CHAYUAN_ROOT={root}", fg="bright_green")
    click.echo("  也可以把上面这行加到 ~/.zshrc / ~/.bashrc。\n")

    activate = root / "activate.sh"
    activate.write_text(
        "#!/usr/bin/env bash\n"
        f"export CHAYUAN_ROOT=\"{root}\"\n"
        "echo \"CHAYUAN_ROOT=$CHAYUAN_ROOT\"\n",
        encoding="utf-8",
    )
    try:
        activate.chmod(0o755)
    except OSError:
        pass
    click.echo(f"  已额外写入：{activate}（source 后自动设置 env）")

    # 记录用户级 state —— 让后续 `chayuan start` / 面板能在「未 export」的终端里
    # 也能发现「向导刚刚选择的是 X，当前进程却跑在 Y」的不一致，给出有针对性提示。
    try:
        from datetime import datetime as _dt
        from chayuan.paths import write_user_state
        write_user_state({
            "last_init_root": str(root),
            "last_activate_sh": str(activate),
            "last_init_at": _dt.now().isoformat(timespec="seconds"),
        })
    except Exception as e:  # noqa: BLE001
        click.secho(f"  （提示：记录 user state 失败，不影响本次向导：{type(e).__name__}: {e}）",
                    fg="yellow")

    # ---------- 6. 面板信息 ----------
    click.echo()
    click.secho("⑥ 面板信息", fg="cyan")
    _run([sys.executable, "-m", "chayuan.cli", "user-info"], env=env)

    # ---------- 7. 立即启动 ----------
    click.echo()
    start_now = click.confirm(
        "⑦ 现在立即启动配置面板（`chayuan start -c`，脱离终端后台运行）？",
        default=True,
    )
    if start_now:
        log = root / "config_panel.out.log"
        click.echo(f"  日志：{log}")
        try:
            subprocess.Popen(
                [sys.executable, "-m", "chayuan.cli", "start", "-c"],
                env=env, cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=open(log, "ab"),
                stderr=subprocess.STDOUT,
                close_fds=True, start_new_session=True,
            )
        except Exception as e:  # noqa: BLE001
            raise click.ClickException(f"启动面板失败：{type(e).__name__}: {e}")
        click.secho(f"  面板已在后台启动（守护日志见上方路径）。", fg="green")
        if login_path:
            click.echo(f"  约 10 秒后可在浏览器访问登录页：http://127.0.0.1:{port}/{login_path}")
        else:
            click.echo(
                f"  约 10 秒后可在浏览器访问：http://127.0.0.1:{port}/<PANEL_LOGIN_PATH>"
                "  （请从 basic_settings.yaml 查看实际登录路径）"
            )
    else:
        click.echo("  若要手动启动：")
        click.secho(
            f"    export CHAYUAN_ROOT={root} && chayuan start -c",
            fg="bright_green",
        )

    click.echo()
    click.secho("初始化完成 ✨", fg="cyan", bold=True)
