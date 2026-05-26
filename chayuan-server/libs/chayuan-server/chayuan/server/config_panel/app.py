"""配置面板（NiceGUI）主入口。

页面：
- `/<登录路径>`   登录页（路径由 `PANEL_LOGIN_PATH` 决定，例如 `/xkjmwpof`）
- `/`             已登录跳转 `/dashboard`，否则跳转登录页
- `/dashboard`    登录后主界面
- `/logout`

认证：读取 `basic_settings.yaml` 中的 `PANEL_USERNAME` + `PANEL_PASSWORD_HASH`；
两者任一为空则登录页提示「请先执行 `chayuan update`」且拒绝登录。
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import sys
from pathlib import Path
from typing import Optional

from chayuan import __version__
from chayuan.server.config_panel.auth import (
    generate_login_path,
    generate_session_secret,
    verify_password,
)
from chayuan.server.config_panel.captcha import (
    generate_captcha,
    verify_captcha,
)
from chayuan.settings import CHAYUAN_ROOT, Settings
from chayuan.utils import build_logger

logger = build_logger()

_SESSION_KEY = "chayuan_panel_user"
_DATA_FILES = [
    "basic_settings.yaml",
    "kb_settings.yaml",
    "model_settings.yaml",
    "tool_settings.yaml",
    "prompt_settings.yaml",
]


def _ensure_windows_selector_event_loop_policy() -> None:
    """NiceGUI/uvicorn is more stable with Selector loop in Windows subprocesses."""
    if sys.platform != "win32":
        return
    try:
        import asyncio

        policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy_cls is not None:
            asyncio.set_event_loop_policy(policy_cls())
    except Exception as exc:  # noqa: BLE001
        logger.warning("set WindowsSelectorEventLoopPolicy failed: %r", exc)


def _patch_basic_settings(updates: dict) -> None:
    """把若干字段回写到 basic_settings.yaml（仅当目标文件存在）。"""
    from chayuan.pydantic_settings_file import import_yaml

    path = Path(CHAYUAN_ROOT) / "basic_settings.yaml"
    if not path.is_file():
        return
    y = import_yaml()
    with open(path, encoding="utf-8") as f:
        doc = y.load(f) or {}
    doc.update(updates)
    with open(path, "w", encoding="utf-8") as f:
        y.dump(doc, f)


def _ensure_session_secret() -> str:
    """若未配置 PANEL_SESSION_SECRET，则生成并回写 basic_settings.yaml。"""
    secret = (Settings.basic_settings.PANEL_SESSION_SECRET or "").strip()
    if secret:
        return secret
    secret = generate_session_secret()
    _patch_basic_settings({"PANEL_SESSION_SECRET": secret})
    return secret


def _ensure_login_path() -> str:
    """若未配置 PANEL_LOGIN_PATH，则随机生成 8 位小写字母并回写 basic_settings.yaml。"""
    p = (Settings.basic_settings.PANEL_LOGIN_PATH or "").strip().strip("/")
    if p:
        return p
    p = generate_login_path()
    _patch_basic_settings({"PANEL_LOGIN_PATH": p})
    try:
        Settings.basic_settings.PANEL_LOGIN_PATH = p
    except Exception:
        pass
    return p


def _current_user(app_obj) -> Optional[str]:
    """从 NiceGUI 的 `app.storage.user` 读取登录态。参数 `app_obj` 是 `from nicegui import app` 里的 `app`。"""
    try:
        value = app_obj.storage.user.get(_SESSION_KEY)
        return value
    except Exception as e:
        import sys

        sys.stderr.write(
            f"[config panel][auth] 读取 app.storage.user 失败：{e!r}"
            "（多数情况是未配置 storage_secret；请重启让服务自动生成）\n"
        )
        sys.stderr.flush()
        return None


def _credentials_configured() -> bool:
    u = (Settings.basic_settings.PANEL_USERNAME or "").strip()
    h = (Settings.basic_settings.PANEL_PASSWORD_HASH or "").strip()
    return bool(u and h)


def _build_pages(login_path: str) -> None:
    """按配置注册 NiceGUI 页面。

    `login_path` 是不带前导斜杠的路径段，例如 `"xkjmwpof"`；本函数会注册路由为 `/<login_path>`。
    访问非登录路径的根路径只会：已登录→重定向 `/dashboard`；未登录→重定向 `/<login_path>`。
    这样没有路径的"路人"在 `/login` 或 `/` 上都只会看到 404 或被踢回登录页，而无法确定登录点。
    """
    from nicegui import app, ui

    route_login = f"/{login_path}"

    @ui.page(route_login)
    def login_page():
        import sys

        def _dlog(msg: str) -> None:
            sys.stderr.write(f"[config panel][login] {msg}\n")
            sys.stderr.flush()

        user_now = _current_user(app)
        if user_now:
            _dlog(f"already authenticated as {user_now!r}, redirect -> /dashboard")
            ui.navigate.to("/dashboard")
            return

        with ui.column().classes("absolute-center items-center gap-4"):
            ui.label("察元AI助手 · 配置面板").classes("text-2xl font-bold")
            ui.label(f"v{__version__}").classes("text-sm text-gray-500")

            if not _credentials_configured():
                ui.label("尚未配置面板账号。请先在服务器执行：").classes("text-red-500")
                ui.code("chayuan update username\nchayuan update password", language="bash")
                ui.label("完成后重启 `chayuan start -c` 即可登录。").classes("text-sm text-gray-600")
                return

            name = ui.input("用户名").props("outlined").classes("w-80")
            pwd = ui.input("密码", password=True, password_toggle_button=True).props(
                "outlined"
            ).classes("w-80")

            captcha_state = {"code": ""}

            def refresh_captcha():
                code, svg = generate_captcha()
                captcha_state["code"] = code
                captcha_img.set_content(svg)

            with ui.row().classes("w-80 items-center gap-2"):
                captcha_input = (
                    ui.input("验证码")
                    .props("outlined dense")
                    .classes("flex-1")
                )
                captcha_img = ui.html("").classes("cursor-pointer")
                captcha_img.on("click", lambda _: refresh_captcha())
                ui.button(
                    icon="refresh",
                    on_click=lambda: refresh_captcha(),
                ).props("flat dense").tooltip("换一张")

            refresh_captcha()
            ui.label("点击图片或刷新按钮可换一张（大小写不敏感）") \
                .classes("text-xs text-gray-500 w-80 text-left")

            async def try_login():
                expected_user = (Settings.basic_settings.PANEL_USERNAME or "").strip()
                stored_hash = (Settings.basic_settings.PANEL_PASSWORD_HASH or "").strip()

                if not verify_captcha(captcha_input.value or "", captcha_state["code"]):
                    _dlog("captcha mismatch")
                    ui.notify("验证码错误，请重新输入", color="negative")
                    refresh_captcha()
                    captcha_input.value = ""
                    return

                user_ok = (
                    bool(name.value)
                    and name.value.strip() == expected_user
                )
                pw_ok = verify_password(pwd.value or "", stored_hash)

                if not (user_ok and pw_ok):
                    _dlog(f"auth failed user_ok={user_ok} pw_ok={pw_ok}")
                    ui.notify("用户名或密码错误", color="negative")
                    refresh_captcha()
                    captcha_input.value = ""
                    return

                try:
                    app.storage.user[_SESSION_KEY] = expected_user
                    _dlog(
                        f"auth OK, wrote storage.user[{_SESSION_KEY!r}]={expected_user!r} "
                        f"(storage keys={list(app.storage.user.keys())})"
                    )
                except Exception as e:
                    _dlog(f"write storage.user failed: {e!r}")
                    ui.notify(
                        "写入会话失败（请确保 storage_secret 已生成并重启服务）",
                        color="negative",
                    )
                    return

                ui.notify("登录成功，正在跳转…", color="positive")
                ui.navigate.to("/dashboard")

            ui.button("登录", on_click=try_login).classes("w-80")
            pwd.on("keydown.enter", lambda _: try_login())
            captcha_input.on("keydown.enter", lambda _: try_login())

    @ui.page("/")
    def index():
        if _current_user(app):
            ui.navigate.to("/dashboard")
        else:
            ui.navigate.to(route_login)

    @ui.page("/logout")
    def logout():
        try:
            app.storage.user.pop(_SESSION_KEY, None)
        except Exception:
            pass
        ui.navigate.to(route_login)

    @ui.page("/dashboard")
    def dashboard():
        from chayuan.server.config_panel.dashboard import render_dashboard

        user_now = _current_user(app)
        if not user_now:
            ui.navigate.to(route_login)
            return
        render_dashboard(app, user_now, route_login)


def run_config_panel(
    started_event: "mp.Event | None" = None, run_mode: Optional[str] = None
) -> None:
    """在独立子进程中启动 NiceGUI 配置面板。

    使用 NiceGUI 原生 `ui.run`（必要，内部会置位 `core.ui_run_has_been_called`）；
    但不走 `reload` 也不启动 native webview，从而能在 multiprocessing spawn 子进程中运行。
    """
    _ensure_windows_selector_event_loop_policy()

    def _log(msg: str) -> None:
        sys.stderr.write(f"[config panel] {msg}\n")
        sys.stderr.flush()

    # NiceGUI 1.4 的 `ui.run` 在 `multiprocessing.current_process().name != 'MainProcess'`
    # 时会直接 return，从而阻止我们通过子进程托管。此处显式把子进程名伪装为
    # 'MainProcess'，使得 ui.run 走正常启动分支。
    import multiprocessing as _mp
    try:
        _mp.current_process().name = "MainProcess"
    except Exception as _e:
        _log(f"warn: 重命名进程失败 ({_e!r})，仍尝试继续")

    _log("import nicegui ...")
    try:
        from nicegui import app as nicegui_app
        from nicegui import ui
    except ImportError:
        _log(
            "未安装 nicegui;请执行 `pip install 'nicegui>=1.4.20,<2.0'` 后重试。"
        )
        if started_event is not None:
            started_event.set()
        raise

    # 53 题:NiceGUI Client.delete 双重调用产生的 KeyError 抑制 patch。
    # 必须在 NiceGUI app 跑起来之前 patch _handle_task_result。
    try:
        from chayuan.server.config_panel._safe_ui import patch_nicegui_disconnect_race
        patch_nicegui_disconnect_race()
    except Exception as _e:  # noqa: BLE001
        _log(f"warn: nicegui disconnect race patch 失败 ({_e!r})")

    # 55 题:**预热重模块 import** — 子进程 spawn 模式 fresh interpreter,
    # 首次访问 dashboard 时同步 import model_config / runtime_framework_panel
    # / install_dialog 等大模块,卡主线程 5-10 秒 → NiceGUI WebSocket 心跳超时
    # → connection lost。在 ui.run 之前**主动 import**这些模块,把同步开销
    # 移到子进程启动早期(用户尚未连接时),首次 page load 后续操作只剩
    # mount DOM + 业务逻辑。
    _log("warmup imports ...")
    try:
        from chayuan.server.config_panel import (  # noqa: F401
            model_config,
            runtime_framework_panel,
            install_dialog,
            install_recipes,
            install_task_manager,
            container_lifecycle,
            auto_register,
            container_health_strip,
            compose_manager,
        )
        # 75 题:57 题新建的 model_settings 子包必须一并预热。
        # 否则用户首次点 ② 模型配置时,dashboard 同步 import 这个包(及其 7 个
        # 子模块 + state_cache 内部 _capability_grouped 触发的 yaml/local_index
        # 扫描)会卡 click handler 数秒 → NiceGUI socket.io 心跳超时 → connection lost
        from chayuan.server.config_panel.model_settings import (  # noqa: F401
            page,
            state_cache,
            runtime_subpage,
            providers_subpage,
            # 单机版已移除 marketplace 模块(CLAUDE.md §0.1 第 (3) 条)。
            defaults_subpage,
        )
        _log("warmup imports done")

        # 75 题(根治):**ui.run() 之前同步预热 Settings + state_cache**,
        # 启动慢一点(3-5s)但用户进来后 click ② 模型配置永远不会 connection lost。
        # 之前后台 daemon thread 异步预热,如果用户启动后立即点击,thread 还没
        # 完成,page.py 又触发一次 prefetch_in_threads(3s deadline),3s 内
        # NiceGUI socket.io 重连提示就出现了 — 实际是"等数据"被误读成"断线"。
        try:
            from chayuan.settings import Settings
            # 触发 pydantic 解析 yaml(8 个 platform + 350+ 模型,可能 1-2s)
            _ = Settings.model_settings.MODEL_PLATFORMS
            _ = Settings.kb_settings
            _ = Settings.basic_settings
            _log("Settings 预解析完成")
        except Exception as _e3:  # noqa: BLE001
            _log(f"warn: Settings 预解析失败({_e3!r})")

        try:
            # 同步 prefetch state_cache,4 项数据进 cache;timeout=10s 兜底防止
            # 真有外部 framework probe 卡住影响启动
            state_cache.prefetch_in_threads(timeout=10.0)
            _log("state_cache 同步 prefetch 完成 — 进入 ② 模型配置不会再 connection lost")
        except Exception as _e2:  # noqa: BLE001
            _log(f"warn: state_cache prefetch 失败({_e2!r})")
    except Exception as _e:  # noqa: BLE001
        _log(f"warmup imports 部分失败(非阻断):{_e!r}")

    # 55 题:**NiceGUI 心跳容忍提高** — 默认 socket.io ping_timeout 20s,
    # 用户首次进入大页面时 mount + 业务 IO 可能 5-15s,在容忍范围内但接近临界。
    # 把容忍调大到 60s,给重操作留余量。属于客户端配置,不影响功能。
    try:
        if hasattr(nicegui_app, "config"):
            cfg = nicegui_app.config
            for attr_name, val in (
                ("reconnect_timeout", 60.0),     # 1.4+ 提供
                ("socket_io_ping_timeout", 60),  # 老版本 fallback 名字
                ("socket_io_ping_interval", 25), # 心跳间隔保持默认
            ):
                if hasattr(cfg, attr_name):
                    try:
                        setattr(cfg, attr_name, val)
                        _log(f"set nicegui {attr_name} = {val}")
                    except Exception:  # noqa: BLE001
                        pass
    except Exception as _e:  # noqa: BLE001
        _log(f"warn: nicegui heartbeat config 失败 ({_e!r})")

    # 静态路由：把仓库自带的模型 logo 目录挂成 /static/model_logos/<file>，
    # 供模型配置页根据 platform id 渲染 <img src>。找不到目录时静默跳过。
    try:
        from pathlib import Path as _Path
        _logos_dir = _Path(__file__).resolve().parents[2] / "img" / "model_logos"
        if _logos_dir.is_dir():
            nicegui_app.add_static_files("/static/model_logos", str(_logos_dir))
    except Exception as _e:  # noqa: BLE001
        _log(f"warn: 挂载 model_logos 失败 ({_e!r})")

    # 知识库内容目录的静态挂载：供"知识库管理"页预览 / 下载文件用（PDF、图片、
    # 文本都能在浏览器里直开）。路径对应 Settings.basic_settings.KB_ROOT_PATH 的
    # 实际值——通常是 $CHAYUAN_ROOT/data/knowledge_base。
    #
    # 访问控制：仅已登录用户能进入面板页面，但静态挂载本身不走 NiceGUI 的 session
    # 校验；考虑到配置面板历来是「admin-only」定位，且用户已经选择把面板暴露，
    # 这里的权衡可以接受。若需严格鉴权，后续可改为 FastAPI 路由 + 手动校验 session。
    try:
        from pathlib import Path as _Path
        _kb_root = (Settings.basic_settings.KB_ROOT_PATH or "").strip()
        if _kb_root and _Path(_kb_root).is_dir():
            nicegui_app.add_static_files("/static/kb_content", _kb_root)
            _log(f"mounted /static/kb_content -> {_kb_root}")
    except Exception as _e:  # noqa: BLE001
        _log(f"warn: 挂载 kb_content 失败 ({_e!r})")

    _log("load settings ...")
    host = Settings.basic_settings.CONFIG_SERVER.get("host", "127.0.0.1")
    port = int(Settings.basic_settings.CONFIG_SERVER.get("port", 8502))

    # 面板启动时做一次"运行时可选依赖体检"——放到后台线程里跑，确保：
    # 1) 网络正常时，几秒内补齐 redis / arq；
    # 2) 断网 / 内网 Nexus 不可达时，绝不阻塞面板进入（ensure_pkg 自带 TCP 探活秒级返回）；
    # 3) 用户真去用某个包时，对应的懒加载 ensure_pkg 会再探一次——幂等。
    def _bg_ensure_runtime_deps() -> None:
        try:
            from chayuan.server.shared.deps import ensure_runtime_deps
            dep_status = ensure_runtime_deps()
            missing = [name for name, ok in dep_status.items() if not ok]
            if missing:
                _log(
                    "auto-install 部分依赖未就绪（可能离线或镜像不可达），"
                    "对应功能将走降级路径：" + ", ".join(missing)
                )
        except Exception as _e:  # noqa: BLE001
            _log(f"ensure_runtime_deps failed: {_e!r}")

    try:
        import threading as _threading
        _threading.Thread(
            target=_bg_ensure_runtime_deps,
            name="chayuan-ensure-deps",
            daemon=True,
        ).start()
    except Exception as _e:  # noqa: BLE001
        _log(f"ensure_runtime_deps thread start failed: {_e!r}")

    # 统一 bootstrap：缺啥补啥（username/password/secret/login_path）。
    # 幂等；已配置的字段不会被覆盖。
    try:
        from chayuan.server.config_panel.bootstrap import (
            ensure_panel_credentials,
            format_cli_banner,
        )
        br = ensure_panel_credentials()
        if br.did_change:
            _log("bootstrap: " + ", ".join(br.changed_fields))
            if br.has_initial_credentials:
                import sys as _sys
                _sys.stderr.write("\n" + format_cli_banner(br) + "\n\n")
                _sys.stderr.flush()
            # Settings 开了 auto_reload，下次属性访问会自动重读 yaml。
    except Exception as _e:  # noqa: BLE001
        _log(f"bootstrap credentials failed: {_e!r}")

    secret = (Settings.basic_settings.PANEL_SESSION_SECRET or "").strip() or _ensure_session_secret()
    login_path = (Settings.basic_settings.PANEL_LOGIN_PATH or "").strip().strip("/") or _ensure_login_path()

    _log(f"build pages (login route = /{login_path}) ...")
    _build_pages(login_path)

    def _notify_parent():
        if started_event is not None:
            try:
                started_event.set()
            except Exception:
                pass

    nicegui_app.on_startup(_notify_parent)

    _log(f"ui.run on http://{host}:{port}")
    # 浏览器页签 favicon：优先走仓库自带的 logo.png，找不到再退回 NiceGUI 默认。
    # 这里用绝对路径避免 NiceGUI 以 CWD 做相对解析。
    try:
        from pathlib import Path as _Path
        _favicon_path = (
            _Path(__file__).resolve().parents[2] / "img" / "logo.png"
        )
        favicon_arg = str(_favicon_path) if _favicon_path.exists() else None
    except Exception:
        favicon_arg = None

    try:
        ui.run(
            host=host,
            port=port,
            title="察元AI助手 · 配置面板",
            favicon=favicon_arg,
            storage_secret=secret,
            reload=False,
            show=False,
            dark=False,
            native=False,
            language="zh-CN",
            uvicorn_logging_level="info",
            show_welcome_message=False,
        )
    except BaseException as e:
        import traceback

        _log(f"NiceGUI 退出：{e!r}")
        traceback.print_exc()
        if started_event is not None:
            try:
                started_event.set()
            except Exception:
                pass
        raise


if __name__ == "__main__":
    run_config_panel()
