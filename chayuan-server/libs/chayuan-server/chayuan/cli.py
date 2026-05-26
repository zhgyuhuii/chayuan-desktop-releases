import sys
from pathlib import Path

# 未 pip 安装时，在 libs/chayuan-server/chayuan 下直接执行 `python cli.py ...` 需将包根目录加入 path
_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))


def _diagnose_torch() -> "list[str]":
    """返回坏掉的症状列表;空列表 = torch 完好。"""
    try:
        import torch  # noqa: F401
    except ImportError:
        return []  # 完全没装 — 后续 import 会自然报清晰错误,这里不拦
    except Exception as e:
        return [f"import torch 失败: {type(e).__name__}: {e}"]
    bad_signs: list = []
    if not hasattr(torch, "SymInt"):
        bad_signs.append("torch.SymInt 缺失(C 扩展未加载完整)")
    try:
        _ = torch.tensor([1.0])
    except Exception as e:  # noqa: BLE001
        bad_signs.append(f"torch.tensor() 调用失败: {type(e).__name__}: {e}")
    return bad_signs


def _auto_repair_torch() -> int:
    """一键修复 torch 半装。返回 subprocess 退出码,0 = 成功。

    步骤:
      1. (Windows)杀同环境 Python 进程,释放 _C.pyd 锁
      2. pip uninstall -y torch torchvision torchaudio
      3. 删 site-packages\\torch* 残留 + ~* 残骸
      4. pip install --no-cache-dir torch==2.4.1 ... --index-url cpu

    在 cli.py preflight 里被调,不能 import chayuan.* 任何东西
    (避免再次触发坏 torch 的链式加载)。
    """
    import os
    import shutil
    import subprocess
    import sysconfig

    is_windows = sys.platform == "win32"

    sys.stdout.write("\n[一键修复] 步骤 1/4: 关闭可能占用 torch 的 Python 进程\n")
    sys.stdout.flush()
    if is_windows:
        # 不能杀自己 —— 用 -EA 0 容忍空集合,过滤掉当前 PID
        my_pid = os.getpid()
        ps = (
            f"Get-Process python,pythonw,jupyter*,chayuan* -EA 0 | "
            f"Where-Object {{ $_.Id -ne {my_pid} }} | "
            f"Stop-Process -Force -EA 0"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
    else:
        # *nix 上一般不需要杀进程,但仍尝试找出
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", "python|jupyter"], text=True
            ).split()
            others = [p for p in out if p and int(p) != os.getpid()]
            if others:
                sys.stdout.write(f"  发现 {len(others)} 个 Python 进程,跳过(*nix 一般不锁文件)\n")
        except Exception:  # noqa: BLE001
            pass

    sys.stdout.write("\n[一键修复] 步骤 2/4: 卸载 torch 系列\n")
    sys.stdout.flush()
    rc = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y",
         "torch", "torchvision", "torchaudio"],
        check=False,
    ).returncode
    if rc != 0:
        sys.stderr.write(
            f"\n[一键修复] ✗ 卸载失败(rc={rc})。\n"
            "  可能原因:文件被锁。请退出 IDE / Jupyter / 其它 chayuan 进程后重试。\n"
            "  Windows 用户:用 *管理员模式* 打开 PowerShell。\n"
        )
        return rc

    sys.stdout.write("\n[一键修复] 步骤 3/4: 清理残留目录(torch*, ~*)\n")
    sys.stdout.flush()
    site_pkgs = Path(sysconfig.get_paths()["purelib"])
    for pat in ("torch*", "~*"):
        for p in site_pkgs.glob(pat):
            if p.is_dir():
                sys.stdout.write(f"  删除 {p.name}\n")
                shutil.rmtree(p, ignore_errors=True)

    sys.stdout.write("\n[一键修复] 步骤 4/4: 重装 torch 2.4.1 (CPU 版)\n")
    sys.stdout.flush()
    rc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir",
         "torch==2.4.1", "torchvision==0.19.1", "torchaudio==2.4.1",
         "--index-url", "https://download.pytorch.org/whl/cpu"],
        check=False,
    ).returncode
    return rc


