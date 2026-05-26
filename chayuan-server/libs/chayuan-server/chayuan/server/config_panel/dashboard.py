"""配置面板的 Dashboard 页面。

布局：左侧按「项目生命周期」顺序列出步骤 / 配置项，右侧切换渲染：

    ① 服务监控              -- KPI 卡片 / 时间序列 / 分类分布 / 知识库 / 热力图（ECharts）
    ② 基础配置             -- 主区两张表单卡：数据目录配置（改 CHAYUAN_ROOT 并写 shell rc）
                               + basic_settings.yaml 表单/原始 YAML；
                               右上角 ? 按钮弹出帮助 dialog（一致性诊断 / 派生路径 /
                               命令行速查）。
                               （内部仍叫 `root` 页，因为它同时管数据目录和 basic_settings）
    ③ 知识库配置           -- kb_settings.yaml
    ④ 模型配置             -- model_settings.yaml
    ── 高级 ──
    ⑤ 工具配置             -- tool_settings.yaml
    ⑥ Prompt 模板          -- prompt_settings.yaml
    ⑦ 服务管理             -- 运行时元数据 + 立即重启

所有「yaml 表单 / 原始 YAML」编辑能力集中在 ②–⑥ 五页；
①、⑦ 是只读 / 动作类页面。
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from chayuan import __version__
from chayuan.settings import CHAYUAN_ROOT, Settings

from chayuan.server.config_panel import schema as _schema
from chayuan.server.config_panel import yaml_store

logger = logging.getLogger("chayuan.config_panel")


_SENSITIVE_BASIC_KEYS = {
    "PANEL_USERNAME",
    "PANEL_PASSWORD_HASH",
    "PANEL_SESSION_SECRET",
    "PANEL_LOGIN_PATH",
}


# ---------------------------------------------------------------------------
# 外部入口
# ---------------------------------------------------------------------------

def render_dashboard(app_obj, user_name: str, login_route: str) -> None:
    """在 `@ui.page("/dashboard")` 上下文内调用。"""
    from nicegui import ui

    pending_restart: Dict[str, set] = {s.filename: set() for s in _schema.ALL_SCHEMAS}
    banner_ctx: Dict[str, Any] = {"banner": None, "label": None}

    def refresh_banner() -> None:
        total = sum(len(v) for v in pending_restart.values())
        banner = banner_ctx["banner"]
        label = banner_ctx["label"]
        if banner is None or label is None:
            return
        if total > 0:
            files = ", ".join(f for f, s in pending_restart.items() if s)
            label.set_text(
                f"检测到 {total} 项需要重启生效的变更（来自：{files}）。"
                "点击右侧「立即重启」让新配置生效。"
            )
            banner.style("display:flex")
        else:
            banner.style("display:none")

    nav_items = _nav_items()
    drawer_state: Dict[str, Any] = {"mini": False, "drawer": None}

    # 顶部 header —— 左上角放一个汉堡按钮控制抽屉展开/mini
    with ui.header(elevated=True).classes(
        "items-center justify-between bg-primary text-white q-px-md"
    ):
        with ui.row().classes("items-center gap-2 no-wrap"):
            ui.button(
                icon="menu",
                on_click=lambda: _toggle_drawer(drawer_state),
            ).props("flat round dense color=white").tooltip("折叠 / 展开侧边栏")
            ui.label("察元AI助手 · 配置面板").classes("text-lg font-bold")
            ui.label(f"v{__version__}").classes("text-sm opacity-70")
        with ui.row().classes("items-center gap-3 no-wrap"):
            ui.label(f"当前用户：{user_name}")
            ui.button("退出登录", on_click=lambda: ui.navigate.to("/logout")).props(
                "flat color=white"
            )

    # 左侧抽屉：Quasar QDrawer，fixed + mini 模式原生支持
    left = ui.left_drawer(value=True, bordered=True, fixed=True).classes("bg-grey-2")
    # mini-to-overlay=false：折叠时不以浮层覆盖主区，而是就地缩窄到 mini-width
    # breakpoint=0：关闭 Quasar 自动窄屏覆盖，始终受我们 toggle 控制
    left.props(':width="260" :mini-width="64" :mini-to-overlay="false" :breakpoint="0"')
    drawer_state["drawer"] = left

    content_slot: Dict[str, Any] = {"column": None}
    nav_items_map: Dict[str, Any] = {}
    # 让 _render_page 能把 select 函数传给子页面（例如服务配置卡片点「去配置」）
    nav_handle: Dict[str, Any] = {"select": None}

    with left:
        with ui.column().classes("w-full").style("gap:4px;padding:8px"):
            ui.label("配置向导").classes(
                "text-base font-semibold text-grey-8 q-px-sm q-pt-xs drawer-title"
            )
            with ui.list().props("padded dense").classes("w-full rounded-borders"):
                def _select(key: str) -> None:
                    # 先 log 一条;若用户反馈"点了没反应",看 server 日志至少能确认
                    # 点击事件已抵达后端,问题就缩到 _render_page 内部
                    logger.info("[dashboard] nav click → key=%r", key)
                    for k, entry in nav_items_map.items():
                        entry["item"].props(remove="active")
                    if key in nav_items_map:
                        nav_items_map[key]["item"].props("active")
                    target = content_slot["column"]
                    if target is None:
                        # 第一次渲染时 column 还没 attach;直接报错给用户看,
                        # 而不是静默吞掉(过去这种状态下点击就是"没反应")
                        try:
                            ui.notify(
                                "导航失败:主区还未就绪,请等页面加载完再点。",
                                type="warning",
                            )
                        except Exception:  # noqa: BLE001
                            pass
                        return
                    try:
                        _render_page(
                            ui, key, target,
                            pending_restart, refresh_banner,
                            navigate_to=nav_handle["select"],
                        )
                    except Exception as e:  # noqa: BLE001
                        # 任何未处理异常都在主区显形,避免"点了没反应"
                        logger.exception("[dashboard] nav render failed for key=%r", key)
                        try:
                            target.clear()
                            with target:
                                ui.label(f"渲染 {key!r} 页失败").classes(
                                    "text-negative text-lg font-semibold"
                                )
                                ui.label(f"{type(e).__name__}: {e}").classes(
                                    "text-sm font-mono text-grey-8"
                                )
                                ui.label(
                                    "(详细堆栈见服务端日志,搜索 'nav render failed')"
                                ).classes("text-xs text-grey-6")
                        except Exception:  # noqa: BLE001
                            pass
                nav_handle["select"] = _select

                for item in nav_items:
                    if item.get("separator"):
                        ui.separator().classes("q-my-sm drawer-separator")
                        ui.label(item["label"]).classes(
                            "text-xs text-grey-7 q-px-md q-mb-xs drawer-section"
                        )
                        continue
                    key = item["key"]
                    icon_name = item.get("icon", "circle")
                    q_item = ui.item(on_click=lambda _=None, k=key: _select(k)).props(
                        'clickable active-class="bg-blue-1 text-primary"'
                    ).classes("rounded-borders")
                    with q_item:
                        with ui.item_section().props("avatar"):
                            ui.icon(icon_name).classes("text-grey-8").tooltip(
                                f"{item['index']} {item['label']}"
                            )
                        ui.item_section(item["label"])
                    nav_items_map[key] = {"item": q_item}

    # 主内容区 —— 现在是 page 直接子元素，不再需要 row 包一层
    with ui.column().classes("w-full q-pa-md").style(
        "min-width:0;max-width:100%;overflow-x:hidden"
    ):
        with ui.row().classes(
            "w-full items-center q-pa-sm bg-warning text-black no-wrap"
        ).style("display:none;gap:12px") as banner:
            banner_ctx["banner"] = banner
            ui.icon("priority_high").classes("text-lg")
            banner_ctx["label"] = ui.label("").classes("text-sm flex-1")
            ui.button("立即重启", on_click=lambda: _on_restart(ui, pending_restart)).props(
                "color=negative"
            )

        # ===== 业务库不可达 红色横幅 =====
        # 启动后周期探活；不通则横幅常驻，提供「去配置基础参数」一键跳转。
        # 配置面板自身只读写 yaml，因此 DB 死了横幅本身仍可见。
        #
        # 关键：探活必须走 `asyncio.to_thread`，psycopg2 connect 是阻塞 syscall，
        # 直接在 ui.timer 回调里跑会冻结 NiceGUI 事件循环 3s（connect_timeout），
        # 与服务管理页自己的 5s 轮询叠加 → 整个面板"乱套"。改 async + to_thread
        # 后只阻塞工作线程，UI 持续可交互。
        db_banner_ctx: Dict[str, Any] = {
            "row": None, "label": None, "ok": True, "in_flight": False,
        }
        # 改用 ui.row 的 classes 控制 hidden，避免 style display 与 quasar 类冲突
        # 也避免长 URI + 错误信息把 no-wrap 容器撑出滚动条
        with ui.row().classes(
            "w-full items-center q-pa-sm bg-red-1 text-red-10 rounded-borders"
        ).style("display:none;gap:12px;border:1px solid #ef9a9a") as db_row:
            db_banner_ctx["row"] = db_row
            ui.icon("storage").classes("text-lg flex-shrink-0")
            # break-all + 限高 让超长错误文本换行而不撑破容器
            db_banner_ctx["label"] = ui.label("").classes(
                "text-sm flex-1 break-all"
            ).style("min-width:0;overflow-wrap:anywhere")
            ui.button(
                "去配置",
                on_click=lambda: nav_handle["select"]("root") if nav_handle["select"] else None,
            ).props("color=negative dense").classes("flex-shrink-0")

        async def _refresh_db_banner() -> None:
            """async 版本：DB 探活进 to_thread，避免阻塞 NiceGUI 主 loop。"""
            row = db_banner_ctx["row"]
            label = db_banner_ctx["label"]
            if row is None or label is None:
                return
            if db_banner_ctx["in_flight"]:
                # 上一次还没回 → 直接跳过，避免堆积探活任务把 socket 打爆
                return
            db_banner_ctx["in_flight"] = True
            try:
                import asyncio
                ok, info = await asyncio.to_thread(_probe_business_db)
            except Exception:  # noqa: BLE001
                ok, info = False, "探活异常"
            finally:
                db_banner_ctx["in_flight"] = False

            if ok and not db_banner_ctx["ok"]:
                try:
                    ui.notify("业务库已恢复连接，相关功能即可用", type="positive")
                except Exception:  # noqa: BLE001
                    pass
            db_banner_ctx["ok"] = ok
            if ok:
                row.style("display:none")
            else:
                row.style("display:flex")
                # 截断防止极端长 traceback 把横幅撑成大段
                msg_info = info if len(info) <= 240 else info[:240] + "…"
                label.set_text(
                    f"业务库不可达：{msg_info}  ·  请点「去配置」改 "
                    "basic_settings.SQLALCHEMY_DATABASE_URI 后重启服务。"
                )

        # 启动时立即探一次（async 任务），并每 5s 复探一次
        try:
            import asyncio as _aio
            _aio.create_task(_refresh_db_banner())
        except Exception:  # noqa: BLE001
            pass
        try:
            # 5s 周期 refresh; 用 safe_timer_cb 防 client deleted 警告
            from chayuan.server.config_panel._safe_ui import safe_timer_cb
            ui.timer(5.0, safe_timer_cb(_refresh_db_banner))
        except Exception:  # noqa: BLE001
            pass

        # 42 题 Phase 6:容器健康状态条 — 显示 5 个 docker service 实时状态。
        # 全 missing 时自动隐藏(用户没用 docker 类 service 的话不显示)。
        try:
            from chayuan.server.config_panel.container_health_strip import (
                render_container_health_strip,
            )
            render_container_health_strip(
                ui,
                interval_seconds=10.0,
                navigate_to=nav_handle.get("select"),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[dashboard] container_health_strip 挂载失败(忽略):%r", e)

        # 89-10:图像嵌入引导横幅
        # 检测条件:Infinity 已装(compose/infinity.yaml 存在),但默认 clip 模型
        # 没有任何模型命中已上线 → 显示蓝色提示横幅 + 立即安装按钮
        _render_image_embedder_onboarding_banner(
            ui, navigate_to=nav_handle.get("select"),
        )

        content = ui.column().classes("w-full").style("min-width:0;gap:12px")
        content_slot["column"] = content

    # 全局样式：
    # - mini 模式下隐藏 drawer 里的纯文字元素（分组标题等）；
    # - 为 avatar 格子锁死宽度，避免某个图标字形缺失时整行被撑变形；
    # - 主文本禁止换行 + 溢出省略号，侧栏永远不会因为一个长词破图。
    ui.add_head_html("""
    <style>
      .q-drawer--mini .drawer-title,
      .q-drawer--mini .drawer-section,
      .q-drawer--mini .drawer-separator { display:none !important; }
      .q-drawer--mini .q-item__section--main { display:none !important; }
      .q-drawer--mini .q-item { justify-content:center; padding:6px; }
      /* 侧栏每一项都不允许换行 / 撑破 */
      .q-drawer .q-item { min-height:36px; }
      .q-drawer .q-item__section--avatar { min-width:40px; max-width:40px; }
      .q-drawer .q-item__section--main {
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
      }
    </style>
    """)

    first_key = nav_items[0]["key"]
    nav_items_map[first_key]["item"].props("active")
    _render_page(
        ui, first_key, content_slot["column"],
        pending_restart, refresh_banner,
        navigate_to=nav_handle["select"],
    )
    refresh_banner()


def _check_image_embedder_needs_onboarding() -> Tuple[bool, str]:
    """89-10:Infinity 已装 + 没默认 image embedder 上线 → 需要引导。

    返回 ``(need_banner, reason)``。reason 用于横幅文案。

    92 题:用户在 ④ tab 选了云厂商图像模型(如 qwen-vl-max @ bailian)
    也算"已配置",不弹横幅打扰。
    """
    # Infinity yaml 是否存在
    try:
        from chayuan.server.config_panel.compose_manager import (
            get_compose_file_for_service,
        )
        yaml_p = get_compose_file_for_service("infinity")
    except Exception:  # noqa: BLE001
        return False, ""
    if yaml_p is None or not yaml_p.exists():
        return False, ""  # Infinity 都没装,不引导

    # 92 题:用户在 ④ tab 选了任何模型(含云厂商)→ 视为"已配置",不弹引导
    try:
        from chayuan.server.image_source.embedder import resolve_default
        model_id, _platform = resolve_default()
        if model_id and model_id != "google/siglip2-base-patch16-224":
            # 默认兜底值不算"用户选过",其它视为已选
            return False, ""
    except Exception:  # noqa: BLE001
        pass

    # client 是否已上线
    try:
        from chayuan.server.image_source.embedder import get_client
        cli = get_client()
        if cli.kind == "infinity" and cli.healthcheck():
            return False, ""
        if cli.kind == "inproc" and cli.healthcheck():
            return False, ""
    except Exception:  # noqa: BLE001
        pass
    return True, "Infinity 已安装但未挂载图像嵌入模型"


def _render_image_embedder_onboarding_banner(
    ui: Any, *, navigate_to: Optional[Callable[[str], None]] = None,
) -> None:
    """89-10:在 Dashboard 顶部显示一键安装图像嵌入模型的横幅。

    条件由 :func:`_check_image_embedder_needs_onboarding` 决定;
    用户点【立即安装】跳转到模型广场预筛选页(image-embedding + Infinity)。
    """
    ctx: Dict[str, Any] = {"row": None, "label": None}
    with ui.row().classes(
        "w-full items-center q-pa-sm bg-blue-1 text-blue-10 rounded-borders"
    ).style("display:none;gap:12px;border:1px solid #93c5fd") as row:
        ctx["row"] = row
        ui.icon("auto_fix_high").classes("text-lg flex-shrink-0")
        ctx["label"] = ui.label("").classes("text-sm flex-1")

        def _go_marketplace() -> None:
            # 真实菜单 key 是 ``file:model_settings.yaml`` (见 _nav_items),
            # 加 #marketplace fragment 让 dashboard._render_page 切到③模型广场 tab
            try:
                if navigate_to:
                    navigate_to("file:model_settings.yaml#marketplace")
            except Exception as e:  # noqa: BLE001
                logger.debug("[image-onboarding] nav failed: %r", e)

        ui.button("立即安装", icon="download",
                  on_click=_go_marketplace).props("color=primary dense")
        ui.button(icon="close", on_click=lambda: row.style("display:none")).props(
            "flat dense round"
        ).style("color:#1e3a8a")

    async def _refresh() -> None:
        try:
            need, reason = await _async_to_thread(_check_image_embedder_needs_onboarding)
        except Exception:  # noqa: BLE001
            return
        if not ctx["row"] or not ctx["label"]:
            return
        if need:
            ctx["label"].set_text(
                f"{reason}。一键安装推荐 jinaai/jina-clip-v1(900MB,跨模态文-图都能搜)。"
            )
            ctx["row"].style("display:flex")
        else:
            ctx["row"].style("display:none")

    try:
        import asyncio as _aio
        _aio.create_task(_refresh())
    except Exception:  # noqa: BLE001
        pass
    try:
        # 30s 复探;频率不高,引导只是状态提示
        from chayuan.server.config_panel._safe_ui import safe_timer_cb
        ui.timer(30.0, safe_timer_cb(_refresh))
    except Exception:  # noqa: BLE001
        pass


async def _async_to_thread(func, *args, **kwargs):
    """py3.9 兼容 wrapper(asyncio.to_thread 是 3.9+)。"""
    import asyncio
    if hasattr(asyncio, "to_thread"):
        return await asyncio.to_thread(func, *args, **kwargs)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def _toggle_drawer(state: Dict[str, Any]) -> None:
    """在 mini / full 之间切换 QDrawer。"""
    drawer = state.get("drawer")
    if drawer is None:
        return
    mini = state.get("mini", False)
    if mini:
        drawer.props(remove="mini")
    else:
        drawer.props("mini")
    state["mini"] = not mini


# ---------------------------------------------------------------------------
# 左 rail 项
# ---------------------------------------------------------------------------

def _nav_items() -> List[Dict[str, Any]]:
    # 数据目录（CHAYUAN_ROOT）与 basic_settings.yaml 的所有字段高度相关——前者决定
    # 派生路径的默认值，后者里大部分字段都跟 root 有关。为了减少「刚选完数据目录又
    # 要跳到另一个 tab 去配置端口 / 路径」的跳跃成本，把它们合并在同一页。
    # 菜单顺序(86 题):服务配置在前(系统先决条件),模型配置紧跟其后
    items: List[Dict[str, Any]] = [
        {"index": "①", "key": "status",  "label": "服务监控",    "icon": "insights"},
        {"index": "🚀", "key": "service_config", "label": "服务配置",
         "icon": "rocket_launch"},
        {"index": "②", "key": "file:model_settings.yaml",  "label": "模型配置",   "icon": "smart_toy"},
        {"index": "③", "key": "root",    "label": "基础配置", "icon": "folder_open"},
        {"index": "④", "key": "file:kb_settings.yaml",     "label": "知识库配置", "icon": "menu_book"},
        {"separator": True, "label": "内容管理"},
        {"index": "⑤", "key": "kb_manage", "label": "知识库管理", "icon": "library_books"},
        {"index": "⑥", "key": "mcp_manage", "label": "MCP 管理",   "icon": "cable"},
        {"separator": True, "label": "高级"},
        {"index": "⑦", "key": "file:tool_settings.yaml",   "label": "工具配置",   "icon": "build"},
        {"index": "⑧", "key": "custom_tools",              "label": "自定义工具",  "icon": "extension"},
        {"index": "⑨", "key": "ws_endpoints",              "label": "WebSocket 端点", "icon": "cable"},
        {"index": "⑩", "key": "file:prompt_settings.yaml", "label": "Prompt 模板", "icon": "edit_note"},
        {"separator": True, "label": "开放平台"},
        {"index": "⑪", "key": "apps", "label": "App 管理", "icon": "business_center"},
        {"separator": True, "label": "运维"},
        {"index": "⑫", "key": "service", "label": "服务管理",   "icon": "restart_alt"},
        {"index": "⑬", "key": "scalability", "label": "性能与可扩展性", "icon": "speed"},
        {"index": "⑭", "key": "users", "label": "用户管理", "icon": "group"},
        {"index": "⑮", "key": "kb_acl", "label": "知识库访问控制", "icon": "shield"},
    ]
    return items


# ---------------------------------------------------------------------------
# 路由：根据 key 渲染右侧主区
# ---------------------------------------------------------------------------

def _render_page(
    ui,
    key: str,
    container,
    pending_restart: Dict[str, set],
    refresh_banner: Callable[[], None],
    *,
    navigate_to: Callable[[str], None] | None = None,
) -> None:
    container.clear()
    with container:
        # 前置守卫：除「服务监控 / 服务配置 / 基础配置」本身外，其它页都挂一条就绪
        # banner（系统未就绪时显眼提示「先去服务配置修一修」）
        if key not in ("status", "service_config", "root"):
            try:
                from chayuan.server.config_panel.service_config_page import (
                    render_readiness_banner_if_needed,
                )
                render_readiness_banner_if_needed(ui)
            except Exception:  # noqa: BLE001
                pass

        if key == "status":
            from chayuan.server.config_panel.monitoring import render_monitoring
            try:
                render_monitoring(ui)
            except Exception as e:  # noqa: BLE001
                logger.exception("render_monitoring failed")
                ui.label("监控页渲染失败").classes("text-negative text-lg font-semibold")
                ui.label(f"{type(e).__name__}: {e}").classes("text-sm font-mono text-grey-8")
                ui.separator()
                ui.label("下方为兼容视图").classes("text-xs text-grey-7")
                _render_status_page(ui)
        elif key == "service_config":
            # 109 题:移除"服务配置(系统先决条件)+ 拓扑图说明"两条提示,
            # 让下方实际配置卡片往上移,首屏空间更紧凑
            try:
                from chayuan.server.config_panel.service_config_page import (
                    render_service_config_page,
                )
                # 跳转函数注入：卡片上的「去配置」点击后，切换左栏 active 到目标项
                render_service_config_page(ui, navigate_to=navigate_to)
            except Exception as e:  # noqa: BLE001
                logger.exception("render_service_config_page failed")
                ui.label(f"服务配置页渲染失败：{type(e).__name__}: {e}").classes(
                    "text-negative text-sm"
                )
        elif key == "root":
            _render_root_page(
                ui, pending_restart, refresh_banner,
                navigate_to=navigate_to,
            )
        elif key == "service":
            try:
                from chayuan.server.config_panel.service_page import render_service_page
                render_service_page(ui, pending_restart, refresh_banner)
            except Exception as e:  # noqa: BLE001
                logger.exception("render_service_page failed")
                _render_page_error(ui, "服务管理", e)
                ui.separator().classes("q-my-sm")
                ui.label("下方为兼容视图（仅重启）").classes("text-xs text-grey-7")
                _render_service_page(ui, pending_restart, refresh_banner)
        elif key == "scalability":
            try:
                from chayuan.server.config_panel.performance_page import (
                    render_performance_page,
                )
                render_performance_page(ui)
            except Exception as e:  # noqa: BLE001
                logger.exception("render_performance_page failed")
                _render_page_error(ui, "性能与可扩展性", e)
                ui.separator().classes("q-my-sm")
                ui.label("下方为兼容视图（静态体检）").classes("text-xs text-grey-7")
                try:
                    from chayuan.server.config_panel.scalability import render_scalability
                    render_scalability(ui)
                except Exception as e2:  # noqa: BLE001
                    logger.exception("fallback render_scalability also failed")
                    _render_page_error(ui, "（兼容视图)静态体检", e2)
        elif key == "users":
            from chayuan.server.config_panel.users_page import render_users_page
            try:
                render_users_page(ui)
            except Exception as e:  # noqa: BLE001
                logger.exception("render_users_page failed")
                ui.label("用户管理页渲染失败").classes("text-negative text-lg font-semibold")
                ui.label(f"{type(e).__name__}: {e}").classes("text-sm font-mono text-grey-8")
        elif key == "kb_acl":
            from chayuan.server.config_panel.kb_acl_page import render_kb_acl_page
            try:
                render_kb_acl_page(ui)
            except Exception as e:  # noqa: BLE001
                logger.exception("render_kb_acl_page failed")
                ui.label("KB 访问控制页渲染失败").classes("text-negative text-lg font-semibold")
                ui.label(f"{type(e).__name__}: {e}").classes("text-sm font-mono text-grey-8")
        elif key == "kb_manage":
            try:
                from chayuan.server.config_panel.kb_manage_page import (
                    render_kb_manage_page,
                )

                # 76 题:知识库管理变更走 Settings.auto_reload + DB,不需要重启
                def _mark_kb_manage_restart_needed() -> None:
                    logger.debug(
                        "[dashboard] kb_manage 已修改 — 走 DB + Settings.auto_reload,"
                        "不显示重启 banner",
                    )

                render_kb_manage_page(
                    ui, mark_restart_needed=_mark_kb_manage_restart_needed,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("render_kb_manage_page failed")
                ui.label("知识库管理页渲染失败").classes(
                    "text-negative text-lg font-semibold"
                )
                ui.label(f"{type(e).__name__}: {e}").classes(
                    "text-sm font-mono text-grey-8"
                )
        elif key == "custom_tools":
            ui.label("自定义工具").classes("text-2xl font-semibold q-mb-sm")
            ui.label(
                "把任意 HTTP 接口包装成 Agent 工具：定义 URL / 方法 / 参数 schema 与"
                "描述后，LLM 就能按描述提示词自动调用该接口并把响应喂给下游。"
            ).classes("text-sm text-grey-7 q-mb-md")
            try:
                from chayuan.server.config_panel.custom_tools_page import (
                    render_custom_tools_page,
                )

                def _mark_custom_restart() -> None:
                    pending_restart.setdefault("custom_tools.yaml", set()).add(
                        "custom_tools"
                    )
                    refresh_banner()

                render_custom_tools_page(ui, mark_restart_needed=_mark_custom_restart)
            except Exception as e:  # noqa: BLE001
                logger.exception("render_custom_tools_page failed")
                ui.label(f"自定义工具页渲染失败：{type(e).__name__}: {e}").classes(
                    "text-negative text-sm"
                )
        elif key == "ws_endpoints":
            ui.label("WebSocket 端点").classes("text-2xl font-semibold q-mb-sm")
            ui.label(
                "把任意 ws:// / wss:// 接口包装成 Agent 工具："
                "LLM 给参数 → Chayuan 建立 WebSocket → 发请求 → 收 N 条或超时 → "
                "按固定格式返回。适合流式行情、对话 SSE-over-WS 等场景。"
            ).classes("text-sm text-grey-7 q-mb-md")
            try:
                from chayuan.server.config_panel.websocket_endpoints_page import (
                    render_ws_endpoints_page,
                )

                def _mark_ws_restart() -> None:
                    pending_restart.setdefault("websocket_endpoints.yaml", set()).add(
                        "ws_endpoints"
                    )
                    refresh_banner()

                render_ws_endpoints_page(ui, mark_restart_needed=_mark_ws_restart)
            except Exception as e:  # noqa: BLE001
                logger.exception("render_ws_endpoints_page failed")
                ui.label(f"WebSocket 端点页渲染失败：{type(e).__name__}: {e}").classes(
                    "text-negative text-sm"
                )
        elif key == "apps":
            ui.label("开放平台 · App 管理").classes("text-2xl font-semibold q-mb-sm")
            ui.label(
                "用签名 OpenAPI 让外部系统调用察元（/openapi/v1/*），"
                "或订阅回调事件让察元把事件推给它们。"
            ).classes("text-sm text-grey-7 q-mb-md")
            try:
                from chayuan.server.config_panel.apps_page import render_apps_page
                render_apps_page(ui)
            except Exception as e:  # noqa: BLE001
                logger.exception("render_apps_page failed")
                ui.label(f"App 管理页渲染失败：{type(e).__name__}: {e}").classes(
                    "text-negative text-sm"
                )
        elif key == "mcp_manage":
            ui.label("MCP 管理").classes("text-2xl font-semibold q-mb-sm")
            ui.label(
                "Model Context Protocol 服务的通用配置与连接管理。"
                "修改后部分字段（如传输参数、command）需重启 MCP Server 进程生效。"
            ).classes("text-sm text-grey-7 q-mb-md")
            try:
                from chayuan.server.config_panel.mcp_manage_page import (
                    render_mcp_manage_page,
                )

                def _mark_mcp_restart_needed() -> None:
                    pending_restart.setdefault("basic_settings.yaml", set()).add(
                        "mcp_connection / mcp_profile"
                    )
                    refresh_banner()

                render_mcp_manage_page(
                    ui, mark_restart_needed=_mark_mcp_restart_needed,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("render_mcp_manage_page failed")
                ui.label("MCP 管理页渲染失败").classes(
                    "text-negative text-lg font-semibold"
                )
                ui.label(f"{type(e).__name__}: {e}").classes(
                    "text-sm font-mono text-grey-8"
                )
        elif key.startswith("file:"):
            filename = key[len("file:"):]
            # 62 题:支持 file:xxx#tab 协议,把 # 后面的 tab id 提出来
            # 供子页面初始化时直接定位到指定 tab(例如默认 LLM 卡片"去配置"
            # → "file:model_settings.yaml#defaults" → 直接落到 ④ 默认模型 tab)
            initial_tab: Optional[str] = None
            if "#" in filename:
                filename, initial_tab = filename.split("#", 1)
            fs = _schema.schema_by_filename(filename)
            if fs is None:
                ui.label(f"未知配置文件：{filename}").classes("text-negative")
                return
            # 知识库页单独加一个顶部卡片：结构化的向量库/检索引擎配置。
            # 其余字段（chunk_size、text_splitter 等）仍走通用 _render_file_page
            # 的 compact 表单模式，去掉「原始 YAML」tab 后视觉更聚焦。
            if filename == "kb_settings.yaml":
                ui.label(fs.title).classes("text-2xl font-semibold q-mb-sm")
                try:
                    from chayuan.server.config_panel.vs_config import render_vs_card

                    # 76 题:kb_settings.yaml 通过 Settings.auto_reload(yaml mtime
                    # cache key)能立即生效,不再显示"重启" banner。
                    # 之前的 pending_restart + refresh_banner 是误导,实际 Settings
                    # 在下次访问时已经读到新 yaml/DB 数据。
                    def _mark_kb_restart_needed() -> None:
                        logger.debug(
                            "[dashboard] kb_settings 已修改 — Settings.auto_reload "
                            "自动刷新,不再显示重启 banner",
                        )

                    render_vs_card(ui, mark_restart_needed=_mark_kb_restart_needed)
                except Exception as e:  # noqa: BLE001
                    logger.exception("render_vs_card failed")
                    ui.label(f"向量库配置卡片渲染失败：{type(e).__name__}: {e}").classes(
                        "text-negative text-sm"
                    )
                _render_file_page(
                    ui, fs, pending_restart, refresh_banner, compact=True
                )
            elif filename == "model_settings.yaml":
                # 57 题:模型设置页 v2 — 4 tabs lazy 渲染版,替代旧 3196 行单页。
                # 顶部 4 tab(① 运行时 / ② 厂商 / ③ 广场 / ④ 默认模型),NiceGUI
                # 原生 lazy:只渲染当前 tab,首屏从 ~2-3s 降到 ~500ms,
                # connection lost 根治。
                try:
                    from chayuan.server.config_panel.model_settings import (
                        render_model_settings_v2,
                    )

                    # 68 题:model_settings.yaml 通过 Settings.auto_reload(yaml mtime
                    # 做 cache key)+ config_center DB 镜像可以立即生效,不需要
                    # "重启"提示。这里把 mark_restart 设为 noop —
                    # 之前的 pending_restart + refresh_banner 是误导,实际 Settings
                    # 在下次访问时已经读到了新的 yaml/DB 数据。
                    def _mark_model_restart_needed() -> None:
                        logger.debug(
                            "[dashboard] model_settings 已修改 — Settings.auto_reload "
                            "会自动刷新,不再显示重启 banner",
                        )

                    render_model_settings_v2(
                        ui,
                        mark_restart_needed=_mark_model_restart_needed,
                        initial_tab=initial_tab,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception("render_model_settings_v2 failed")
                    ui.label(f"模型平台配置渲染失败:{type(e).__name__}: {e}").classes(
                        "text-negative text-sm"
                    )
            elif filename == "tool_settings.yaml":
                # 工具配置页：完全定制化——去掉通用「可视化 / 原始 YAML」双 tab，
                # 换成按工具分组的卡片列表（render_tool_settings_page）。
                # 每个工具独立启用 + 参数一体化展示；text2images.model 自动绑定
                # model_settings.yaml 中 text2image 模型的下拉。
                try:
                    from chayuan.server.config_panel.tools_page import (
                        render_tool_settings_page,
                    )

                    def _mark_tool_restart_needed() -> None:
                        pending_restart.setdefault("tool_settings.yaml", set()).add(
                            "tools"
                        )
                        refresh_banner()

                    render_tool_settings_page(
                        ui, mark_restart_needed=_mark_tool_restart_needed,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception("render_tool_settings_page failed")
                    ui.label(f"工具配置页渲染失败：{type(e).__name__}: {e}").classes(
                        "text-negative text-sm"
                    )
            else:
                _render_file_page(ui, fs, pending_restart, refresh_banner)
        else:
            ui.label(f"未知导航项：{key}").classes("text-negative")


# ---------------------------------------------------------------------------
# ① 项目状态
# ---------------------------------------------------------------------------

def _probe(host: str, port: int, timeout: float = 0.6) -> bool:
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


def _probe_business_db() -> Tuple[bool, str]:
    """快速探活业务库（dashboard 横幅用）。

    与 startup._probe_db_status 同语义，但放在 dashboard 模块里以避免循环 import。
    超时 3s；任何异常 → False + 简短原因。
    """
    try:
        from sqlalchemy import create_engine, text

        uri = getattr(Settings.basic_settings, "SQLALCHEMY_DATABASE_URI", "") or ""
        if not uri:
            return False, "basic_settings.SQLALCHEMY_DATABASE_URI 为空"
        # connect_timeout 只对 PG/MySQL 有效;SQLite 会抛 TypeError(driver 不接受)
        _connect_args = (
            {"connect_timeout": 3}
            if uri.startswith(("postgresql", "postgres", "mysql"))
            else {}
        )
        eng = create_engine(uri, pool_pre_ping=True, connect_args=_connect_args)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, uri
    except Exception as e:  # noqa: BLE001
        # 只取错误首行，避免横幅过长
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        return False, f"{type(e).__name__}: {first[:160]}"


def _render_status_page(ui) -> None:
    ui.label("项目状态").classes("text-2xl font-semibold q-mb-sm")
    ui.label(
        "按项目生命周期快速检查：数据目录是否准备好、后台服务是否在运行、"
        "面板账号是否已配置。这里的服务探活实时刷新，不会改动任何配置。"
    ).classes("text-sm text-grey-8 q-mb-md")

    bs = Settings.basic_settings
    from chayuan.server.config_panel.restart import runtime_meta_path, load_runtime

    api = dict(getattr(bs, "API_SERVER", {}) or {})
    cfg = dict(getattr(bs, "CONFIG_SERVER", {}) or {})

    from chayuan.paths import resolve_chayuan_root
    root_info = resolve_chayuan_root()
    src_label = {
        "env": "来自 $CHAYUAN_ROOT",
        "xdg": "XDG 默认",
        "macos": "macOS 默认",
        "windows": "Windows 默认",
        "linux": "Linux 默认",
    }.get(root_info.source, root_info.source)

    # 数据目录卡
    with ui.card().classes("w-full q-mb-md"):
        with ui.row().classes("items-center w-full"):
            ui.label("数据目录 CHAYUAN_ROOT").classes("text-base font-semibold")
            ui.space()
            ui.badge(src_label).props(
                "color=" + ("green" if root_info.source == "env" else "grey")
            )
        ui.label(str(CHAYUAN_ROOT)).classes("text-sm font-mono text-primary")
        files = [s.filename for s in _schema.ALL_SCHEMAS]
        with ui.grid(columns=2).classes("w-full q-mt-sm"):
            for f in files:
                p = yaml_store.yaml_path(f)
                ok = p.is_file()
                ui.label(f"{'✅' if ok else '⚠️'}  {f}").classes(
                    "text-sm " + ("" if ok else "text-negative")
                )

    # 服务探活
    with ui.card().classes("w-full q-mb-md"):
        ui.label("后台服务").classes("text-base font-semibold")
        rows = [
            ("API",    api.get("host", "127.0.0.1"), api.get("port", 62581)),
            ("配置面板", cfg.get("host", "127.0.0.1"), cfg.get("port", 8502)),
        ]
        for name, h, p in rows:
            alive = _probe(h, p)
            mark = "🟢 运行中" if alive else "🔴 未运行"
            with ui.row().classes("items-center w-full"):
                ui.label(f"{name:<6}").classes("text-sm w-16")
                ui.label(f"{h}:{p}").classes("text-sm font-mono text-grey-8")
                ui.space()
                ui.label(mark).classes("text-sm")

    # 面板账号
    with ui.card().classes("w-full"):
        ui.label("配置面板登录信息").classes("text-base font-semibold")
        user = (getattr(bs, "PANEL_USERNAME", "") or "").strip() or "（未设置）"
        has_pw = bool((getattr(bs, "PANEL_PASSWORD_HASH", "") or "").strip())
        login_path = (getattr(bs, "PANEL_LOGIN_PATH", "") or "").strip().strip("/") or "（未设置）"
        with ui.grid(columns=2).classes("w-full q-mt-sm"):
            ui.label(f"用户名：{user}")
            ui.label(f"密码：{'已设置' if has_pw else '未设置'}")
            ui.label(f"登录路径：/{login_path}")
            ui.label(f"端口：{cfg.get('port', 8502)}")
        ui.label(
            "这四项只能用 CLI 修改（`chayuan update username/password/port/path`），"
            "面板内不提供在线修改。"
        ).classes("text-xs text-grey-7 q-mt-sm")

    # 运行时元数据
    meta = load_runtime()
    with ui.card().classes("w-full q-mt-md"):
        ui.label("运行时元数据").classes("text-base font-semibold")
        ui.label(str(runtime_meta_path())).classes("text-xs font-mono text-grey-8")
        if meta:
            ui.label(f"父进程 pid = {meta.get('pid')}，命令：{' '.join(meta.get('argv') or [])}")\
                .classes("text-sm")
            ui.label(f"启动工作目录：{meta.get('cwd')}").classes("text-xs text-grey-8")
        else:
            ui.label(
                "未找到。通常是通过非 `chayuan start` 的方式拉起面板；「立即重启」按钮将不可用。"
            ).classes("text-sm text-negative")


# ---------------------------------------------------------------------------
# ② 基础配置（数据目录 + basic_settings.yaml）
# ---------------------------------------------------------------------------

def _render_root_page(
    ui,
    pending_restart: Dict[str, set],
    refresh_banner: Callable[[], None],
    *,
    navigate_to: Callable[[str], None] | None = None,
) -> None:
    """② 基础配置。

    页面主体三张卡，都是会保存到后端的「表单」：
    1. 数据目录配置（``_render_data_root_card``）——改 ``CHAYUAN_ROOT`` 并写入 shell rc。
    2. 主业务数据库（``db_config.render_main_db_card``）——方言下拉 + 结构化字段
       + 「验证连接」按钮；保存时统一组装成 ``SQLALCHEMY_DATABASE_URI`` 写入 yaml。
    3. ``basic_settings.yaml`` 其余字段（``_render_file_page`` compact）——端口、性能等
       非数据库相关配置，保存写磁盘。

    辅助信息（一致性诊断、派生路径、命令行速查）收在右上角帮助按钮弹出的
    ``ui.dialog`` 里，默认不占主区空间。
    """
    from chayuan.paths import resolve_chayuan_root, read_user_state

    root_info = resolve_chayuan_root()
    env_value = (os.environ.get("CHAYUAN_ROOT") or "").strip() or None
    user_state = read_user_state()
    last_init_root = user_state.get("last_init_root") or None
    last_activate_sh = user_state.get("last_activate_sh") or None
    last_init_at = user_state.get("last_init_at") or None

    help_dialog = _build_basic_help_dialog(
        ui,
        actual_root=str(CHAYUAN_ROOT),
        env_root=env_value,
        last_init_root=last_init_root,
        last_activate_sh=last_activate_sh,
        last_init_at=last_init_at,
        source_reason=root_info.reason,
        source=root_info.source,
    )

    # --- Header：标题 + 右上角 help 按钮 -------------------------------------
    with ui.row().classes("items-center w-full q-mb-md no-wrap"):
        ui.label("基础配置").classes("text-2xl font-semibold")
        ui.space()
        ui.button(
            icon="help_outline",
            on_click=help_dialog.open,
        ).props("flat round color=primary").tooltip(
            "帮助 / 使用说明"
        )

    # --- 数据目录配置（独立卡，可编辑：改 CHAYUAN_ROOT 并写入 shell rc）--------
    _render_data_root_card(
        ui,
        current_root=str(CHAYUAN_ROOT),
        source_reason=root_info.reason,
        open_help=help_dialog.open,
    )

    # 文件存储 / 主业务数据库 / Redis 连接：已迁至「🚀 服务配置」页内联编辑。
    # 这里给一条小提示，引导用户去新位置；不再重复渲染编辑卡片。
    with ui.card().classes("w-full q-pa-md").style(
        "background:#eef6ff;border-left:4px solid #2185d0"
    ):
        with ui.row().classes("items-center w-full no-wrap").style("gap:10px"):
            ui.icon("info").classes("text-info").style("font-size:24px;flex:none")
            with ui.column().classes("flex-1 q-gutter-none"):
                ui.label("基础设施连接配置已迁移").classes("text-base font-semibold")
                ui.label(
                    "主业务数据库 / Redis / 文件存储（MinIO）现统一在「🚀 服务配置」"
                    "页拓扑卡片上的「编辑」按钮。本页只保留数据目录、端口、性能等字段。"
                ).classes("text-xs text-grey-8")
            ui.button(
                "去服务配置", icon="open_in_new",
                on_click=lambda: (
                    navigate_to("service_config") if navigate_to else None
                ),
            ).props("flat color=primary")

    # --- basic_settings.yaml 其它字段（端口 / 性能等；紧凑表单，保存写磁盘） --
    fs_basic = _schema.schema_by_filename("basic_settings.yaml")
    if fs_basic is None:
        ui.label("未能加载 basic_settings.yaml schema").classes("text-negative")
        return
    _render_file_page(ui, fs_basic, pending_restart, refresh_banner, compact=True)


def _build_basic_help_dialog(
    ui,
    *,
    actual_root: str,
    env_root: str | None,
    last_init_root: str | None,
    last_activate_sh: str | None,
    last_init_at: str | None,
    source_reason: str,
    source: str,
):
    """构建「基础配置」页右上角 help 按钮弹出的 dialog。

    把原来散布在主区的 5 块辅助信息（一致性诊断 / 当前 CHAYUAN_ROOT 详情 /
    派生路径 / shell rc 编辑器 / 命令行速查）收进一个可滚动的 dialog。
    返回创建好的 ``ui.dialog`` 对象（未打开），调用方用 ``dialog.open`` 绑定按钮。
    """
    with ui.dialog() as dlg:
        with ui.card().classes("q-pa-md").style(
            "min-width: min(880px, 92vw); max-width: 92vw; "
            "max-height: 92vh; overflow: auto;"
        ):
            with ui.row().classes("items-center w-full q-mb-md no-wrap"):
                ui.icon("help_outline").classes("text-primary").style(
                    "font-size: 28px"
                )
                with ui.column().classes("q-gutter-none flex-1"):
                    ui.label("基础配置 · 帮助").classes("text-xl font-semibold")
                    ui.label(
                        "本弹窗集中展示数据目录相关的诊断、派生路径、切换命令；"
                        "只读信息，不影响 basic_settings.yaml 表单的保存。"
                    ).classes("text-xs text-grey-7")
                ui.button(icon="close", on_click=dlg.close).props(
                    "flat round dense"
                ).tooltip("关闭")

            _render_root_consistency_card(
                ui,
                actual_root=actual_root,
                env_root=env_root,
                last_init_root=last_init_root,
                last_activate_sh=last_activate_sh,
                last_init_at=last_init_at,
                source_reason=source_reason,
            )

            _render_current_root_detail_card(
                ui,
                actual_root=actual_root,
                source=source,
                source_reason=source_reason,
            )

            _render_derived_paths_card(ui)

            _render_cli_cheatsheet_card(
                ui,
                actual_root=actual_root,
                last_activate_sh=last_activate_sh,
            )

            with ui.row().classes("w-full justify-end q-mt-sm"):
                ui.button("我知道了", on_click=dlg.close).props("color=primary")
    return dlg


def _render_current_root_detail_card(
    ui,
    *,
    actual_root: str,
    source: str,
    source_reason: str,
) -> None:
    """当前 CHAYUAN_ROOT 详情卡：解释来源并给出持久化 export 命令。

    用在帮助 dialog 里；主区的回显卡更精简，只展示路径本身。
    """
    with ui.card().classes("w-full q-mb-md"):
        ui.label("当前 CHAYUAN_ROOT（进程实际使用）").classes(
            "text-base font-semibold"
        )
        ui.label(actual_root).classes("text-lg font-mono text-primary").style(
            "word-break: break-all"
        )
        ui.label(source_reason).classes("text-xs text-grey-7")
        ui.separator().classes("q-my-sm")
        if source == "init":
            ui.label(
                "当前路径来自最近一次 `chayuan init` 写入的 state.json —— 无论 shell 里的 "
                "$CHAYUAN_ROOT 是什么，服务都会走这里（除非设置 "
                "CHAYUAN_ROOT_IGNORE_STATE=1）。若想让新终端里的 `chayuan` 命令也"
                "自动感知该路径，可把下面这行写进 shell rc："
            ).classes("text-sm text-grey-8")
        elif source == "env":
            ui.label(
                "当前路径由 $CHAYUAN_ROOT 显式指定。若未在 ~/.zshrc 里 export，"
                "新终端会落回 OS 默认路径。可把下面这行追加到 rc 里持久化："
            ).classes("text-sm text-grey-8")
        else:
            ui.label(
                "当前路径来自 OS 默认（或 $XDG_DATA_HOME）。以下 export 可把选择"
                "显式化，新终端也会使用这份数据："
            ).classes("text-sm text-grey-8")
        _code_block(ui, f'export CHAYUAN_ROOT="{actual_root}"')


def _code_block(ui, text: str, language: str = "bash") -> None:
    """渲染一个带「复制」按钮的命令行代码块。

    纯 ``ui.code`` 只能选中复制，用户场景里常常是一串多行 shell 命令，右上角挂一
    个一键复制按钮最直观；hover 时按钮变得更突出。
    """
    with ui.row().classes("items-start w-full no-wrap q-mb-sm").style("gap:4px"):
        code_el = ui.code(text, language=language)
        code_el.classes("flex-1").style("margin:0;min-width:0")
        ui.button(
            icon="content_copy",
            on_click=lambda t=text: _copy_to_clipboard(ui, t),
        ).props("flat dense round size=sm color=primary").tooltip("复制到剪贴板")


def _render_cli_cheatsheet_card(
    ui,
    *,
    actual_root: str,
    last_activate_sh: str | None,
) -> None:
    """「配置说明 / 命令行速查」——项目里最高频的几个 shell 命令，一键复制。"""
    activate_snippet = (
        f"source {last_activate_sh}"
        if last_activate_sh
        else f'export CHAYUAN_ROOT="{actual_root}"'
    )
    with ui.card().classes("w-full q-mb-md"):
        ui.label("配置说明 / 命令行速查").classes("text-base font-semibold")
        ui.label(
            "察元的所有持久化数据（配置、知识库、日志、向量库）都放在 CHAYUAN_ROOT 下。"
            "运行期进程的 CHAYUAN_ROOT 由启动时的 shell env 决定，不跟随界面里的修改。"
            "以下命令覆盖了 95% 的日常操作，每块右上角都能一键复制。"
        ).classes("text-xs text-grey-7 q-mb-sm")

        def _block(title: str, help_text: str, code: str) -> None:
            ui.label(title).classes("text-sm font-semibold q-mt-sm")
            if help_text:
                ui.label(help_text).classes("text-xs text-grey-7")
            _code_block(ui, code)

        _block(
            "① 初始化 / 切换数据目录",
            "交互式向导：选择数据根目录、生成默认 yaml、在目录里落一份 activate.sh。",
            "chayuan init",
        )
        _block(
            "② 临时进入当前数据目录所在 shell",
            "直接 source 向导落下的 activate.sh，新 shell 会自带 CHAYUAN_ROOT。",
            activate_snippet,
        )
        _block(
            "③ 启动所有服务（API + WebUI + 配置面板）",
            "启动前建议先 source activate.sh，否则会读到别的 CHAYUAN_ROOT。",
            "chayuan start",
        )
        _block(
            "④ 只启动配置面板（不起对话 / API）",
            "日常改配置场景，只需要这一个进程。",
            "chayuan start -c",
        )
        _block(
            "⑤ 把 CHAYUAN_ROOT 固化到 ~/.zshrc",
            "让任何新开的终端自动带上当前数据目录。",
            f'echo \'export CHAYUAN_ROOT="{actual_root}"\' >> ~/.zshrc\nsource ~/.zshrc',
        )


def _detect_shell_rc() -> Tuple[str, str]:
    """返回 ``(shell_kind, rc_path)``；鸟类：``zsh`` / ``bash`` / ``fish``。"""
    shell = os.environ.get("SHELL", "")
    home = Path.home()
    name = Path(shell).name if shell else ""
    if "zsh" in name or (home / ".zshrc").is_file():
        return "zsh", str(home / ".zshrc")
    if "fish" in name:
        return "fish", str(home / ".config" / "fish" / "config.fish")
    if "bash" in name or (home / ".bashrc").is_file():
        return "bash", str(home / ".bashrc")
    return "sh", str(home / ".profile")


def _upsert_export_line(rc_path: Path, var: str, value: str, shell_kind: str) -> Tuple[bool, str]:
    """幂等地写 / 更新 ``export VAR=value``（对 fish 用 ``set -x``）。

    返回 ``(changed, action)``：action 为 ``appended`` / ``replaced`` / ``noop``。
    """
    if shell_kind == "fish":
        new_line = f'set -x {var} "{value}"'
        pattern = f"set -x {var} "
    else:
        new_line = f'export {var}="{value}"'
        pattern = f"export {var}="

    try:
        rc_path.parent.mkdir(parents=True, exist_ok=True)
        lines = rc_path.read_text(encoding="utf-8").splitlines() if rc_path.is_file() else []
    except OSError as e:
        raise RuntimeError(f"读取 {rc_path} 失败：{e}") from e

    found_idx = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith(pattern):
            found_idx = i
            break

    if found_idx is not None:
        if lines[found_idx].strip() == new_line:
            return False, "noop"
        lines[found_idx] = new_line
        action = "replaced"
    else:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append(f"# Added by 察元AI助手 config panel on {datetime.now().isoformat(timespec='seconds')}")
        lines.append(new_line)
        action = "appended"

    tmp = rc_path.with_suffix(rc_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, rc_path)
    return True, action


def _render_root_consistency_card(
    ui,
    *,
    actual_root: str,
    env_root: str | None,
    last_init_root: str | None,
    last_activate_sh: str | None,
    last_init_at: str | None,
    source_reason: str,
) -> None:
    """展示「进程实际 root / shell env / 向导记录」三者的一致性。

    新逻辑（与 ``paths.resolve_chayuan_root`` 对齐）：
    - 只要 ``state.json`` 里有 ``last_init_root`` 且目录存在，服务就一定跑在那里，
      与 shell 里的 ``$CHAYUAN_ROOT`` 是否一致无关 —— 所以「init 选择 ≠ 实际」
      理论上不会再发生。
    - 若 ``$CHAYUAN_ROOT`` 与实际不一致，通常说明用户 shell 里还留着老 export；
      这**不再是红字错误**，而是蓝色提示「已按最近一次 init 选择自动覆盖环境变量」。
    - 只有 ``init 选择 ≠ 实际`` 或者找不到 ``last_init_root`` 但 env 也不匹配时，
      才画红字并给出修复命令。
    """
    inconsistencies: list[str] = []  # 真正的「红字错误」
    notes: list[str] = []             # 蓝色告知（不算错误）

    last_init_matches_actual = (
        last_init_root
        and _normalize_path(last_init_root) == _normalize_path(actual_root)
    )
    env_matches_actual = (
        env_root and _normalize_path(env_root) == _normalize_path(actual_root)
    )

    if last_init_root and not last_init_matches_actual:
        inconsistencies.append(
            f"最近一次 `chayuan init` 向导选择的数据目录是 {last_init_root}，"
            f"但当前进程实际跑在 {actual_root}。正常情况下 init 选择应自动生效，"
            "这里不匹配可能是该目录被删除 / 移走，或设置了 CHAYUAN_ROOT_IGNORE_STATE=1 "
            "跳过了 state.json 回退逻辑。"
        )
    elif env_root and not env_matches_actual:
        # 只要 last_init_root 已经和 actual 对齐，env 不匹配就只是提示，不算错误。
        if last_init_matches_actual:
            notes.append(
                f"当前 shell 里的 $CHAYUAN_ROOT={env_root}，与服务实际使用的 {actual_root} "
                "不一致——已按最近一次 `chayuan init` 选择自动覆盖，无需处理。"
                "如确需让 env 生效，请 `export CHAYUAN_ROOT_IGNORE_STATE=1` 再启动。"
            )
        else:
            inconsistencies.append(
                f"当前面板进程启动时读到的 $CHAYUAN_ROOT={env_root}，"
                f"和运行时解析到的 {actual_root} 不一致。"
            )

    if inconsistencies:
        color = "bg-red-1 border-red"
    elif notes:
        color = "bg-blue-1 border-blue"
    else:
        color = "bg-green-1 border-green"
    with ui.card().classes(f"w-full q-mb-md {color}"):
        with ui.row().classes("items-center w-full"):
            if inconsistencies:
                icon_name, icon_color, title_suffix = "warning", "text-red", "：不一致，请查看下方说明"
            elif notes:
                icon_name, icon_color, title_suffix = "info", "text-blue", "：已按最近一次 init 自动对齐"
            else:
                icon_name, icon_color, title_suffix = "rule", "text-green", "：OK"
            ui.icon(icon_name).classes(icon_color)
            ui.label("数据目录一致性" + title_suffix).classes("text-base font-semibold")

        def _row(label: str, value: str | None, muted: str | None = None) -> None:
            with ui.row().classes("items-start w-full q-mt-xs"):
                ui.label(label).classes("text-sm text-grey-8").style("min-width: 200px")
                if value:
                    ui.label(str(value)).classes("text-sm font-mono")
                else:
                    ui.label("（未设置）").classes("text-sm text-grey-7 italic")
                if muted:
                    ui.label(muted).classes("text-xs text-grey-7 q-ml-sm")

        _row("进程实际使用 (Settings.CHAYUAN_ROOT)", actual_root, source_reason)
        _row("当前 shell / 容器 env $CHAYUAN_ROOT", env_root)
        _row(
            "最近一次 `chayuan init` 向导选择",
            last_init_root,
            f"记录时间：{last_init_at}" if last_init_at else None,
        )
        if last_activate_sh:
            _row("向导写入的 activate.sh", last_activate_sh)

        if inconsistencies:
            ui.separator().classes("q-my-sm")
            for msg in inconsistencies:
                ui.label("• " + msg).classes("text-sm text-red-10 q-mb-xs")
            if last_activate_sh:
                ui.label("修复步骤（任选其一即可）：").classes("text-sm font-semibold q-mt-sm")
                _code_block(
                    ui,
                    f"# 方式一：立刻用向导记录的 activate.sh 切回\n"
                    f"source {last_activate_sh} && chayuan start -c\n"
                    f"\n"
                    f"# 方式二：在 ~/.zshrc 里 export，新开终端自动生效\n"
                    f"echo 'export CHAYUAN_ROOT=\"{last_init_root}\"' >> ~/.zshrc\n"
                    f"source ~/.zshrc && chayuan start -c",
                )
            else:
                _code_block(
                    ui,
                    f'export CHAYUAN_ROOT="{last_init_root or actual_root}" && chayuan start -c',
                )
        elif notes:
            ui.separator().classes("q-my-sm")
            for msg in notes:
                ui.label("• " + msg).classes("text-sm text-blue-10 q-mb-xs")


def _normalize_path(p: str) -> str:
    try:
        return str(Path(p).expanduser().resolve())
    except Exception:
        return p or ""


def _render_derived_paths_card(ui) -> None:
    """展示 KB_ROOT_PATH / DB_ROOT_PATH / SQLALCHEMY_DATABASE_URI 的生效值。

    重点在「真实生效值」而不是 yaml 原文：yaml 里可以是空串 / legacy 绝对路径，
    validator 统一派生成下方这几项。用户一眼能看出「跟随 CHAYUAN_ROOT」是否生效。
    """
    bs = Settings.basic_settings
    # 从 yaml 原文读出 raw 值，便于对比是「yaml 留空派生」还是「yaml 显式指定」。
    try:
        raw = yaml_store.load_yaml("basic_settings.yaml").doc or {}
    except Exception:
        raw = {}

    def _origin(key: str) -> str:
        val = raw.get(key, None)
        if val in (None, ""):
            return "跟随 CHAYUAN_ROOT"
        return "yaml 显式指定"

    items = [
        ("KB_ROOT_PATH", str(bs.KB_ROOT_PATH)),
        ("DB_ROOT_PATH", str(bs.DB_ROOT_PATH)),
        ("SQLALCHEMY_DATABASE_URI", str(bs.SQLALCHEMY_DATABASE_URI)),
    ]

    with ui.card().classes("w-full q-mb-md"):
        ui.label("派生路径 / 数据库 URI（运行时生效值）").classes("text-base font-semibold")
        ui.label(
            "下面是 Settings 读取后实际使用的值。如果 yaml 里这几项留空，程序会按 CHAYUAN_ROOT "
            "派生；如果 yaml 里是一段不属于当前 OS 的路径（例如 Windows 迁来的 `D:\\…`），"
            "程序会自动回退并 stderr 打 warning，无需手动清理——但建议到下方「基础配置」页"
            "把字段清空，这样以后跟随数据目录漂移。"
        ).classes("text-xs text-grey-7 q-mb-sm")

        for key, value in items:
            with ui.row().classes("items-center w-full q-mb-xs"):
                ui.label(key).classes("text-sm text-grey-8").style("min-width: 240px")
                ui.label(value).classes("text-sm font-mono")
                ui.space()
                origin = _origin(key)
                color = "bg-blue-2" if origin == "跟随 CHAYUAN_ROOT" else "bg-amber-2"
                ui.label(origin).classes(f"text-xs {color} q-px-sm q-py-xs rounded-borders")


def _render_data_root_card(
    ui,
    *,
    current_root: str,
    source_reason: str,
    open_help: Callable[[], None] | None = None,
) -> None:
    """「数据目录配置」紧凑表单卡。

    页面要点：只有表单控件，不放说明文字；解释性内容全部走 tooltip 或卡右上角
    的 help 按钮弹 dialog。

    布局：
    - 卡顶一行：标题 `数据目录配置` + 当前路径回显（monospace，灰底）+ help 按钮；
    - 一行：`新的 CHAYUAN_ROOT` 输入（占大部分宽度）+ 「复制」按钮；
    - 一行：`shell` 下拉 + `rc 文件` 输入（自动联动）+ 「写入 rc」按钮。
    """
    shell_kind, default_rc = _detect_shell_rc()

    with ui.card().classes("w-full q-mb-md q-pa-md"):
        # 顶栏（标题 / 当前路径回显 / help 按钮）按用户要求整行移除——
        # 当前 CHAYUAN_ROOT 仍然通过「新的 CHAYUAN_ROOT」输入框默认值回显，
        # 诊断信息统一在页面右上角 help 按钮弹窗中查看。

        # --- 行 1：新路径 + 复制 ---------------------------------------------
        with ui.row().classes("items-center w-full no-wrap q-mb-sm").style("gap:8px"):
            path_input = ui.input(
                label="新的 CHAYUAN_ROOT",
                value=current_root,
            ).props("outlined dense").classes("flex-1")
            path_input.tooltip(
                "想切换到的目录；可以是空目录。保存到 rc 后需新开终端或 source rc 才会生效，"
                "当前运行的服务不会自动跟随。"
            )
            ui.button(
                icon="content_copy",
                on_click=lambda: _copy_to_clipboard(
                    ui, f'export CHAYUAN_ROOT="{path_input.value}"'
                ),
            ).props("flat dense round color=primary").tooltip("复制 export 命令")

        # --- 行 2：shell + rc 路径 + 写入按钮 ---------------------------------
        with ui.row().classes("items-center w-full no-wrap").style("gap:8px"):
            shell_select = ui.select(
                ["zsh", "bash", "fish", "sh"],
                label="shell", value=shell_kind,
            ).props("outlined dense").style("min-width:120px")
            shell_select.tooltip("切换 shell 会自动填对应的 rc 文件默认路径")

            rc_input = ui.input(label="rc 文件路径", value=default_rc).props(
                "outlined dense"
            ).classes("flex-1")
            rc_input.tooltip(
                "要追加/替换 export 的 shell 启动脚本；已有同名 export 会被替换，不重复追加"
            )

            def _autofill_rc() -> None:
                home = Path.home()
                mapping = {
                    "zsh":  str(home / ".zshrc"),
                    "bash": str(home / ".bashrc"),
                    "fish": str(home / ".config" / "fish" / "config.fish"),
                    "sh":   str(home / ".profile"),
                }
                rc_input.value = mapping.get(shell_select.value, str(home / ".profile"))

            shell_select.on("update:model-value", lambda _=None: _autofill_rc())

            def _write_rc() -> None:
                try:
                    target_path = Path(rc_input.value).expanduser()
                    target_val = str(Path(path_input.value).expanduser())
                    changed, action = _upsert_export_line(
                        target_path, "CHAYUAN_ROOT", target_val, shell_select.value,
                    )
                except Exception as e:  # noqa: BLE001
                    ui.notify(f"写入失败：{type(e).__name__}: {e}", color="negative")
                    return
                if action == "noop":
                    ui.notify(
                        f"{target_path} 已经包含相同 export，无需修改。", color="info"
                    )
                elif action == "replaced":
                    ui.notify(
                        f"已替换 {target_path} 中旧的 export；新开终端即可生效。",
                        color="positive", timeout=8000,
                    )
                else:
                    ui.notify(
                        f"已追加到 {target_path}；新开终端即可生效。",
                        color="positive", timeout=8000,
                    )

            ui.button(
                "写入 rc", icon="save", on_click=_write_rc,
            ).props("color=primary dense").tooltip(
                "把 export CHAYUAN_ROOT=... 幂等写入选定的 shell 启动脚本"
            )


def _copy_to_clipboard(ui, text: str) -> None:
    try:
        ui.run_javascript(f"navigator.clipboard.writeText({text!r})")
        ui.notify("已复制到剪贴板。", color="info")
    except Exception as e:  # noqa: BLE001
        ui.notify(f"复制失败：{e}", color="negative")


# ---------------------------------------------------------------------------
# ③–⑦ 单个 yaml 文件页
# ---------------------------------------------------------------------------

def _render_file_page(
    ui,
    fs: "_schema.FileSchema",
    pending_restart: Dict[str, set],
    refresh_banner: Callable[[], None],
    *,
    compact: bool = False,
) -> None:
    """渲染一份 yaml 的「可视化配置 / 原始 YAML」tab 页。

    ``compact=True`` 时（供「基础配置」主页复用）：
    - 隐藏本页大标题（上游已经有「基础配置」H1，避免层级重复）；
    - 隐藏 ``fs.description`` 多行说明（交给帮助 dialog / tooltip）；
    - 只保留右上角一行 `✅ <path>` 状态，避免占空间。

    ``compact=False`` 是独立页签用法，照常显示完整标题 + 描述。
    """
    load_res = yaml_store.load_yaml(fs.filename)

    if compact:
        # compact 模式：不显示标题、文件路径、说明等任何辅助信息，
        # 直接铺表单；也不套 tabs 暴露原始 YAML 编辑器。
        _render_form_tab(
            ui, fs, load_res, pending_restart, refresh_banner, compact=True
        )
        return

    with ui.row().classes("items-center w-full q-mb-sm"):
        ui.label(fs.title).classes("text-2xl font-semibold")
        ui.space()
        status = "✅" if load_res.exists else "⚠️ 文件不存在（保存时会自动创建）"
        ui.label(f"{status}  {load_res.path}").classes(
            "text-xs text-grey-7 font-mono"
        )
    if fs.description:
        ui.label(fs.description).classes("text-sm text-grey-8 q-mb-md")

    with ui.tabs().classes("w-full") as sub_tabs:
        tab_form = ui.tab("form", label="可视化配置")
        tab_raw = ui.tab("raw", label="原始 YAML") if fs.raw_editor else None

    with ui.tab_panels(sub_tabs, value=tab_form).classes("w-full"):
        with ui.tab_panel(tab_form):
            _render_form_tab(
                ui, fs, load_res, pending_restart, refresh_banner, compact=False
            )
        if tab_raw is not None:
            with ui.tab_panel(tab_raw):
                _render_raw_tab(ui, fs, pending_restart, refresh_banner)


# --- 表单 tab ---

def _render_form_tab(
    ui,
    fs: "_schema.FileSchema",
    load_res: yaml_store.LoadResult,
    pending_restart: Dict[str, set],
    refresh_banner: Callable[[], None],
    *,
    compact: bool = False,
) -> None:
    """basic-style 表单。``compact=True`` 时段描述收进 section 标题的 tooltip，
    节省纵向空间；每个输入控件自己带 tooltip（``_build_input`` 里的 ``f.help``）。
    """
    inputs: List[Tuple["_schema.Field", Any]] = []
    gutter = "q-gutter-sm" if compact else "q-gutter-md"

    with ui.column().classes(f"w-full {gutter}"):
        for section in fs.sections:
            card_cls = "w-full q-pa-sm" if compact else "w-full"
            with ui.card().classes(card_cls):
                with ui.row().classes("items-center"):
                    section_title = ui.label(section.title).classes(
                        "text-base font-semibold"
                    )
                    if any(f.needs_restart for f in section.fields):
                        ui.badge("部分字段需重启").props("color=orange")
                    if compact and section.description:
                        section_title.tooltip(section.description)
                if section.description and not compact:
                    ui.label(section.description).classes("text-xs text-grey-7")
                ui.separator()

                with ui.grid(columns=2).classes("w-full q-gutter-sm"):
                    for f in section.fields:
                        current = yaml_store.get_by_path(load_res.doc, f.path)
                        el = _build_input(ui, f, current)
                        inputs.append((f, el))

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button(
                "重置", icon="undo",
                on_click=lambda: _reset_form(ui, fs, inputs, pending_restart, refresh_banner),
            ).props("flat")
            ui.button(
                "保存", icon="save",
                on_click=lambda: _save_form(ui, fs, inputs, pending_restart, refresh_banner),
            ).props("color=primary")


def _build_input(ui, f: "_schema.Field", current: Any):
    label = f.label + ("  (需重启)" if f.needs_restart else "  (即时生效)")
    tip = f.help or ""

    if f.widget == "switch":
        el = ui.switch(label, value=bool(current) if current is not None else False)
        if tip:
            el.tooltip(tip)
        return el

    if f.widget == "number":
        val: Any = current
        if val in (None, ""):
            val = None
        el = ui.number(
            label=label, value=val,
            min=f.min, max=f.max, step=f.step or 1, format=None,
        ).props("outlined dense").classes("w-full")
        if tip:
            el.tooltip(tip)
        return el

    if f.widget == "select":
        choices = list(f.choices or [])
        if current is not None and current not in choices:
            choices = [current, *choices]
        el = ui.select(choices, label=label, value=current).props("outlined dense").classes("w-full")
        if tip:
            el.tooltip(tip)
        return el

    if f.widget == "password":
        el = ui.input(
            label=label,
            value=str(current) if current is not None else "",
            password=True,
            password_toggle_button=bool(f.password_toggle),
        ).props("outlined dense").classes("w-full")
        if tip:
            el.tooltip(tip)
        return el

    if f.widget == "textarea":
        el = ui.textarea(
            label=label,
            value=str(current) if current is not None else "",
        ).props(
            f"outlined dense autogrow input-style='font-family: monospace;min-height:{max(f.rows,4)*1.2}em'"
        ).classes("w-full")
        if tip:
            el.tooltip(tip)
        return el

    el = ui.input(
        label=label,
        value="" if current is None else str(current),
        placeholder=f.placeholder,
    )
    props = "outlined dense"
    # 有 placeholder 时追加 stack-label：Quasar 在 outlined + 未 focus 未输入时
    # 默认把 label 压在输入框中间，会把灰色 placeholder 遮住；stack-label 让
    # label 常驻顶部槽位，placeholder 在空值时始终可见 —— 对于「知识库 / 数据
    # 库路径」这种默认值即关键信息的字段尤为重要。
    if f.placeholder:
        props += " stack-label"
    el.props(props).classes("w-full")
    if tip:
        el.tooltip(tip)
    return el


def _reset_form(
    ui,
    fs: "_schema.FileSchema",
    inputs: List[Tuple["_schema.Field", Any]],
    pending_restart: Dict[str, set],
    refresh_banner: Callable[[], None],
) -> None:
    load_res = yaml_store.load_yaml(fs.filename)
    for f, el in inputs:
        current = yaml_store.get_by_path(load_res.doc, f.path)
        if f.widget == "switch":
            el.value = bool(current) if current is not None else False
        elif f.widget == "number":
            el.value = None if current in (None, "") else current
        else:
            el.value = "" if current is None else str(current) if not isinstance(current, (dict, list)) else ""
    pending_restart[fs.filename].clear()
    refresh_banner()
    ui.notify(f"已从 {fs.filename} 重新载入。", color="info")


def _render_page_error(ui, page_name: str, exc: BaseException) -> None:
    """统一的"页面渲染失败"展示器:醒目标题 + 一行原因 + 折叠 traceback。

    用户看到这块就能直接复制完整堆栈反馈,不再需要去翻服务器日志。
    """
    import traceback as _tb

    ui.label(f"{page_name} 页渲染失败").classes(
        "text-negative text-lg font-semibold"
    )
    ui.label(f"{type(exc).__name__}: {exc}").classes(
        "text-sm font-mono text-grey-8"
    ).style("word-break: break-all")
    tb_text = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
    # 默认折叠,避免长 traceback 占满屏幕;但内容齐全方便复制
    with ui.expansion("展开完整 Traceback(复制反馈用)", icon="bug_report").classes(
        "q-mt-sm w-full",
    ):
        ui.html(
            "<pre style='margin:0;font-size:11px;line-height:1.4;"
            "background:#0f172a;color:#e2e8f0;padding:10px 12px;border-radius:6px;"
            "max-height:400px;overflow:auto;white-space:pre-wrap;word-break:break-all'>"
            f"{_html_escape(tb_text)}</pre>"
        )


def _html_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _save_form(
    ui,
    fs: "_schema.FileSchema",
    inputs: List[Tuple["_schema.Field", Any]],
    pending_restart: Dict[str, set],
    refresh_banner: Callable[[], None],
) -> None:
    updates: Dict[str, Any] = {}
    try:
        for f, el in inputs:
            if fs.filename == "basic_settings.yaml" and f.path in _SENSITIVE_BASIC_KEYS:
                continue
            raw = getattr(el, "value", None)
            updates[f.path] = yaml_store.coerce_for_widget(f.widget, raw)
    except ValueError as e:
        ui.notify(f"字段校验失败：{e}", color="negative")
        return

    try:
        path, bak, changes = yaml_store.save_updates(fs.filename, updates)
    except Exception as e:  # noqa: BLE001
        logger.exception("save_updates failed for %s", fs.filename)
        ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
        return

    if not changes:
        ui.notify("没有变更需要保存。", color="info")
        return

    restart_paths = {
        p for p in changes
        for spec in _spec_fields(fs)
        if spec.path == p and spec.needs_restart
    }
    pending_restart[fs.filename].update(restart_paths)
    refresh_banner()

    bak_note = f"；备份：{bak.name}" if bak else ""
    ui.notify(
        f"已保存 {len(changes)} 项到 {path.name}"
        f"（{'需重启' if restart_paths else '即时生效'}）{bak_note}",
        color="positive",
    )


def _spec_fields(fs: "_schema.FileSchema") -> List["_schema.Field"]:
    out: List["_schema.Field"] = []
    for sec in fs.sections:
        out.extend(sec.fields)
    return out


# --- 原始 YAML tab ---

def _render_raw_tab(
    ui,
    fs: "_schema.FileSchema",
    pending_restart: Dict[str, set],
    refresh_banner: Callable[[], None],
) -> None:
    raw_text = yaml_store.load_text(fs.filename)

    with ui.column().classes("w-full q-gutter-sm"):
        ui.label(
            "直接编辑 yaml 原文。保存前做语法校验，保存后自动生成 .bak 备份。"
            "默认认为「原始 YAML」修改都需要重启生效。"
        ).classes("text-sm text-grey-8")

        editor = (
            ui.codemirror(raw_text, language="yaml", theme="basicLight")
            .classes("w-full")
            .style("min-height:480px;border:1px solid #ddd;border-radius:4px")
            if hasattr(ui, "codemirror")
            else ui.textarea(value=raw_text)
            .props("outlined")
            .classes("w-full")
            .style("min-height:480px;font-family:monospace")
        )

        with ui.row().classes("w-full justify-end"):
            ui.button("校验语法", icon="checklist",
                      on_click=lambda: _check_raw(ui, editor)).props("flat")
            ui.button("重新载入", icon="refresh",
                      on_click=lambda: _reload_raw(ui, fs, editor)).props("flat")
            ui.button("保存", icon="save",
                      on_click=lambda: _save_raw(ui, fs, editor, pending_restart, refresh_banner))\
                .props("color=primary")


def _editor_value(editor) -> str:
    v = getattr(editor, "value", None)
    return "" if v is None else str(v)


def _check_raw(ui, editor) -> None:
    ok, err = yaml_store.validate_text(_editor_value(editor))
    ui.notify("YAML 语法正确。" if ok else f"YAML 语法错误：{err}",
              color="positive" if ok else "negative")


def _reload_raw(ui, fs: "_schema.FileSchema", editor) -> None:
    editor.value = yaml_store.load_text(fs.filename)
    ui.notify(f"已重新载入 {fs.filename}。", color="info")


def _save_raw(
    ui,
    fs: "_schema.FileSchema",
    editor,
    pending_restart: Dict[str, set],
    refresh_banner: Callable[[], None],
) -> None:
    text = _editor_value(editor)
    ok, err = yaml_store.validate_text(text)
    if not ok:
        ui.notify(f"拒绝保存：YAML 语法错误：{err}", color="negative")
        return
    try:
        path, bak = yaml_store.save_raw_text(fs.filename, text)
    except Exception as e:  # noqa: BLE001
        logger.exception("save_raw_text failed for %s", fs.filename)
        ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
        return

    pending_restart[fs.filename].add("<raw-yaml>")
    refresh_banner()
    bak_note = f"；备份：{bak.name}" if bak else ""
    ui.notify(f"已保存 {path.name}（原始 YAML，改动默认需要重启）{bak_note}",
              color="positive")


# ---------------------------------------------------------------------------
# ⑧ 服务管理
# ---------------------------------------------------------------------------

def _render_service_page(
    ui,
    pending_restart: Dict[str, set],
    refresh_banner: Callable[[], None],
) -> None:
    from chayuan.server.config_panel.restart import load_runtime, runtime_meta_path

    ui.label("服务管理").classes("text-2xl font-semibold q-mb-sm")
    ui.label(
        "这里可以触发「立即重启」——等价于「停掉当前的 chayuan start，再以相同参数重新拉起」。"
        "如果你修改了需要重启的 yaml 字段，也可以在这里确认后一次性重启。"
    ).classes("text-sm text-grey-8 q-mb-md")

    # 元数据卡
    meta = load_runtime()
    with ui.card().classes("w-full q-mb-md"):
        ui.label("运行时元数据").classes("text-base font-semibold")
        ui.label(str(runtime_meta_path())).classes("text-xs font-mono text-grey-8 q-mb-sm")
        if meta is None:
            ui.label("未找到，重启按钮已禁用。").classes("text-negative")
        else:
            with ui.grid(columns=2).classes("w-full"):
                ui.label(f"父进程 pid：{meta.get('pid')}")
                ui.label(f"记录时间：{meta.get('recorded_at')}")
                ui.label(f"可执行文件：{meta.get('executable')}").classes("col-span-2 text-xs font-mono")
                ui.label(f"命令：{' '.join(meta.get('argv') or [])}").classes("col-span-2 text-xs font-mono")
                ui.label(f"工作目录：{meta.get('cwd')}").classes("col-span-2 text-xs font-mono")

    total = sum(len(v) for v in pending_restart.values())
    with ui.card().classes("w-full"):
        if total > 0:
            ui.label(f"当前有 {total} 项改动需要重启。").classes("text-base font-semibold text-warning")
        else:
            ui.label("当前无待重启改动，可主动触发重启以加载最新配置。").classes("text-base")
        ui.button(
            "立即重启",
            icon="restart_alt",
            on_click=lambda: _on_restart(ui, pending_restart),
        ).props("color=negative" if meta else "disabled")


# ---------------------------------------------------------------------------
# 重启对话框
# ---------------------------------------------------------------------------

def _on_restart(ui, pending_restart: Dict[str, set]) -> None:
    with ui.dialog() as dialog, ui.card():
        ui.label("确定立即重启服务吗？").classes("text-base font-semibold")
        files = ", ".join(f for f, s in pending_restart.items() if s) or "（无，但仍可手动触发）"
        ui.label(f"变更文件：{files}").classes("text-sm text-grey-8")
        ui.label(
            "重启会中断现有 API / 对话 / 面板连接。等待约 10 秒后刷新页面即可。"
        ).classes("text-sm text-grey-8")
        with ui.row().classes("justify-end w-full"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button(
                "确认重启",
                color="negative",
                on_click=lambda: (_do_restart(ui, pending_restart), dialog.close()),
            )
    dialog.open()


def _do_restart(ui, pending_restart: Dict[str, set]) -> None:
    from chayuan.server.config_panel import restart as _restart

    try:
        info = _restart.trigger_restart(delay=1.0)
    except Exception as e:  # noqa: BLE001
        logger.exception("trigger_restart failed")
        ui.notify(f"触发重启失败：{type(e).__name__}: {e}", color="negative")
        return
    for s in pending_restart.values():
        s.clear()
    ui.notify(
        f"已触发重启（目标 pid={info['target_pid']}，守护脚本 pid={info['helper_pid']}）。"
        "请等待约 10 秒后刷新页面。",
        color="positive",
        timeout=8000,
    )
