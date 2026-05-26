import asyncio
import logging
import logging.config
import multiprocessing as mp
import os
import sys
from contextlib import asynccontextmanager
from multiprocessing import Process

# 设置 numexpr 最大线程数,默认 = CPU 核心数。
# 41 题 P4:原本顶层 ``import numexpr`` 拖 ~43 ms 启动开销,只为读取核心数。
# 直接用 stdlib ``os.cpu_count()`` 完全等价(numexpr 自身 fallback 也是它),
# 零 import 成本。环境变量在 numexpr 真被业务代码加载时即被读到。
_cores = os.cpu_count() or 1
os.environ.setdefault("NUMEXPR_MAX_THREADS", str(_cores))

import click
from typing import Any, Dict, List, Optional

from fastapi import FastAPI

from chayuan.utils import build_logger


logger = build_logger()


_REQUIRED_ACTION_TEMPLATES = (
    "default",
    "platform-agent",
    "platform-knowledge-mode",
    "openai-functions",
    "qwen",
    "glm3",
    "structured-chat-agent",
)
_REQUIRED_RAG_TEMPLATES = ("default", "empty")
_REQUIRED_LLM_TEMPLATES = ("default",)


def _audit_prompt_templates() -> None:
    """
    启动期校验 CHAYUAN_ROOT/prompt_settings.yaml 的关键 section，缺失时：
      1. 打 WARNING 提示用户去补 YAML；
      2. 把 chayuan.settings.Settings.prompt_settings 里内嵌的默认值回填到内存态，
         让本次进程仍能跑起来（软着陆）。

    修的是历史遗留数据根（例如 chayuan_data/prompt_settings.yaml 还是旧 2.x 扁平结构、
    没有 platform-knowledge-mode）——这种场景以前会被 agents_registry._get_action_template
    静默降级成 "You are a helpful assistant"，导致 agent 的 system prompt 占位符渲染错位，
    模型只复述人设不回答用户问题。
    """
    try:
        from chayuan.settings import Settings
        ps = Settings.prompt_settings
    except Exception as e:  # noqa: BLE001
        logger.warning("[prompt-audit] 无法加载 Settings.prompt_settings，跳过校验：%s", e)
        return

    missing: List[str] = []

    def _audit_section(section: str, required_keys: tuple[str, ...], require_system_prompt: bool):
        section_obj = getattr(ps, section, None) or {}
        if not isinstance(section_obj, dict):
            logger.warning("[prompt-audit] prompt_settings.%s 不是字典，跳过", section)
            return
        for key in required_keys:
            tpl = section_obj.get(key)
            ok = False
            if isinstance(tpl, dict):
                ok = bool(tpl.get("SYSTEM_PROMPT")) if require_system_prompt else True
            elif isinstance(tpl, str):
                ok = bool(tpl.strip())
            if not ok:
                missing.append(f"{section}.{key}")

    _audit_section("action_model", _REQUIRED_ACTION_TEMPLATES, require_system_prompt=True)
    _audit_section("rag", _REQUIRED_RAG_TEMPLATES, require_system_prompt=False)
    _audit_section("llm_model", _REQUIRED_LLM_TEMPLATES, require_system_prompt=False)

    if missing:
        logger.warning(
            "[prompt-audit] prompt_settings.yaml 缺失 %d 个必需模板：%s；"
            "agents_registry 会在被调用时从 settings.py 内嵌默认回填，建议同步修复 YAML。",
            len(missing),
            ", ".join(missing),
        )
    else:
        logger.info("[prompt-audit] prompt_settings.yaml 必需模板齐全 (%d/%d)", 
                    len(_REQUIRED_ACTION_TEMPLATES) + len(_REQUIRED_RAG_TEMPLATES) + len(_REQUIRED_LLM_TEMPLATES),
                    len(_REQUIRED_ACTION_TEMPLATES) + len(_REQUIRED_RAG_TEMPLATES) + len(_REQUIRED_LLM_TEMPLATES))