def _preflight_check_torch() -> None:
    """提早探测 torch 是否完整;残骸/半装会让后续 langchain/sentence-transformers
    导入抛奇怪栈(如 ``AttributeError: module 'torch' has no attribute 'SymInt'``)。

    检测到损坏时:
      1. 打印诊断信息
      2. 询问是否一键修复(交互式 TTY 才问)
      3. 用户同意 → 自动跑 _auto_repair_torch(),完成后让用户重启
      4. 用户拒绝 / 非交互式 → 打印手动步骤后 exit 2
    """
    bad_signs = _diagnose_torch()
    if not bad_signs:
        return

    import torch as _torch_mod  # 必然能 import,只是属性损坏

    sep = "=" * 72
    sys.stderr.write(f"\n{sep}\n")
    sys.stderr.write("[chayuan] 检测到 PyTorch 安装损坏 — 启动中止\n")
    sys.stderr.write(f"{sep}\n\n")
    sys.stderr.write("症状:\n")
    for sign in bad_signs:
        sys.stderr.write(f"    ✗ {sign}\n")
    sys.stderr.write(
        f"\n当前 torch 版本: {getattr(_torch_mod, '__version__', '<unknown>')}\n"
        f"安装位置:        {getattr(_torch_mod, '__file__', '<unknown>')}\n\n"
    )
    sys.stderr.write(
        "成因:通常是 pip 升级 torch 时被中断(Windows [WinError 5] / 权限不足),\n"
        "      留下半装的 site-packages 目录(_C.pyd 等 C 扩展未替换完整)。\n"
    )
    sys.stderr.write(f"{sep}\n")
    sys.stderr.flush()

    # 非交互模式(管道 / CI)不问,直接退出 + 打印手动步骤
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _print_manual_repair_steps()
        sys.exit(2)

    # 交互模式 — 问用户是否一键修复
    sys.stdout.write("\n是否立即一键修复(卸载 + 清残留 + 重装 torch 2.4.1 CPU 版)? [y/N]: ")
    sys.stdout.flush()
    try:
        choice = sys.stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        choice = ""

    if choice not in ("y", "yes", "是"):
        sys.stdout.write("\n已跳过自动修复。\n\n")
        _print_manual_repair_steps()
        sys.exit(2)

    rc = _auto_repair_torch()

    if rc != 0:
        sys.stderr.write(
            f"\n{sep}\n"
            f"[一键修复] ✗ 失败(退出码 {rc})。请按手动步骤修复:\n"
            f"{sep}\n"
        )
        _print_manual_repair_steps()
        sys.exit(rc)

    sys.stdout.write(
        f"\n{sep}\n"
        "[一键修复] ✓ torch 已重装完成。\n\n"
        "  本进程必须退出 — Python 已把坏 torch 加载到 sys.modules,\n"
        "  无法热替换。请在终端**重新执行**原命令:\n"
        "      python cli.py start -a\n"
        f"{sep}\n"
    )
    sys.exit(0)


def _print_manual_repair_steps() -> None:
    """提供给非交互模式 / 自动修复失败的用户。"""
    msg = (
        "\n手动修复步骤(Windows PowerShell,管理员模式):\n\n"
        "  1. 关闭所有 Python 进程:\n"
        "     Get-Process python,pythonw,jupyter*,chayuan* -EA 0 | Stop-Process -Force\n\n"
        "  2. 卸载 torch 系列:\n"
        "     pip uninstall -y torch torchvision torchaudio\n\n"
        "  3. 删残留目录:\n"
        "     Remove-Item -Recurse -Force \"<env>\\Lib\\site-packages\\torch*\"\n"
        "     Get-ChildItem \"<env>\\Lib\\site-packages\\~*\" -Directory -Force -EA 0 |\n"
        "         Remove-Item -Recurse -Force\n\n"
        "  4. 重装 torch 2.4.1 CPU 版:\n"
        "     pip install torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1 \\\n"
        "         --index-url https://download.pytorch.org/whl/cpu\n\n"
        "  5. 验证后重启:\n"
        "     python -c \"import torch; print(torch.__version__, hasattr(torch,'SymInt'))\"\n"
        "     python cli.py start -a\n\n"
        "Linux/macOS 等价(改 ps -ef + kill,删 site-packages 改 rm -rf)。\n"
        "详情:docs/contributing/README_dev.md / scripts/repair_torch.ps1\n"
    )
    sys.stderr.write(msg)


# 40 题 P1:torch preflight 改 **opt-in**,默认禁用。
# 原因:_diagnose_torch() 第一行就 ``import torch``,把整套 torch(~0.9 秒)拖
# 进所有 cli 子命令的启动开销。99% 时间 torch 是好的,这个校验是纯浪费。
# 仍提供两条触发路径:
#   1) 设环境变量 ``CHAYUAN_PREFLIGHT_TORCH=1`` — 部署 / CI 想强校验
#   2) 用户跑 ``chayuan doctor`` — doctor_cmd 内部已经会自己 _diagnose_torch
import os as _os_for_preflight

