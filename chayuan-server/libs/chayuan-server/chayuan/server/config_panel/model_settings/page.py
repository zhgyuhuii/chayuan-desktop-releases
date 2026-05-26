"""模型设置页 v2 — 4 tabs 入口(57 题 + 59 题 hotfix)。

关键问题史:
* 旧版 ``render_model_settings_page`` 单页 3196 行同步渲染 → connection lost
* 57 题拆成 4 tab + lazy,但 click handler 同步调 ``state_cache.get_healths()``,
  首次会触发 ``probe_all_frameworks`` (3s deadline),仍然阻塞 socket.io 心跳
* 59 题:click handler **立即** mount 骨架 + spinner,真正渲染推到下一个
  event loop tick,期间用 ``asyncio.to_thread`` 把 prefetch 挪到线程池

时间线:
    t=0    click → mount 骨架 + spinner(<10ms)
    t=50ms ui.timer 触发 → ``_do_render`` 异步执行
    t=50ms  → ``await asyncio.to_thread(prefetch_in_threads, 3.0)``
            主 event loop 让出 → socket.io 心跳保持响应
    t=≤3s  prefetch 完成 → mount tabs + 渲染 runtime tab(cache 命中,瞬时)
    t=≤3s  骨架删除,UI 完整呈现

性能:
* click 响应:<10ms
* socket.io 心跳:整个加载期间不间断
* tab 切换:cache 命中 <100ms
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.config_panel.model_settings.page")

# 2 个 tab 的 value 常量(106 题:删除 ① 运行时与服务 tab,framework cards
# 移到 ▸ 服务配置 页,"配置"按钮跳到 ② 模型厂商 dialog;
# 单机版重构:移除 ③ 模型广场 tab —— 全网模型目录已删除)
TAB_PROVIDERS = "providers"
TAB_DEFAULTS = "defaults"

# 兼容老路径:历史 fragment 可能传 "runtime" / "marketplace",归一化时 fallback 到 PROVIDERS
TAB_RUNTIME = "runtime"  # 已废弃
TAB_MARKETPLACE = "marketplace"  # 已废弃

_VALID_TABS = (TAB_PROVIDERS, TAB_DEFAULTS)


def normalize_initial_tab(value: Optional[str]) -> str:
    """把外部传入的 tab id 归一化为合法的 3 选 1;非法/空/老 'runtime' → PROVIDERS。

    供 ``render_model_settings_v2`` 与 ``dashboard`` 解析 ``file:xx#tab`` 共用。
    """
    if isinstance(value, str) and value in _VALID_TABS:
        return value
    return TAB_PROVIDERS


# ---------------------------------------------------------------------------
# 66 题:tab 脏标记 — 跨 tab 数据更新机制
# ---------------------------------------------------------------------------
# 场景:用户在 ② 模型厂商保存后,④ 默认模型 tab 的下拉应该立刻能看到新厂商。
# 但 ④ 是 lazy 渲染,首次渲染后 rendered=True,page.py 不会再调 _render_defaults。
#
# 解法:任何 _mount_tabs 实例注册自己的 invalidator(closure 内能访问 rendered
# dict + container);保存厂商后调 mark_tab_dirty("defaults"),所有 invalidator
# 把 ④ tab 标脏(rendered=False + container.clear)。下次用户切到 ④ 时,
# page.py 检测到 rendered[④]=False,重新渲染,拿到最新数据。
#
# 模块化:
# - mark_tab_dirty(tab_id) 是 module 级 public API,所有子页面都能调用
# - _TAB_INVALIDATORS 是 List[Callable[[str], None]],每个 _mount_tabs 实例
#   注册一个 local 函数;client 死亡时自动 unregister(NiceGUI 警告防护)
# - 失败的 invalidator 自动从 list 移除(避免无限累积)
_TAB_INVALIDATORS: List[Callable[[str], None]] = []


def mark_tab_dirty(tab_id: str) -> None:
    """标记某个 tab 数据已变,下次切到时强制重新渲染。

    供子页面(如 ② providers_subpage 保存厂商后)调用,通知 ④ defaults / ③
    marketplace 等 tab 重读数据。Tab id 必须是 ``TAB_*`` 常量之一。

    内部:遍历所有已注册的 invalidator;失败的(client 已死)自动移除。
    """
    if tab_id not in _VALID_TABS:
        logger.debug("[mark_tab_dirty] unknown tab_id: %r (ignored)", tab_id)
        return
    for fn in list(_TAB_INVALIDATORS):
        try:
            fn(tab_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("[mark_tab_dirty] invalidator failed: %r → unregister", e)
            try:
                _TAB_INVALIDATORS.remove(fn)
            except ValueError:
                pass


def render_model_settings_v2(
    ui: Any,
    *,
    mark_restart_needed: Optional[Callable[[], None]] = None,
    initial_tab: Optional[str] = None,
) -> None:
    """模型设置主页:4 tabs lazy 渲染 + 异步骨架(防 connection lost)。

    Args:
        ui: NiceGUI ui 模块。
        mark_restart_needed: 保存厂商配置后调用,标记需要重启服务。
        initial_tab: 进入页面时默认激活的 tab(``"runtime"``/``"providers"``/
            ``"marketplace"``/``"defaults"``)。``None`` 或非法值 → ``runtime``。
            供 ``service_config_page`` 的"去配置"按钮跳转用,例如默认 LLM
            卡片直接跳到 ④ 默认模型 tab。

    入口由 ``dashboard.py`` 在用户点击"② 模型配置"时调用,**必须保证同步部分
    <50ms**,否则 socket.io 心跳超时。
    """
    # ----- 步骤 1:click handler 同步部分 — 只 mount 骨架 + spinner -----
    # 在 click handler 返回前,前端已经能看到"加载中"反馈,socket.io 心跳保持
    page_root = ui.column().classes("w-full")
    with page_root:
        skeleton = ui.column().classes("w-full q-pa-md").style("gap: 12px;")
        with skeleton:
            with ui.row().classes("items-center w-full no-wrap").style("gap: 10px;"):
                ui.spinner(size="24px").props("color=primary")
                ui.label("正在加载模型配置...").classes("text-subtitle1 text-grey-8")
            ui.linear_progress(show_value=False).props(
                "indeterminate color=primary"
            ).classes("w-full")
            ui.label(
                "首次加载会异步预热运行时与服务的健康状态(deadline 3 秒),"
                "完成后自动切换到 4 tab 视图。"
            ).classes("text-caption text-grey-6")

        # 真实内容容器 — _do_render 异步填充
        real_root = ui.column().classes("w-full")

    # ----- 步骤 2:把真正的渲染推到下一 event loop tick -----
    async def _do_render() -> None:
        from chayuan.server.config_panel.model_settings import state_cache
        try:
            # 关键:用 asyncio.to_thread 跑同步阻塞 IO(probe_all_frameworks 等),
            # 不阻塞 event loop;socket.io 心跳在此期间仍能响应
            await asyncio.to_thread(state_cache.prefetch_in_threads, 3.0)
        except Exception as e:  # noqa: BLE001
            logger.warning("prefetch_in_threads failed: %s", e)

        # cache 命中,UI mount 瞬时
        try:
            with real_root:
                _mount_tabs(
                    ui,
                    mark_restart_needed=mark_restart_needed,
                    initial_tab=normalize_initial_tab(initial_tab),
                )
        except Exception as e:  # noqa: BLE001
            logger.exception("mount tabs failed: %s", e)
            with real_root:
                ui.label(
                    f"模型配置加载失败:{type(e).__name__}: {e}"
                ).classes("text-negative text-sm font-mono")
                ui.label("(可刷新页面重试,或查看服务日志)").classes(
                    "text-xs text-grey-6"
                )
        finally:
            # 删骨架(用户已看到真实 UI)
            try:
                skeleton.delete()
            except Exception:  # noqa: BLE001
                pass

    # 50ms 后触发 — click handler 已经返回,前端 socket.io 心跳已恢复
    ui.timer(0.05, _do_render, once=True)


def _mount_tabs(
    ui: Any,
    *,
    mark_restart_needed: Optional[Callable[[], None]] = None,
    initial_tab: str = TAB_PROVIDERS,
) -> None:
    """真正 mount 3 tabs + tab_panels + lazy 渲染分派。

    106 题:删除了 ① 运行时与服务 tab。Framework cards 移到 ▸ 服务配置 页,
    "配置"按钮跳到 ② 模型厂商对应 dialog。

    Args:
        initial_tab: 进入页面时立即激活并渲染的 tab(已被
            ``normalize_initial_tab`` 归一化保证合法)。
    """
    # ----- Tabs 顶栏(单机版重构: 2 tabs, 移除"③ 模型广场" tab)-----
    with ui.tabs().classes("w-full").props("dense") as tabs:
        ui.tab(TAB_PROVIDERS, label="① 模型厂商", icon="storefront")
        ui.tab(TAB_DEFAULTS, label="② 默认模型", icon="tune")

    # 跟踪每个 tab 是否已渲染过 — 切到时才 render,避免重复
    rendered: Dict[str, bool] = {
        TAB_PROVIDERS: False,
        TAB_DEFAULTS: False,
    }

    # ----- 2 个 tab_panels(顺序与上面 ui.tab 一致:厂商 / 默认模型)-----
    with ui.tab_panels(tabs, value=initial_tab).classes("w-full").props("animated"):
        with ui.tab_panel(TAB_PROVIDERS):
            providers_container = ui.column().classes("w-full")
        with ui.tab_panel(TAB_DEFAULTS):
            defaults_container = ui.column().classes("w-full")

    # ----- Lazy 渲染分派(98 题模式)-----
    from chayuan.server.config_panel.model_settings._async_mount import (
        lazy_async_render,
    )

    def _render_providers() -> None:
        if rendered[TAB_PROVIDERS]:
            return
        rendered[TAB_PROVIDERS] = True

        def _do() -> None:
            from chayuan.server.config_panel.model_settings.providers_subpage import (
                render_providers_subpage,
            )
            render_providers_subpage(ui, mark_restart_needed=mark_restart_needed)
        lazy_async_render(ui, providers_container, "② 模型厂商", _do)

    def _render_defaults() -> None:
        if rendered[TAB_DEFAULTS]:
            return
        rendered[TAB_DEFAULTS] = True

        def _do() -> None:
            from chayuan.server.config_panel.model_settings.defaults_subpage import (
                render_defaults_subpage,
            )
            render_defaults_subpage(ui)
        # 107 题:默认模型放到第二位(原第四位)
        lazy_async_render(ui, defaults_container, "② 默认模型", _do)

    _DISPATCH: Dict[str, Callable[[], None]] = {
        TAB_PROVIDERS: _render_providers,
        TAB_DEFAULTS: _render_defaults,
    }

    # 容器表 — invalidator 清空容器时用
    _CONTAINERS: Dict[str, Any] = {
        TAB_PROVIDERS: providers_container,
        TAB_DEFAULTS: defaults_container,
    }

    # 66 题:本 _mount_tabs 实例的 local invalidator,注册到 module 级 list
    # mark_tab_dirty(tab_id) 会调用所有已注册的 invalidator
    def _local_invalidator(tab_id: str) -> None:
        """把 tab_id 标脏:rendered=False + container.clear()。

        client 已死时 container.clear() 会抛(_safe_ui 已 patch);
        外部 mark_tab_dirty 兜一层 try/except 自动 unregister 本函数。
        """
        from chayuan.server.config_panel._safe_ui import is_client_alive
        cont = _CONTAINERS.get(tab_id)
        # client 死亡 → 抛异常让 mark_tab_dirty unregister 自己
        if cont is not None and not is_client_alive(cont):
            raise RuntimeError("client dead, unregister this invalidator")
        if tab_id in rendered:
            rendered[tab_id] = False
        if cont is not None:
            try:
                cont.clear()
            except Exception:  # noqa: BLE001
                # 清不了就让 mark_tab_dirty 兜底 unregister
                raise

    _TAB_INVALIDATORS.append(_local_invalidator)

    # 立即渲染 initial_tab(此时 state_cache 已暖,probe 命中,瞬时)
    initial_render = _DISPATCH.get(initial_tab, _render_providers)
    initial_render()

    # 切 tab 时按需 render(rendered=False 会重新触发 — 配合 mark_tab_dirty)
    def _on_tab_change(e: Any) -> None:
        v = getattr(e, "args", None) or getattr(e, "value", None)
        if not isinstance(v, str):
            return
        fn = _DISPATCH.get(v)
        if fn:
            fn()

    try:
        tabs.on("update:model-value", _on_tab_change)
    except Exception:  # noqa: BLE001
        try:
            tabs.on_change(_on_tab_change)
        except Exception:  # noqa: BLE001
            logger.warning("绑定 tab change 失败,切 tab 不会触发 lazy 渲染")


__all__ = [
    "render_model_settings_v2",
    "normalize_initial_tab",
    "mark_tab_dirty",
    "TAB_RUNTIME", "TAB_PROVIDERS", "TAB_MARKETPLACE", "TAB_DEFAULTS",
]