def _pid_alive(pid: int) -> bool:
    """`os.kill(pid, 0)` 只做存活探测，不发信号。

    本函数是上一实例清理的优化路径,**任何**探测异常都当作"不可判定 → 跳过清理",
    绝不能挡住本次启动。

    历史踩坑:Windows + PyInstaller frozen exe + .chayuan_runtime.json 里残留
    旧 PID(被系统回收给特殊进程)时,``os.kill(pid, 0)`` 抛
    ``SystemError: <class 'OSError'> returned a result with an exception set``
    —— 不是 OSError 子类,原 ``except OSError`` 捕不住,sidecar 整个挂掉。
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但不是我们的——当作存活，后续 kill 可能会失败并被忽略
        return True
    except Exception:  # noqa: BLE001
        # OSError / SystemError / ValueError(pid 越界)/ 其它任何探测异常:
        # 都当"测不出来 → 不存在",让上层跳过清理。
        return False
    return True


def _looks_like_chayuan_proc(pid: int) -> bool:
    """尽量避免误杀：用 `ps -p <pid> -o command=` 读 cmdline，
    命中 `cli.py` 或 `chayuan` 才认为是自己之前启起来的父进程。

    读不到（比如进程刚死、ps 不可用）就保守返回 False。
    """
    try:
        import subprocess as _sp
        out = _sp.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            stderr=_sp.DEVNULL,
            timeout=2.0,
        ).decode("utf-8", errors="replace").strip().lower()
    except Exception:
        return False
    if not out:
        return False
    return ("cli.py" in out) or ("chayuan" in out)


def _kill_previous_instance(
    term_timeout: float = 6.0,
    kill_timeout: float = 2.0,
) -> None:
    """若 `$CHAYUAN_ROOT/.chayuan_runtime.json` 里记录的上一次 chayuan 父进程还活着，
    就 SIGTERM → 等待 → SIGKILL，保证同一个 CHAYUAN_ROOT 只有一个 chayuan 实例。

    父进程在 `start_main_server` 的 signal handler 里把 SIGTERM 翻译成 KeyboardInterrupt，
    走 `finally → _safe_kill` 级联清理所有 API/WebUI/配置面板子进程，因此只需杀父进程。

    任何异常（读文件失败、权限不足、ps 读不到 cmdline...）都吞掉，绝不挡住本次启动。
    """
    import signal as _sig
    import time as _time

    try:
        from chayuan.server.config_panel.restart import (
            load_runtime,
            runtime_meta_path,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("跳过旧实例清理：导入 restart 失败：%r", e)
        return

    try:
        meta = load_runtime()
    except Exception as e:  # noqa: BLE001
        logger.debug("跳过旧实例清理：读取 runtime meta 失败：%r", e)
        return

    if not meta:
        return

    try:
        pid = int(meta.get("pid") or 0)
    except (TypeError, ValueError):
        return
    if pid <= 0 or pid == os.getpid():
        return

    if not _pid_alive(pid):
        logger.debug("旧实例 pid=%d 已不存在，跳过。", pid)
        return

    if not _looks_like_chayuan_proc(pid):
        logger.warning(
            "检测到 %s 中记录 pid=%d 仍在运行，但命令行不像 chayuan 进程，"
            "为避免误杀已跳过。请手工确认或删除该文件后重试。",
            runtime_meta_path(), pid,
        )
        return

    logger.warning(
        "检测到上一次 `chayuan start` 的父进程仍在运行（pid=%d，argv=%s），"
        "发送终止信号让它清理并退出...",
        pid, meta.get("argv"),
    )

    # 跨平台停进程（Windows taskkill / POSIX SIGTERM→SIGKILL）
    try:
        from chayuan.server.shared.process_utils import terminate_pid
        ok, detail = terminate_pid(
            pid, force=False,
            term_timeout=term_timeout, kill_timeout=kill_timeout,
        )
        if ok:
            logger.info("旧实例 pid=%d 已退出：%s", pid, detail)
        else:
            logger.error(
                "旧实例 pid=%d 未能停止（%s），后续端口绑定可能失败，"
                "建议人工检查（Linux/macOS: ss -tlnp sport = :PORT；"
                "Windows: netstat -ano | findstr :PORT）。",
                pid, detail,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("发送终止信号失败（忽略，继续启动）：%r", e)


def _warn_stale_chayuan_root() -> None:
    """若向导最近一次选择的数据目录 ≠ 当前进程实际使用的 CHAYUAN_ROOT，就 warning。

    这一步只读取用户级 state.json（chayuan.paths.read_user_state）和当前
    Settings.CHAYUAN_ROOT，不会修改任何东西。任何异常都吞掉，不影响主启动路径。
    """
    try:
        from pathlib import Path as _P
        from chayuan.paths import read_user_state
        from chayuan.settings import CHAYUAN_ROOT as _ROOT

        state = read_user_state() or {}
        last = (state.get("last_init_root") or "").strip()
        activate = (state.get("last_activate_sh") or "").strip()
        env_val = (os.environ.get("CHAYUAN_ROOT") or "").strip()
        if not last:
            return

        def _norm(p: str) -> str:
            try:
                return str(_P(p).expanduser().resolve())
            except Exception:
                return p or ""

        if _norm(last) == _norm(str(_ROOT)):
            return

        logger.warning(
            "[chayuan] 检测到最近一次 `chayuan init` 选择的数据目录是 %s，"
            "但本次启动进程实际运行在 %s。通常意味着你在旧 shell 里直接 `chayuan start`，"
            "忘了先 `source %s` 或重开终端。",
            last, str(_ROOT), activate or f"export CHAYUAN_ROOT={last}",
        )
        if env_val and _norm(env_val) != _norm(last):
            logger.warning(
                "[chayuan] 当前 shell 的 $CHAYUAN_ROOT=%s 与向导选择也不一致——如需切换，"
                "请 Ctrl+C 停服 → `export CHAYUAN_ROOT=%s && chayuan start`。",
                env_val, last,
            )
    except Exception:
        # 这里吞掉是刻意的：诊断逻辑任何异常都不能挡启动
        pass


def _set_app_event(app: FastAPI, started_event: mp.Event = None):
    # 注意:FastAPI/Starlette 在设置 `router.lifespan_context` 后会**完全跳过**
    # `app.router.on_startup` / `on_shutdown` 里已注册的 handler。
    # 历史 bug:server_app.py 用 `@app.on_event("startup")` 注册了 7+ 个关键钩子
    # (DB 迁移、模型首启 seed、auto-start capabilities、WS hub、生命周期、config_center、
    # auto-start rapidocr),如果这里直接 `lifespan_context = lifespan` 而不调用它们,
    # 它们会被静默丢弃 → bundled_models 没 seed、本地服务不自启。
    # 修复:在 yield 前依次 await 所有 on_startup,yield 后依次 await on_shutdown。
    on_startup_handlers = list(app.router.on_startup)
    on_shutdown_handlers = list(app.router.on_shutdown)

    async def _run_handler(handler, phase: str) -> None:
        try:
            result = handler()
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:  # noqa: BLE001
            # 单个 hook 失败不阻塞其它 hook;但要打 ERROR 让运维能在日志里看到
            logger.error("[lifespan] %s hook %r failed: %r", phase, getattr(handler, "__qualname__", handler), e)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for handler in on_startup_handlers:
            await _run_handler(handler, "startup")
        if started_event is not None:
            started_event.set()
        try:
            yield
        finally:
            for handler in on_shutdown_handlers:
                await _run_handler(handler, "shutdown")
            # 关停本地 LLM runtime (级联 kill 3 个 llama-server.exe;Plan 3B 多 capability)
            try:
                from chayuan.server.model_registry.local_runtime_registry import get_registry
                await get_registry().stop_all()
            except Exception as e:  # noqa: BLE001
                logger.warning("[shutdown] stop local runtime failed: %r", e)

    app.router.lifespan_context = lifespan


def run_api_server(
    started_event: mp.Event = None, run_mode: str = None
):
    """启动 API 服务。

    单 worker 走原有 `uvicorn.run(app, ...)` 路径，`started_event` 走 lifespan set()；
    多 worker（UVICORN_WORKERS>1，生产推荐 `2*CPU+1`）改走 import-string 形式，
    因为 uvicorn 要求 `workers>1` 时 app 必须能跨进程重新导入。此时 started_event
    在父进程层面直接 set()（每个 worker 独立启动后由 LB 探活即可）。
    """
    import uvicorn
    from chayuan.utils import (
        get_config_dict,
        get_log_file,
        get_timestamp_ms,
    )

    from chayuan.settings import Settings
    from chayuan.server.utils import set_httpx_config

    logger.info(f"察元AI助手 API MODEL_PLATFORMS: {Settings.model_settings.MODEL_PLATFORMS}")
    set_httpx_config()
    _audit_prompt_templates()

    host = Settings.basic_settings.API_SERVER["host"]
    port = Settings.basic_settings.API_SERVER["port"]
    workers = int(getattr(Settings.basic_settings, "UVICORN_WORKERS", 1) or 1)

    logging_conf = get_config_dict(
        "INFO",
        get_log_file(log_path=Settings.basic_settings.LOG_PATH, sub_dir=f"run_api_server_{get_timestamp_ms()}"),
        1024 * 1024 * 1024 * 3,
        1024 * 1024 * 1024 * 3,
    )
    logging.config.dictConfig(logging_conf)  # type: ignore

    if workers > 1:
        # 多 worker：通过 import-string 让 uvicorn 在每个子进程里重建 app；
        # lifespan 设置在 `server_app.py` 模块级 app 上不会触发本函数的 started_event，
        # 这里在父进程直接 set()，等端口监听就绪即可。
        if started_event is not None:
            try:
                started_event.set()
            except Exception:
                pass
        logger.info("API Server 以多 worker 模式启动：workers=%d", workers)
        uvicorn.run(
            "chayuan.server.api_server.server_app:app",
            host=host,
            port=port,
            workers=workers,
        )
        return

    # 单 worker：保留原有行为（带 started_event 的 lifespan）。
    from chayuan.server.api_server.server_app import create_app

    app = create_app(run_mode=run_mode)
    _set_app_event(app, started_event)
    uvicorn.run(app, host=host, port=port)


def run_config_panel(
    started_event: mp.Event = None, run_mode: str = None
):
    """启动察元AI助手配置面板（NiceGUI）。

    本函数由 multiprocessing 以 spawn 方式在独立子进程中调用。
    子进程的异常/启动进度都显式写到 stderr，方便从父进程日志里追踪。
    """
    if sys.platform == "win32":
        try:
            import asyncio

            policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
            if policy_cls is not None:
                asyncio.set_event_loop_policy(policy_cls())
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[config panel] 设置 Windows 事件循环策略失败：{exc!r}\n")
            sys.stderr.flush()

    sys.stderr.write("[config panel] 子进程已启动，准备加载 NiceGUI...\n")
    sys.stderr.flush()
    try:
        _pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _pkg_root not in sys.path:
            sys.path.insert(0, _pkg_root)

        from chayuan.server.config_panel import run_config_panel as _run

        _run(started_event=started_event, run_mode=run_mode)
    except BaseException as e:
        import traceback

        sys.stderr.write(f"[config panel] 子进程启动失败：{e!r}\n")
        traceback.print_exc()
        sys.stderr.flush()
        if started_event is not None:
            try:
                started_event.set()
            except Exception:
                pass
        raise


def _probe_db_status(connect_timeout: int = 1) -> tuple[bool, str]:
    """快速探活业务库连接:返回 (ok, message)。

    用于启动尾端 banner —— 即使 DB 不通,进程也已经把配置面板拉起来了,
    所以这里只做诊断,**绝不抛异常**。

    41 题 P6:``connect_timeout`` 默认从 3 秒改到 1 秒 —— DB 完全不可达时
    父进程少卡 2 秒。生产 PG/MySQL 同机房通常 < 50ms,1s 已绰绰有余。
    """
    try:
        from sqlalchemy import create_engine, text
        from chayuan.settings import Settings

        uri = getattr(Settings.basic_settings, "SQLALCHEMY_DATABASE_URI", "") or ""
        if not uri:
            return False, "basic_settings.SQLALCHEMY_DATABASE_URI 为空"
        # connect_args.connect_timeout 仅 psycopg2/MySQL 接受;**SQLite 会抛**
        # ``TypeError: 'connect_timeout' is an invalid keyword argument for Connection()``
        # 所以按 URI scheme 走条件传参 — 不再"假设其它驱动忽略"。
        connect_args: dict = {}
        if uri.startswith(("postgresql", "postgres", "mysql")):
            connect_args["connect_timeout"] = connect_timeout
        # SQLite 没等价参数(本地文件 IO 几乎不会卡),不传即可
        eng = create_engine(uri, pool_pre_ping=True, connect_args=connect_args)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, uri
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# 41 题 P6:DB probe 起后台线程,父进程 banner 不阻塞等它。
# `dump_server_info(after_start=True)` 在打印 banner 末尾才取结果,
# 通常 banner 文本生成本身就要几十 ms,DB probe 已并行完成。
_DB_PROBE_FUTURE = None


def _kickoff_db_probe_async() -> None:
    """父进程在 spawn 子进程前启动一次 DB probe,banner 打印时拿结果。"""
    global _DB_PROBE_FUTURE
    if _DB_PROBE_FUTURE is not None:
        return
    try:
        from concurrent.futures import ThreadPoolExecutor
        # 单线程池 — daemon 模式,父退出时自动清理
        _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="db-probe")
        _DB_PROBE_FUTURE = _pool.submit(_probe_db_status, 1)
    except Exception as e:  # noqa: BLE001
        logger.debug("[db-probe] 起后台 thread 失败,fallback 同步:%r", e)


def _get_db_probe_result(wait_timeout: float = 1.5) -> tuple[bool, str]:
    """取 DB probe 结果。已起 future 就 wait;没起就同步跑。"""
    global _DB_PROBE_FUTURE
    if _DB_PROBE_FUTURE is None:
        return _probe_db_status()
    try:
        return _DB_PROBE_FUTURE.result(timeout=wait_timeout)
    except Exception as e:  # noqa: BLE001
        return False, f"db-probe timeout/error: {type(e).__name__}: {e}"


def dump_server_info(after_start=False, args=None):
    import platform

    import langchain

    from chayuan import __version__
    from chayuan.settings import Settings
    from chayuan.server.utils import (
        api_address,
        config_panel_address,
        config_panel_login_url,
    )

    print("\n")
    print("=" * 30 + "察元AI助手 配置" + "=" * 30)
    print(f"操作系统：{platform.platform()}.")
    print(f"python版本：{sys.version}")
    print(f"项目版本：{__version__}")
    print(f"langchain版本：{langchain.__version__}")
    print(f"数据目录：{Settings.CHAYUAN_ROOT}")
    print("\n")

    print(f"当前使用的分词器：{Settings.kb_settings.TEXT_SPLITTER_NAME}")

    print(f"默认选用的 Embedding 名称： {Settings.model_settings.DEFAULT_EMBEDDING_MODEL}")

    if after_start:
        print("\n")
        print(f"服务端运行信息：")
        if args.api:
            print(f"    察元AI助手 API: {api_address()}")
        if getattr(args, "config", False):
            print(f"    察元AI助手配置面板: {config_panel_address()}")
            print(f"    配置面板登录 URL  : {config_panel_login_url()}")

        # ===== DB 状态 + 不可达时的明确指引 =====
        # 41 题 P6:取 spawn 前已启动的 DB probe 结果 — 通常已并行完成,
        # 父进程不再为 DB 探活同步阻塞 1-3 秒。
        db_ok, db_info = _get_db_probe_result()
        print("")
        if db_ok:
            print(f"[OK] 业务库连接正常：{db_info}")
        else:
            cfg_url = config_panel_login_url() if getattr(args, "config", False) else "（未启动配置面板，请用 -c 启动）"
            # 用 stderr 着色（部分终端会高亮 ERR），并加显眼分隔
            sep = "!" * 78
            msg = (
                f"\n{sep}\n"
                f"[业务库不可达] 当前 SQLALCHEMY_DATABASE_URI 无法连接：\n"
                f"  原因：{db_info}\n"
                f"\n"
                f"  配置面板仍可用，请打开浏览器进入：\n"
                f"      {cfg_url}\n"
                f"  → 左侧「基础配置 / basic_settings.yaml」\n"
                f"  → 修改字段：SQLALCHEMY_DATABASE_URI\n"
                f"     （形如 postgresql+psycopg2://USER:PASS@HOST:PORT/DB）\n"
                f"  → 保存后回到本终端重启：python cli.py start -a\n"
                f"\n"
                f"  说明：API 服务的 DB 相关接口在修复前会返回 503。配置面板本\n"
                f"        身只读写 yaml，不依赖业务库，因此始终可用。\n"
                f"{sep}\n"
            )
            sys.stderr.write(msg)
            sys.stderr.flush()
            # 同时记日志，方便用 grep / file tail 在 CI 里捕获
            logger.warning(msg.replace("\n", " "))
    print("=" * 30 + "察元AI助手 配置" + "=" * 30)
    print("\n")


async def start_main_server(args):
    import signal
    import time

    from chayuan.utils import (
        get_config_dict,
        get_log_file,
        get_timestamp_ms,
    )

    from chayuan.settings import Settings

    logging_conf = get_config_dict(
        "INFO",
        get_log_file(
            log_path=Settings.basic_settings.LOG_PATH, sub_dir=f"start_main_server_{get_timestamp_ms()}"
        ),
        1024 * 1024 * 1024 * 3,
        1024 * 1024 * 1024 * 3,
    )
    logging.config.dictConfig(logging_conf)  # type: ignore

    shutdown_requested = False

    def handler(signalname):
        """
        Python 3.9 has `signal.strsignal(signalnum)` so this closure would not be needed.
        Also, 3.8 includes `signal.valid_signals()` that can be used to create a mapping for the same purpose.
        """

        def f(signal_received, frame):
            nonlocal shutdown_requested
            if not shutdown_requested:
                logger.warning("%s received, shutting down child processes...", signalname)
            shutdown_requested = True

        return f

    # This will be inherited by the child process if it is forked (not spawned)
    signal.signal(signal.SIGINT, handler("SIGINT"))
    signal.signal(signal.SIGTERM, handler("SIGTERM"))

    mp.set_start_method("spawn")
    manager = mp.Manager()
    run_mode = None

    if args.all:
        args.api = True
        args.config = True

    # 41 题 P6:DB probe 提前在后台 thread 跑,几秒后才在 banner 末尾用结果。
    # 这样即便 DB 不可达,父进程的"打印 banner → spawn 子进程"流程不阻塞。
    _kickoff_db_probe_async()

    dump_server_info(args=args)

    # 端口预检:把「上次没退干净的 chayuan 孤儿进程还占着端口」这种高频事故在
    # spawn 前拦下,给出可直接 copy 的 kill 命令,避免 uvicorn 再吐一整屏堆栈。
    _preflight_port_check(args)

    # ---- 运行时端口/凭据自检（T-runtime）-----------------------------------
    # 把 API_SERVER / CONFIG_SERVER 端口过一遍 PortAllocator：被占就在
    # PORT_RANGE 内自动 bump，结果落到 <CHAYUAN_ROOT>/runtime.json 让下次
    # 重启稳定复用，并打印一张"最终端口/凭据"表方便用户立刻看到。
    try:
        from chayuan.server.runtime import allocate_core_ports, render_endpoints_table
        rt_result = allocate_core_ports()
        if rt_result.warnings or rt_result.api.port != int((Settings.basic_settings.API_SERVER or {}).get("port") or 0):
            print("\n[runtime] 服务端口 / 凭据：")
            print(render_endpoints_table(rt_result))
            print("")
    except Exception as e:  # noqa: BLE001
        logger.warning("[runtime] allocate_core_ports 失败（不影响启动）：%s", e)

    # 启动模型目录的实时感知（开发期把新模型扔进 models/ 自动入库）
    try:
        from chayuan.server.model_registry.watcher import start_default_watcher
        start_default_watcher()
    except Exception as e:  # noqa: BLE001
        logger.warning("[model-watcher] 启动失败（不影响主链路）：%s", e)

    if len(sys.argv) > 1:
        logger.info(f"正在启动服务：")
        logger.info(f"如需查看 llm_api 日志，请前往 {Settings.basic_settings.LOG_PATH}")

    processes = {}

    def process_count():
        return len(processes)

    api_started = manager.Event()
    if args.api:
        process = Process(
            target=run_api_server,
            name=f"API Server",
            kwargs=dict(
                started_event=api_started,
                run_mode=run_mode,
            ),
            daemon=False,
        )
        processes["api"] = process

    config_started = manager.Event()
    if getattr(args, "config", False):
        process = Process(
            target=run_config_panel,
            name="察元AI助手配置面板",
            kwargs=dict(
                started_event=config_started,
                run_mode=run_mode,
            ),
            daemon=True,
        )
        processes["config"] = process

    # 启动等待超时（秒）；轮询子进程状态，提前失败就立刻抛出，避免卡住不可操作。
    _WAIT_TIMEOUT = 30.0
    _CONFIG_WAIT_TIMEOUT = 10.0

    # 辅助：把刚 spawn 的子进程 pid + 端口写入 runtime meta 的 children 表，
    # 供 `chayuan stop --<role>` 精确定位进程。失败不影响启动。
    def _register_child_quiet(role: str, pid: int, port: Optional[int],
                                 name: str) -> None:
        try:
            from chayuan.server.config_panel.restart import register_child
            register_child(role, pid, port=port, name=name)
        except Exception as _e:  # noqa: BLE001
            logger.debug("register_child(%s) 失败（忽略）：%r", role, _e)

    def _port_of(key: str, default: int) -> Optional[int]:
        try:
            srv = getattr(Settings.basic_settings, key, {}) or {}
            p = srv.get("port") if isinstance(srv, dict) else None
            return int(p) if p else default
        except Exception:  # noqa: BLE001
            return default

    def _wait_child_started(role: str, p: Process, event: Any, timeout: float) -> bool:
        """等待启动事件；子进程提前退出时立即报错，不等待完整 timeout。"""
        import time

        deadline = time.monotonic() + max(0.1, float(timeout or 0.1))
        while time.monotonic() < deadline:
            if event.is_set():
                p.join(0.2)
                if not p.is_alive() and p.exitcode not in (None, 0):
                    raise RuntimeError(f"{role} 启动失败，子进程已退出，exitcode={p.exitcode}")
                return True
            if not p.is_alive():
                raise RuntimeError(f"{role} 启动失败，子进程已退出，exitcode={p.exitcode}")
            time.sleep(0.2)
        return False

    try:
        if p := processes.get("api"):
            p.start()
            p.name = f"{p.name} ({p.pid})"
            _register_child_quiet(
                "api", int(p.pid or 0), _port_of("API_SERVER", 62581), p.name,
            )
            if not _wait_child_started("API", p, api_started, _WAIT_TIMEOUT):
                logger.warning(
                    "API 启动事件 %.0fs 内未收到信号，继续启动下一个进程（端口可能尚未就绪）。",
                    _WAIT_TIMEOUT,
                )

        if p := processes.get("config"):
            p.start()
            p.name = f"{p.name} ({p.pid})"
            _register_child_quiet(
                "config", int(p.pid or 0), _port_of("CONFIG_SERVER", 8502), p.name,
            )
            if not _wait_child_started("配置面板", p, config_started, _CONFIG_WAIT_TIMEOUT):
                logger.warning(
                    "配置面板启动事件 %.0fs 内未收到信号，请用 `chayuan status config` 核对。",
                    _CONFIG_WAIT_TIMEOUT,
                )

        dump_server_info(after_start=True, args=args)

        while processes and not shutdown_requested:
            for name, p in list(processes.items()):
                p.join(2)
                if not p.is_alive():
                    processes.pop(name, None)
    except (KeyboardInterrupt, Exception) as e:
        logger.error(e)
        logger.warning("Caught KeyboardInterrupt! Setting stop event...")
    finally:
        for p in list(processes.values()):
            try:
                if isinstance(p, dict):
                    for process in p.values():
                        _safe_kill(process)
                else:
                    _safe_kill(p)
            except Exception as e:
                logger.warning("kill 子进程异常（忽略继续）：%r", e)

        for p in list(processes.values()):
            try:
                logger.info("Process status: %s (alive=%s)", p, getattr(p, "is_alive", lambda: False)())
            except Exception:
                pass
        try:
            manager.shutdown()
        except Exception as e:  # noqa: BLE001
            logger.debug("multiprocessing manager shutdown failed (ignored): %r", e)


def _safe_kill(p) -> None:
    """对 `multiprocessing.Process` 做防御式 kill：未启动（_popen 为 None）时跳过。"""
    try:
        if getattr(p, "_popen", None) is None:
            logger.info("进程 %s 尚未启动，跳过 kill。", getattr(p, "name", p))
            return
        logger.warning("Sending SIGKILL to %s", p)
        p.kill()
    except Exception as e:
        logger.warning("kill %s 失败（忽略）：%r", getattr(p, "name", p), e)


# ---------------------------------------------------------------------------
# 端口预检：避免把一整面 uvicorn traceback 抛到终端
# ---------------------------------------------------------------------------
# 典型场景（本次的实际 bug）：上一轮 `chayuan start` 被终端关闭/主进程被
# kill，但子进程 PPID 被托管给 init（PPID=1）继续占着端口。下一次再跑时
# uvicorn 在 startup() 里才发现 EADDRINUSE 抛 SystemExit，日志里只能看到
# 噪声堆栈。这里在 spawn 子进程前做一次 connect() 探活，若某端口已有监听
# 就用 `lsof` 问出是谁，对「像是察元遗留进程」给出精确的 `kill PID`。


def _pid_on_port(port: int) -> int | None:
    """查询当前 LISTEN 在给定 TCP 端口的 PID。仅用于出错诊断，失败返回 None。"""
    try:
        import subprocess

        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=2,
        )
        line = out.stdout.strip().splitlines()
        if line:
            return int(line[0].strip())
    except Exception:
        return None
    return None


def _cmdline_of(pid: int) -> str:
    try:
        import subprocess

        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _probe_port_conflicts(plan) -> "list[tuple[str, str, int, int | None, str]]":
    """对 plan 里的每个端口做一次 connect() 探活，返回冲突列表。

    plan = [(name, cfg_dict), ...]；cfg_dict 至少含 host / port。
    """
    import socket

    problems: list[tuple[str, str, int, int | None, str]] = []
    for name, cfg in plan:
        host = str(cfg.get("host") or "0.0.0.0").strip()
        try:
            port = int(cfg.get("port"))
        except (TypeError, ValueError):
            continue
        probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        try:
            with socket.create_connection((probe_host, port), timeout=0.3):
                in_use = True
        except OSError:
            in_use = False
        if in_use:
            pid = _pid_on_port(port)
            cmd = _cmdline_of(pid) if pid else ""
            problems.append((name, host, port, pid, cmd))
    return problems


def _format_port_conflicts(problems, *, interactive_hint: bool) -> str:
    """把冲突列表格式化成人类可读的多行字符串。"""
    lines = ["检测到即将绑定的端口已被占用，启动已中止（避免后续 uvicorn 抛长堆栈）。"]
    for name, host, port, pid, cmd in problems:
        lines.append(f"  • {name}  {host}:{port}")
        if pid is None:
            lines.append(
                "      ↳ lsof 查不到具体 PID（或没装 lsof）；可手动："
                f" `lsof -nP -iTCP:{port} -sTCP:LISTEN`"
            )
            continue
        trimmed = cmd if len(cmd) <= 100 else cmd[:97] + "..."
        if "chayuan" in (cmd or "").lower():
            lines.append(
                f"      ↳ 是上次没退干净的察元进程 PID={pid}："
            )
            lines.append(f"        {trimmed}")
            if not interactive_hint:
                lines.append(
                    f"      修复：`kill {pid}`（如仍占用则 `kill -9 {pid}`）"
                )
        else:
            lines.append(
                f"      ↳ 被其它进程占用 PID={pid}：{trimmed}"
            )
            lines.append(
                "      修复：关掉该进程，或到「数据目录 & 基础配置 → 服务器绑定地址」"
                "改掉对应端口"
            )
    return "\n".join(lines)


def _kill_pids(pids, term_timeout: float = 4.0, kill_timeout: float = 2.0) -> None:
    """对一组 PID 先 SIGTERM → 等 → 还没死就 SIGKILL。失败忽略。"""
    import signal as _sig
    import time as _time

    if not pids:
        return
    alive = list(dict.fromkeys(int(p) for p in pids if p))  # 去重、保序
    # SIGTERM
    for pid in alive:
        try:
            os.kill(pid, _sig.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("SIGTERM pid=%d 失败：%r", pid, e)

    deadline = _time.monotonic() + term_timeout
    while _time.monotonic() < deadline:
        alive = [p for p in alive if _pid_alive(p)]
        if not alive:
            return
        _time.sleep(0.2)

    # SIGKILL 兜底
    for pid in alive:
        logger.warning("pid=%d 未在 %.1fs 内退出，发送 SIGKILL。", pid, term_timeout)
        try:
            os.kill(pid, _sig.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("SIGKILL pid=%d 失败：%r", pid, e)

    deadline = _time.monotonic() + kill_timeout
    while _time.monotonic() < deadline:
        alive = [p for p in alive if _pid_alive(p)]
        if not alive:
            return
        _time.sleep(0.1)


def _preflight_port_check(args) -> None:
    """`spawn` 子进程之前，对每个计划绑定的端口做一次探活。

    三种结果：
    1. 无冲突 → return；
    2. **冲突全部来自察元自己的残留进程** + stdin 是 TTY → 友好打印冲突清单后
       ``click.confirm("是否停止旧服务并重启？", default=True)``：
         - Y（默认）：SIGTERM → 等 → SIGKILL → 再 probe 一次，端口空了就继续启动；
         - n：打印"已取消启动"并 ``SystemExit(0)``（不是 2，是用户主动选择）；
       杀进程之后仍有残留冲突（例如用户把 PID 捏在 root 下杀不动），降级到情况 3。
    3. 冲突里掺了非察元进程 / 非交互终端 / 终止后仍占用 → 按旧行为抛 ``SystemExit(2)``，
       stderr 打印完整冲突报告 + 可复制的 `kill PID` 命令。
    """
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    plan: list[tuple[str, dict]] = []
    if getattr(args, "api", False):
        plan.append(("API", dict(getattr(bs, "API_SERVER", {}) or {})))
    if getattr(args, "config", False):
        plan.append(("配置面板", dict(getattr(bs, "CONFIG_SERVER", {}) or {})))

    problems = _probe_port_conflicts(plan)
    if not problems:
        return

    # 分类：全 chayuan 残留（可自动清理）vs 掺了外部进程（只能交给用户手动）。
    chayuan_pids = [
        p for (_n, _h, _port, p, cmd) in problems
        if p is not None and "chayuan" in (cmd or "").lower()
    ]
    has_foreign = any(
        p is None or "chayuan" not in (cmd or "").lower()
        for (_n, _h, _port, p, cmd) in problems
    )
    is_interactive = sys.stdin.isatty() and sys.stderr.isatty()

    # ---- 情况 2：纯察元残留 + 交互式终端 → 弹 Y/n 让用户选择重启 ----
    if chayuan_pids and not has_foreign and is_interactive:
        # 友好打印冲突清单；这里隐去 kill 命令，因为我们自己会去杀
        sys.stderr.write(
            "\n" + _format_port_conflicts(problems, interactive_hint=True) + "\n"
        )
        sys.stderr.flush()
        try:
            ans = click.confirm(
                "\n检测到上一次的察元服务仍在运行，是否停止旧服务并重启？",
                default=True,
            )
        except (EOFError, click.exceptions.Abort):
            ans = False

        if not ans:
            sys.stderr.write("\n已取消启动。\n")
            sys.stderr.flush()
            raise SystemExit(0)

        # 用户同意重启：杀旧进程 → 等端口真正释放 → 再 probe 一次
        logger.warning(
            "用户确认重启，正在终止旧进程：%s",
            ", ".join(str(p) for p in chayuan_pids),
        )
        sys.stderr.write(
            f"正在停止旧服务（PID={', '.join(str(p) for p in chayuan_pids)}）...\n"
        )
        sys.stderr.flush()
        _kill_pids(chayuan_pids)

        # 端口关闭到 OS 实际回收之间可能有几十 ms 延迟；最多重试 10 次
        import time as _time
        remaining = problems
        for _ in range(10):
            _time.sleep(0.3)
            remaining = _probe_port_conflicts(plan)
            if not remaining:
                break

        if not remaining:
            sys.stderr.write("旧服务已停止，继续启动...\n\n")
            sys.stderr.flush()
            return

        # 杀完仍有残留：走错误路径（情况 3），用 remaining 当新冲突清单
        sys.stderr.write(
            "停止旧服务后仍有端口占用，请手动处理。\n"
        )
        problems = remaining

    # ---- 情况 3：非交互 / 掺外部进程 / 重启失败 → 硬失败 ----
    msg = _format_port_conflicts(problems, interactive_hint=False)
    msg += "\n\n冲突解除后重新执行 `chayuan start ...` 即可。"
    logger.warning(msg)
    sys.stderr.write("\n" + msg + "\n")
    sys.stderr.flush()
    raise SystemExit(2)


@click.command(help="启动服务")
@click.option(
    "-a",
    "--all",
    "all",
    is_flag=True,
    help="同时启动 API 与配置面板",
)
@click.option(
    "--api",
    "api",
    is_flag=True,
    help="仅启动 API 服务",
)
@click.option(
    "-c",
    "--config",
    "config",
    is_flag=True,
    help="仅启动配置面板（NiceGUI，默认端口 8502）",
)
@click.option(
    "--single-machine",
    "single_machine",
    is_flag=True,
    help=(
        "单机模式(Phase 2):抑制 Redis/Celery/远程依赖,适合 Tauri sidecar 嵌入。"
        "通过设置 CHAYUAN_PROFILE=single-machine 等环境变量驱动 Phase 5 bootstrap 挑实现。"
    ),
)
def main(all, api, config, single_machine):
    class args:
        ...
    args.all = all
    args.api = api
    args.config = config
    args.single_machine = single_machine

    # ── 单机模式 env 注入 ───────────────────────────────────────────
    # Phase 5 的 bootstrap 在 ``CHAYUAN_PROFILE=single-machine`` 时挑选:
    #   - 鉴权:LocalUser(anonymous)
    #   - 缓存:cachetools(替代 Redis)
    #   - 队列:asyncio.Queue + 线程池(替代 Celery)
    #   - 全文检索:SQLite FTS5(替代 ES)
    #   - 向量:sqlite-vec(替代 Milvus)
    # 这里仅注入 env;不修改老路径。env 已存在(用户 / Tauri sidecar 已显式设)时尊重。
    if single_machine:
        os.environ.setdefault("CHAYUAN_PROFILE", "single-machine")
        os.environ.setdefault("CHAYUAN_AUTH", "anonymous")
        os.environ.setdefault("CHAYUAN_REDIS", "disabled")
        os.environ.setdefault("CHAYUAN_QUEUE", "inproc")
        os.environ.setdefault("CHAYUAN_VECTOR_STORE", "sqlite-vec")

    # ── Phase 5 profile bootstrap ──────────────────────────────────
    # ``apply_profile`` 读 CHAYUAN_PROFILE,把单机模式开关一次性切到位。
    # 必须在 DB / KB 初始化之前完成,否则 KBServiceFactory 等先读到旧 default。
    try:
        from chayuan.server.profiles import apply_profile
        applied = apply_profile()
        if applied:
            logger.info("已应用 profile:%s", applied)
    except Exception as _e:  # noqa: BLE001
        logger.warning(f"profile bootstrap 失败(降级走默认 SaaS 形态):{_e!r}")

    # 添加这行代码
    cwd = os.getcwd()
    sys.path.append(cwd)
    mp.freeze_support()
    print("cwd:" + cwd)

    # 启动前的路径一致性检查：最近一次向导选择 vs 当前进程 CHAYUAN_ROOT。
    # 触发条件：向导写过 state.last_init_root，但当前运行目录不同（常见原因：
    # 用户 init 后没 source activate.sh 就在旧 shell 里 `chayuan start`）。
    _warn_stale_chayuan_root()

    # 同一个 CHAYUAN_ROOT 只允许一份 chayuan 实例：再次 `chayuan start` 会先把
    # 上一次启动的父进程杀掉（SIGTERM → 超时 SIGKILL），其子进程会在 signal
    # handler 触发的 finally 分支里被 _safe_kill 级联清理。失败不影响本次启动。
    _kill_previous_instance()

    # 记录运行时元数据，供配置面板「重启服务」按钮调用（失败不影响启动）。
    try:
        from chayuan.server.config_panel.restart import record_runtime

        meta_path = record_runtime()
        logger.info(f"已记录运行时元数据：{meta_path}")
    except Exception as _e:
        logger.warning(f"写入运行时元数据失败（重启按钮可能不可用）：{_e!r}")

    # DB 初始化降级策略：
    # - 仅 `-c`（只启动配置面板）时：完全跳过 create_tables / 迁移 / 管理员种子。
    #   配置面板只读写 yaml，与 KB 数据库无关；跳过可保证「DB 还没就绪」时也能打开
    #   面板去修改数据库连接串（部署/运维起步阶段最常见的场景）。
    # - 其它模式（-a/--api）：把 DB bootstrap 全部 try 住，任一步失败只记 warning、
    #   不阻塞 start_main_server，从而「任何情况下都能保证配置面板起来」——即使此时
    #   API 自己会因缺库回报 500，用户也能先进面板修 yaml 再重启。
    _needs_db = bool(getattr(args, "all", False)
                     or getattr(args, "api", False))

    if not _needs_db:
        logger.info(
            "检测到仅启动配置面板（-c）：跳过 DB 初始化 / 迁移 / 管理员种子，"
            "以兼容数据库尚未就绪的部署阶段。"
        )
    else:
        try:
            from chayuan.server.knowledge_base.migrate import create_tables

            create_tables()
            _db_ready = True
        except Exception as _e:
            logger.warning(
                "create_tables 失败（已降级继续启动；API/WebUI 的 DB 相关功能将不可用，"
                "请在配置面板修正 basic_settings.SQLALCHEMY_DATABASE_URI 后重启）：%r",
                _e,
            )
            _db_ready = False

        if _db_ready:
            # P3：在 create_tables 之后跑幂等 schema 迁移（加列 / 建用户表 / 建授权表 /
            # 建配置中心表）。任何一步失败都只记日志，不影响服务继续起来。
            try:
                from chayuan.server.db.migrations import run_migrations

                run_migrations()
            except Exception as _e:
                logger.warning(f"run_migrations 失败（忽略，继续启动）：{_e!r}")

            # 配置中心:启动时检测每个 namespace 的 yaml 是否已同步到 DB。
            # 51 题改用 ``ensure_seeded`` 替代 ``seed_from_yaml`` —
            # **delta seed**:已存在的 key 不动(尊重用户在面板改过的值),
            # yaml 新增的 key 单独补;升级 chayuan 后 yaml 模板新增字段也能自动同步。
            # 业务代码后续读配置走 DB + Redis 热更新 + yaml 反向镜像。
            try:
                from chayuan.server.config_center import ensure_seeded
                from chayuan.settings import CHAYUAN_ROOT as _R
                from pathlib import Path as _P

                _seed_targets = [
                    # 新增的 3 个 store(写入完全走 DB)
                    ("apps",            _P(_R) / "apps.yaml",                 "apps"),
                    ("custom_tools",    _P(_R) / "custom_tools.yaml",         "custom_tools"),
                    ("ws_endpoints",    _P(_R) / "websocket_endpoints.yaml",  "websocket_endpoints"),
                    # P1 迁移
                    ("tool_settings",   _P(_R) / "tool_settings.yaml",        ""),
                    ("prompt_settings", _P(_R) / "prompt_settings.yaml",      ""),
                    # P2 迁移
                    ("kb_settings",     _P(_R) / "kb_settings.yaml",          ""),
                    # P3 迁移
                    ("model_settings",  _P(_R) / "model_settings.yaml",       ""),
                    # basic_settings.yaml **不迁**:SQLALCHEMY_DATABASE_URI 自己是
                    # 配置中心的 DB 来源,进库形成自举循环;留在 yaml + 环境变量。
                ]

                _total_seeded = 0
                _total_matched = 0
                for ns, path, top_key in _seed_targets:
                    try:
                        rpt = ensure_seeded(ns, path, top_key=top_key)
                        _total_seeded += rpt.get("seeded", 0)
                        _total_matched += rpt.get("matched", 0)
                    except Exception as _e:
                        logger.warning(
                            "config_center.ensure_seeded(%s) 失败(忽略):%r",
                            ns, _e,
                        )
                logger.info(
                    "config_center: 同步完成 — 新增 %d keys / 已存在 %d / 共 %d 命名空间",
                    _total_seeded, _total_matched, len(_seed_targets),
                )
            except Exception as _e:
                logger.warning(f"config_center seed 失败(忽略,继续启动):{_e!r}")

            try:
                from chayuan.server.auth.service import bootstrap_default_admin

                seeded = bootstrap_default_admin()
                if seeded is not None:
                    _u, _pw = seeded
                    logger.warning(
                        "首次启动：已创建默认管理员 username=%s，临时密码=%s "
                        "（请立即在「用户管理」页修改密码）",
                        _u, _pw,
                    )
            except Exception as _e:
                logger.warning(f"bootstrap_default_admin 失败（忽略）：{_e!r}")
        else:
            logger.warning(
                "DB 未就绪：跳过 run_migrations / bootstrap_default_admin；"
                "配置面板仍会启动，可在面板里修改 DB 配置并重启服务。"
            )
    if sys.version_info < (3, 10):
        loop = asyncio.get_event_loop()
    else:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()

        asyncio.set_event_loop(loop)
    loop.run_until_complete(start_main_server(args))


if __name__ == "__main__":
    main()