if (_os_for_preflight.environ.get("CHAYUAN_PREFLIGHT_TORCH") or "").strip() in ("1", "true", "yes"):
    _preflight_check_torch()


def _brandlock_startup_check() -> None:
    """品牌防篡改启动检查 — **已禁用(39 题)**。

    曾经在启动时跑 SHA-256 校验 + 打印 ``[chayuan-brandlock] ⚠ 检测到品牌资源被改动`` banner,
    现按用户要求关闭(不再校验,不再提示,不再 import brandlock 模块)。
    ``chayuan/brandlock/`` 包代码保留 — 如未来需要重启用,把函数体改回原检查逻辑即可。
    """
    return  # 不进行检测,不打印任何 banner


# 39 题:不再启动时校验品牌资源 — 调用保留为 no-op
_brandlock_startup_check()


import click
import shutil
import typing as t

# 40 题 P0.5:**仅顶层 import** click + 轻量公共依赖。其它 click 子命令模块
# (startup / init_database / cli_admin / cli_doctor / chat_doctor / cli_service)
# 全部走下面的 ``_LazyGroup`` 延迟加载 — ``chayuan --help`` 时不再触发它们。
from chayuan.settings import Settings
from chayuan.utils import build_logger


logger = build_logger()


class _LazyGroup(click.Group):
    """click Group 子类:子命令模块延迟到**真被调用**时才 import。

    ``lazy_subcommands`` 值支持两种形态:
      * ``"module.path:attr"`` — 字符串规范,attr 默认为 ``main``
      * ``callable`` — 任意零参函数,调用返回 click 命令对象(用于需要在加载
        后做"重命名 / 包装"等额外步骤的入口,如 ``ai-platform``)

    ``--help`` 列子命令时只用名字,**不会** import 任何子命令模块。
    若 callable / module 加载失败(例如可选包未装),静默从列表里移除该项,
    不报错也不阻断主 CLI。

    收益:``chayuan --help`` / 不触发命令模块的 cli 调用时间从 6.3s → 0.2-0.5s。
    """

    def __init__(self, *args, lazy_subcommands=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._lazy_subs: dict = dict(lazy_subcommands or {})

    def list_commands(self, ctx):  # type: ignore[override]
        return sorted(set(super().list_commands(ctx)) | set(self._lazy_subs))

    def get_command(self, ctx, cmd_name):  # type: ignore[override]
        if cmd_name in self._lazy_subs:
            spec = self._lazy_subs[cmd_name]
            try:
                if callable(spec):
                    return spec()
                import importlib
                modpath, _, attr = spec.partition(":")
                mod = importlib.import_module(modpath)
                return getattr(mod, attr) if attr else getattr(mod, "main")
            except Exception as e:  # noqa: BLE001
                # 可选子命令(如 ai-platform 包未装)— 静默不阻断 CLI,
                # 仅在用户实际敲它时给一行错误,而不是启动就崩。
                click.echo(
                    f"⚠ 子命令 {cmd_name!r} 不可用:{type(e).__name__}: {e}",
                    err=True,
                )
                return None
        return super().get_command(ctx, cmd_name)


def _lazy_load_ai_platform_cli():
    """40 题 P3:延迟加载 ai-platform CLI 子群组。

    原本在 cli.py 顶层 ``register_ai_platform_cli(main)`` 同步调用,无脑触发
    ``chayuan_cli → chayuan_discovery → chayuan_registry → sqlalchemy`` 链
    (实测 ~222 ms)。改成只在用户真敲 ``chayuan ai-platform <op>`` 时加载。

    返回:click 命令对象;chayuan_cli 包未装时由 _LazyGroup 处理为 None。
    """
    from chayuan.server.ai_platform import is_ai_platform_available
    avail = is_ai_platform_available()
    if not avail.get("chayuan_cli"):
        missing = ",".join(k for k, v in avail.items() if not v)
        raise ImportError(f"chayuan_cli 未安装(缺包:{missing})")
    from chayuan_cli.__main__ import cli as ai_cli
    # 改名以便 ``chayuan ai-platform --help`` 显示更清晰
    ai_cli.name = "ai-platform"
    return ai_cli


@click.group(
    cls=_LazyGroup,
    help="察元AI助手(chayuan)命令行工具",
    lazy_subcommands={
        # 40 题:每次只在用户跑这个子命令时才 import 模块,
        # `chayuan --help` 不会触发 langchain / torch / transformers 重链
        "start":       "chayuan.startup:main",
        "kb":          "chayuan.init_database:main",
        "status":      "chayuan.cli_admin:status_cmd",
        "user-info":   "chayuan.cli_admin:user_info_cmd",
        "update":      "chayuan.cli_admin:update_group",
        "auth-user":   "chayuan.cli_admin:auth_user_group",
        "doctor":      "chayuan.cli_doctor:doctor_cmd",
        "chat-doctor": "chayuan.chat_doctor:chat_doctor_cmd",
        "service":     "chayuan.cli_service:service_group",
        "model":       "chayuan.cli_service:model_group",
        # P3: ai-platform 也走 lazy — 222ms 的 chayuan_cli 链不再启动时拖
        "ai-platform": _lazy_load_ai_platform_cli,
    },
)
def main():
    ...


@main.command("init", help="项目初始化（默认走交互向导；-q 可跳过向导走旧的静默流程）")
@click.option("-x", "--xinference-endpoint", "xf_endpoint",
              help="指定Xinference API 服务地址。默认为 http://127.0.0.1:9997/v1")
@click.option("-l", "--llm-model",
              help="指定默认 LLM 模型。默认为 glm4-chat")
@click.option("-e", "--embed-model",
              help="指定默认 Embedding 模型。默认为 bge-large-zh-v1.5")
@click.option("-r", "--recreate-kb",
              is_flag=True,
              show_default=True,
              default=False,
              help="同时重建知识库（必须确保指定的 embed model 可用）。")
@click.option("-k", "--kb-names", "kb_names",
              show_default=True,
              default="samples",
              help="要重建知识库的名称。可以指定多个知识库名称，以 , 分隔。")
@click.option("-q", "--non-interactive", "non_interactive",
              is_flag=True, default=False,
              help="跳过交互式向导，按旧行为（使用当前 CHAYUAN_ROOT）直接生成模板。")
@click.option("--profile", "profile",
              type=click.Choice(["local", "prod", "enterprise"], case_sensitive=False),
              default=None,
              help="生成配置的 profile：\n"
                   "  local      = sqlite + faiss（默认，个人版用）；\n"
                   "  prod       = postgres + milvus + redis + 多 worker + 启用鉴权；\n"
                   "  enterprise = prod 的别名（企业版桌面安装包用）。\n"
                   "未设置时等价于 local；容器 / 桌面安装包可通过 CHAYUAN_INIT_PROFILE=prod 同样效果。")
def init(
    xf_endpoint: str = "",
    llm_model: str = "",
    embed_model: str = "",
    recreate_kb: bool = False,
    kb_names: str = "",
    non_interactive: bool = False,
    profile: t.Optional[str] = None,
):
    # profile 优先级：CLI 参数 > 环境变量 CHAYUAN_INIT_PROFILE > "local"
    # "enterprise" 是桌面企业版的别名，内部统一走 "prod" 代码路径；后续
    # 若企业版需要和 prod 不同的默认（如增加组织管理），再分叉。
    import os as _os
    effective_profile = (profile or _os.getenv("CHAYUAN_INIT_PROFILE") or "local").lower()
    if effective_profile == "enterprise":
        effective_profile = "prod"
    if effective_profile not in ("local", "prod"):
        effective_profile = "local"

    # 预先补齐运行时可选依赖（redis / arq 等）——让用户 init 完直接打开面板
    # 就能点「验证 Redis」/ 「启用异步入库」，不用自己再去 pip install。
    try:
        from chayuan.server.shared.deps import ensure_runtime_deps
        dep_status = ensure_runtime_deps()
        missing = [name for name, ok in dep_status.items() if not ok]
        if missing:
            logger.warning(
                "以下运行时可选依赖未就绪（已尝试自动安装），可稍后手动 pip install：%s",
                ", ".join(missing),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ensure_runtime_deps 异常（不影响后续流程）：{type(e).__name__}: {e}")

    # 交互式向导：问路径 / 账号，然后以子进程方式用正确 CHAYUAN_ROOT 完成实际初始化。
    if not non_interactive:
        from chayuan.init_wizard import run_init_wizard
        run_init_wizard(
            xf_endpoint=xf_endpoint or "",
            llm_model=llm_model or "",
            embed_model=embed_model or "",
            recreate_kb=bool(recreate_kb),
            kb_names=kb_names or "samples",
        )
        return

    Settings.set_auto_reload(False)
    bs = Settings.basic_settings
    kb_names = [x.strip() for x in kb_names.split(",")]
    logger.success(f"开始初始化项目数据目录：{Settings.CHAYUAN_ROOT}")
    Settings.basic_settings.make_dirs()
    logger.success("创建所有数据目录：成功。")
    if(bs.PACKAGE_ROOT / "data/knowledge_base/samples" != Path(bs.KB_ROOT_PATH) / "samples"):
        shutil.copytree(bs.PACKAGE_ROOT / "data/knowledge_base/samples", Path(bs.KB_ROOT_PATH) / "samples", dirs_exist_ok=True)
    logger.success("复制 samples 知识库文件：成功。")
    # 40 题:lazy import — 仅 init 命令真正执行到这里时才触发 migrate / langchain 链
    from chayuan.init_database import create_tables
    create_tables()
    logger.success("初始化知识库数据库：成功。")

    if xf_endpoint:
        Settings.model_settings.MODEL_PLATFORMS[0].api_base_url = xf_endpoint
    if llm_model:
        Settings.model_settings.DEFAULT_LLM_MODEL = llm_model
    if embed_model:
        Settings.model_settings.DEFAULT_EMBEDDING_MODEL = embed_model

    Settings.createl_all_templates()
    Settings.set_auto_reload(True)

    # --- 35 题:项目初始化时一次性写好运行时资产 yaml ---
    # <CHAYUAN_ROOT>/compose/docker-compose.yaml — 5 个 docker 服务编排
    # <CHAYUAN_ROOT>/runtime/<framework>.yaml    — 5 个 modality wrapper 配置
    # 全部幂等,已存在的不覆盖。
    try:
        from chayuan.server.config_panel.init_assets import (
            format_init_report,
            init_runtime_assets,
        )
        rpt = init_runtime_assets()
        logger.success("生成运行时资产 yaml:")
        for line in format_init_report(rpt).splitlines():
            logger.info(line)
        if rpt.errors:
            for err in rpt.errors:
                logger.warning(f"runtime asset 警告:{err}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"init_runtime_assets 失败(不影响主流程):{type(e).__name__}: {e}")

    # --- 生产 profile：把默认 sqlite+faiss 改为 postgres+milvus+redis ---
    if effective_profile == "prod":
        try:
            from chayuan.init_prod_profile import apply_prod_profile
            changed = apply_prod_profile()
            if changed:
                logger.success(
                    f"已按 prod profile 更新配置（postgres + milvus + redis + 多 worker），"
                    f"变更字段：{', '.join(sorted(changed.keys()))}"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"apply_prod_profile 失败（不影响 local 默认可用）：{type(e).__name__}: {e}")

    logger.success("生成默认配置文件：成功。")
    logger.success("请先检查确认 model_settings.yaml 里模型平台、LLM模型和Embed模型信息已经正确")

    # 首次 init 后补全面板凭据（username/password/secret/login_path）——交互向导会立刻
    # 被后续 `chayuan update` 覆盖，所以只有「-q 非交互」路径会真正用到这些随机值。
    try:
        from chayuan.server.config_panel.bootstrap import (
            ensure_panel_credentials,
            format_cli_banner,
        )
        br = ensure_panel_credentials()
        if br.did_change:
            click.echo()
            click.echo(format_cli_banner(br))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"bootstrap 面板凭据失败（不影响主流程）：{type(e).__name__}: {e}")

    if recreate_kb:
        # 40 题:lazy import — 重链(folder2db → langchain → torch)只在真重建 KB 时触发
        from chayuan.init_database import folder2db
        from chayuan.server.utils import get_default_embedding
        folder2db(kb_names=kb_names,
                  mode="recreate_vs",
                  vs_type=Settings.kb_settings.DEFAULT_VS_TYPE,
                  embed_model=get_default_embedding())
        logger.success("<green>所有初始化已完成，执行 chayuan start -a 启动服务。</green>")
    else:
        logger.success("执行 chayuan kb -r 初始化知识库，然后 chayuan start -a 启动服务。")


# 40 题:删除所有 ``main.add_command(...)`` — 全部走 _LazyGroup.lazy_subcommands 注册,
# 子命令模块在 ``main.get_command(name)`` 时才 import,显著减少 ``--help`` 启动时间。


# ---------------------------------------------------------------------------
# `chayuan stop` —— 粒度化停服
# ---------------------------------------------------------------------------

def _render_stop_outcome(outcome) -> None:
    """把 StopOutcome 渲染成终端友好文本（无需外部依赖 rich）。"""
    if outcome.parent_pid is not None:
        if outcome.parent_stopped:
            click.secho(
                f"✔ 父进程 pid={outcome.parent_pid} 已停止（{outcome.parent_detail}）",
                fg="green",
            )
        else:
            click.secho(
                f"✘ 父进程 pid={outcome.parent_pid} 停止失败：{outcome.parent_detail}",
                fg="red",
            )
    else:
        click.secho(f"- 父进程：{outcome.parent_detail}", fg="yellow")

    click.echo()
    click.echo("各服务状态：")
    for r in outcome.roles:
        color = {
            "stopped":     "green",
            "not_running": "yellow",
            "failed":      "red",
            "skipped":     "cyan",
        }.get(r.status, "white")
        marker = {
            "stopped":     "✔",
            "not_running": "○",
            "failed":      "✘",
            "skipped":     "»",
        }.get(r.status, "?")
        extra = []
        if r.pid:
            extra.append(f"pid={r.pid}")
        if r.port:
            extra.append(f"port={r.port}")
        if r.detail:
            extra.append(r.detail)
        click.secho(
            f"  {marker} {r.label:<18} [{r.status:<11}] " + "  ".join(extra),
            fg=color,
        )


@main.command("stop", help="停服：默认整体停；可按 --api / -c 单独停某个服务")
@click.option(
    "-a", "--all", "all_flag", is_flag=True,
    help="整体停（等价不加任何选项的默认行为）",
)
@click.option("--api", "stop_api", is_flag=True, help="仅停 API 服务（62581）")
@click.option(
    "-c", "--config", "stop_config", is_flag=True,
    help="仅停配置面板（8502）",
)
@click.option(
    "-f", "--force", is_flag=True,
    help="直接强杀（SIGKILL/taskkill /F），不给优雅退出时间",
)
@click.option(
    "--dry-run", "dry_run", is_flag=True,
    help="只列出当前运行中的服务，不执行停止",
)
def stop_cmd(all_flag: bool, stop_api: bool,
               stop_config: bool, force: bool, dry_run: bool):
    """统一停服入口。默认 = `stop --all`；多个 --api/-c 可组合（会按组合分别 stop）。"""
    from chayuan.server.config_panel.stop import (
        KNOWN_ROLES, status_snapshot, stop_all, stop_role,
    )

    # Dry-run：只打印状态
    if dry_run:
        click.echo("运行状态快照：")
        snap = status_snapshot()
        for role, info in snap.items():
            running = info["running"]
            color = "green" if running else "white"
            marker = "●" if running else "○"
            extra = []
            if info["pid"]:
                alive = "alive" if info["pid_alive"] else "dead"
                extra.append(f"pid={info['pid']}({alive})")
            if info["port"]:
                listen = "listen" if info["port_listen"] else "idle"
                extra.append(f"port={info['port']}({listen})")
            click.secho(
                f"  {marker} {info['label']:<18} " + "  ".join(extra),
                fg=color,
            )
        return

    selective = stop_api or stop_config
    if selective and all_flag:
        click.secho(
            "⚠ 同时指定 --all 与单个 --api/-c 没有意义；将按 --all 执行。",
            fg="yellow",
        )
        selective = False

    if not selective:
        # 整体停：走父进程 SIGTERM → 级联清理
        outcome = stop_all(force=force)
        _render_stop_outcome(outcome)
        sys.exit(0 if outcome.any_action or all([
            r.status == "not_running" for r in outcome.roles
        ]) else 1)

    # 粒度 stop：一条条按 role 处理
    roles_to_stop = []
    if stop_api:
        roles_to_stop.append("api")
    if stop_config:
        roles_to_stop.append("config")

    from chayuan.server.config_panel.stop import StopOutcome
    outcome = StopOutcome()
    for role in roles_to_stop:
        outcome.roles.append(stop_role(role, force=force))
    # 父进程保留；不渲染 parent 那一行
    outcome.parent_pid = None
    outcome.parent_detail = "保留（单独停子服务，父进程不退出）"
    _render_stop_outcome(outcome)
    any_failed = any(r.status == "failed" for r in outcome.roles)
    sys.exit(1 if any_failed else 0)


# ---------------------------------------------------------------------------
# `chayuan restart` —— 粒度化重启（复用 stop + 独立 spawn）
# ---------------------------------------------------------------------------

def _render_restart_role(result: dict) -> None:
    role = result.get("role", "")
    label = {
        "api": "API 服务", "config": "配置面板",
    }.get(role, role)
    listening = result.get("listening")
    new_pid = result.get("new_pid")
    port = result.get("port")
    detail = result.get("detail", "")

    stopped = result.get("stopped") or {}
    if stopped.get("status") == "stopped":
        click.secho(
            f"  ✔ 已停：{label} (pid={stopped.get('pid')}, {stopped.get('detail') or ''})",
            fg="cyan",
        )
    elif stopped.get("status") == "not_running":
        click.secho(f"  ○ 未运行：{label}（将直接启动）", fg="yellow")
    else:
        click.secho(
            f"  ⚠ 停服状态：{stopped.get('status')} {stopped.get('detail') or ''}",
            fg="yellow",
        )

    if listening:
        click.secho(
            f"  ✔ 新实例已启动：{label}  pid={new_pid}  port={port}",
            fg="green",
        )
    else:
        click.secho(
            f"  ✘ 新实例启动异常：{label}  pid={new_pid}  port={port}  {detail}",
            fg="red",
        )


@main.command("restart", help="重启服务：默认整体重启；可按 --api / -c 单独重启")
@click.option(
    "-a", "--all", "all_flag", is_flag=True,
    help="整体重启（等价不加任何选项的默认行为）",
)
@click.option("--api", "r_api", is_flag=True, help="仅重启 API 服务（62581）")
@click.option(
    "-c", "--config", "r_config", is_flag=True,
    help="仅重启配置面板（8502）",
)
@click.option(
    "-f", "--force", is_flag=True,
    help="stop 阶段强杀（SIGKILL / taskkill /F）",
)
@click.option(
    "--delay", type=float, default=1.0,
    help="整体重启时，SIGTERM 前守护脚本等待秒数（默认 1.0）",
)
@click.option(
    "--start-wait", "start_wait", type=float, default=30.0,
    help="单服务重启：等待新实例监听端口的最大秒数（默认 30）",
)
def restart_cmd(all_flag: bool, r_api: bool, r_config: bool,
                  force: bool, delay: float, start_wait: float):
    """粒度化重启：  
    - 无选项 / -a：复用原启动参数整体重启（通过守护脚本 kill 父进程再 start）  
    - --api/-c：只重启指定服务，保留其他 role 不变
    """
    from chayuan.server.config_panel.restart import restart_all, restart_role

    selective = r_api or r_config
    if selective and all_flag:
        click.secho(
            "⚠ --all 与 --api/-c 同时指定没有意义；按 --all 执行。",
            fg="yellow",
        )
        selective = False

    if not selective:
        # 整体重启（默认走逐 role 路径，最可靠）
        try:
            ret = restart_all(delay=delay, force=force, use_helper=False)
        except Exception as e:  # noqa: BLE001
            click.secho(f"✘ 重启失败：{type(e).__name__}: {e}", fg="red")
            sys.exit(1)

        mode = ret.get("mode")
        if mode == "helper":
            click.secho(
                f"✔ 已触发整体重启（守护脚本 pid={ret.get('helper_pid')}，"
                f"SIGTERM pid={ret.get('target_pid')} 后以原 argv 重启）",
                fg="green",
            )
            click.echo(f"  预计 5-30s 后新服务就绪；用 `chayuan stop --dry-run` 查看。")
            return

        # per_role 模式
        roles_planned = ret.get("roles_planned") or []
        click.echo(f"→ 整体重启：{', '.join(roles_planned)}")
        any_err = False
        any_fail = False
        for r in ret.get("roles") or []:
            if "error" in r:
                click.secho(f"  ✘ {r.get('role')}: {r['error']}", fg="red")
                any_err = True
                continue
            _render_restart_role(r)
            if not r.get("listening"):
                any_fail = True
        sys.exit(1 if (any_err or any_fail) else 0)

    # 粒度 restart
    roles: list = []
    if r_api:
        roles.append("api")
    if r_config:
        roles.append("config")

    any_fail = False
    for role in roles:
        click.echo(f"\n→ 重启 {role} ...")
        try:
            result = restart_role(role, force=force, start_wait_sec=start_wait)
        except Exception as e:  # noqa: BLE001
            click.secho(f"  ✘ 失败：{type(e).__name__}: {e}", fg="red")
            any_fail = True
            continue
        _render_restart_role(result)
        if not result.get("listening"):
            any_fail = True
    sys.exit(1 if any_fail else 0)


@main.command("tray", help="以系统托盘 / 菜单栏形态启动（安装包默认入口）")
def tray_cmd():
    """把原本的 `chayuan start -a` 包进系统托盘壳。

    - macOS：rumps 菜单栏图标（依赖 `pip install rumps`）；
    - Windows / Linux：pystray（依赖 `pip install pystray pillow`）。

    适合桌面安装包双击启动；菜单里提供「打开配置面板 / API 文档 /
    数据目录 / 日志 / 重启 / 退出」等操作。
    """
    from chayuan.tray import main as tray_main

    tray_main()


@main.command("install-pytorch",
              help="安装 PyTorch + torchvision（自己下 wheel + 解压，不用 pip）")
@click.option("--variant", default="cpu",
              type=click.Choice(["cpu", "cu118", "cu121", "cu124", "cu126", "auto"]),
              help="cpu(默认，~250MB) / cuXXX(CUDA 版，2-3GB) / auto(按 GPU 自动选)")
@click.option("--offline", is_flag=True, default=False,
              help="优先用内置 vendor/torch_wheels 里打包期预下的 torch/tv wheel（无网兜底）")
@click.option("--force", is_flag=True, default=False,
              help="已安装也强制重装")
def install_pytorch_cmd(variant: str, offline: bool, force: bool):
    """「安装 PyTorch」recipe 的真正落点 —— 纯 Python wheel 安装器。

    背景:旧实现让 frozen ``chayuan-server.exe -m pip install torch ...`` 跑 pip,
    但 pip 不支持被 PyInstaller freeze(``-m`` 不支持 / pip 没打进包 / distlib
    finder 失效)。改为这个 Click 子命令(Click 在 frozen 下工作正常),内部调
    ``pytorch_installer.install_pytorch_wheels()`` —— 下 wheel + zipfile 解压,绕开 pip。

    ``--variant`` 决定装 CPU 版还是 CUDA 版:cpu 走 ``/whl/cpu``,cuXXX 走
    ``/whl/cuXXX`` index。``auto`` 按本机 NVIDIA GPU + 驱动版本自动选。

    install_task_manager 的 recipe 会把本命令当一条 shell 命令 spawn,
    stdout 即任务日志。所以这里直接 ``click.echo`` 进度,不另搞日志通道。
    """
    from chayuan.server.runtime.pytorch_installer import (
        detect_status,
        install_pytorch_wheels,
    )

    def _log(msg: str) -> None:
        click.echo(msg)
        sys.stdout.flush()

    resolved = variant
    if variant == "auto":
        resolved = detect_status().suggested_variant
        _log(f"[install-pytorch] auto -> variant={resolved}")

    result = install_pytorch_wheels(
        variant=resolved,
        prefer_offline=bool(offline),
        log=_log,
        force=bool(force),
    )
    action = result.get("action")
    if action in ("installed", "skipped"):
        click.echo(f"[install-pytorch] {action}: {result}")
        sys.exit(0)
    click.echo(f"[install-pytorch] 失败: {result.get('error')}", err=True)
    sys.exit(1)


@main.command("worker", help="启动异步入库 worker（基于 Arq）。需配置 REDIS_URL 并 pip install arq")
@click.option("--burst", is_flag=True, default=False, help="跑完队列里现有任务就退出")
@click.option("--max-jobs", type=int, default=None, help="覆盖 ARQ_MAX_JOBS")
@click.option("--queue", "queue_name", type=str, default=None, help="指定队列名；可分别启动 ingest / embedding / office_vectorize / annotation worker")
def worker(burst: bool, max_jobs: t.Optional[int], queue_name: t.Optional[str]):
    """只依赖 arq 提供的 run_worker API；不走 arq cli，便于复用 chayuan 的 settings/logging。"""
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("arq", "arq>=0.25,<0.27")
        from arq.worker import run_worker  # type: ignore
    except ImportError:
        click.echo(
            "[chayuan worker] arq 未安装且自动安装失败。请执行 `pip install 'arq>=0.25,<0.27'` "
            "或关闭 INGEST_ASYNC_ENABLED。",
            err=True,
        )
        sys.exit(2)

    from chayuan.server.ingest_queue import worker_settings

    WS = worker_settings(queue_name=queue_name)
    if max_jobs is not None:
        WS.max_jobs = int(max_jobs)

    click.echo(
        f"[chayuan worker] queue={WS.queue_name} max_jobs={WS.max_jobs} burst={burst}"
    )
    run_worker(WS, burst=burst)


# 40 题 P3:删除原 `register_ai_platform_cli(main)` 同步注入 — 已改走
# `_LazyGroup.lazy_subcommands["ai-platform"] = _lazy_load_ai_platform_cli`,
# 仅当用户敲 `chayuan ai-platform <op>` 时才 import chayuan_cli(省 ~222 ms)。


if __name__ == "__main__":
    main()
