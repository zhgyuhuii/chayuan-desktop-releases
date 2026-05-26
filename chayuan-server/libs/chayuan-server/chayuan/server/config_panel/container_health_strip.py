"""配置面板顶部:容器健康状态条(Phase 6 — 42 题增量)。

设计目标
========
让用户在配置面板任意页面都能一眼看到 4 个 docker service 的实时健康状态,
不必切到"运行时与服务"页:
  ┌─────────────────────────────────────────────────────────────┐
  │  ⬤ vllm  ⬤ infinity  ⊙ comfyui  ⬤ llamacpp                 │
  └─────────────────────────────────────────────────────────────┘
   绿=healthy   蓝=running    黄=starting   红=unhealthy   灰=missing

实现要点
========
* 用 :class:`ContainerLifecycle.health_many`(全 async)并发查所有 service
* 10 秒周期刷新 — 不会给 docker daemon 压力(每次 health 调用 ≤ 200ms)
* 至少 1 个容器 ``healthy`` / ``running`` 才显示;全 missing 时整条隐藏
* 点击 chip → 跳转到"模型配置"页(若 navigate_to 注入了)
* 用 ``safe_timer_cb`` 防 client deleted 警告

不阻塞 / 防并发
================
* ``in_flight`` 标志避免上一次 probe 没回来又起新的
* 任何 exception 都吞掉,只记 debug log
* 单独一个 ``asyncio.create_task`` 跑 probe,不阻塞 timer 主线程
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.container_health_strip")


# 5 个 docker service 的展示名 + 对应路由 key
# 与 compose_manager.COMPOSE_MANAGED_FRAMEWORKS 保持一致
_DISPLAY_NAMES: Dict[str, str] = {
    "vllm": "vLLM",
    "infinity": "Infinity",
    "comfyui": "ComfyUI",
    "llamacpp": "llama.cpp",
}

# 状态 → (颜色, 图标) — Quasar 调色板友好
_STATE_STYLE: Dict[str, Dict[str, str]] = {
    "healthy":   {"color": "#10b981", "bg": "#d1fae5", "border": "#6ee7b7", "icon": "check_circle"},
    "running":   {"color": "#3b82f6", "bg": "#dbeafe", "border": "#93c5fd", "icon": "circle"},
    "starting":  {"color": "#f59e0b", "bg": "#fef3c7", "border": "#fcd34d", "icon": "hourglass_empty"},
    "unhealthy": {"color": "#dc2626", "bg": "#fee2e2", "border": "#fca5a5", "icon": "error"},
    "exited":    {"color": "#9ca3af", "bg": "#f3f4f6", "border": "#d1d5db", "icon": "stop_circle"},
    "created":   {"color": "#9ca3af", "bg": "#f3f4f6", "border": "#d1d5db", "icon": "circle_outline"},
    "missing":   {"color": "#9ca3af", "bg": "#f9fafb", "border": "#e5e7eb", "icon": "remove_circle_outline"},
}


def render_container_health_strip(
    ui: Any,
    *,
    interval_seconds: float = 10.0,
    navigate_to: Optional[Callable[[str], None]] = None,
) -> Callable[[], None]:
    """渲染状态条并返回 ``refresh()`` 让外部强制刷新。

    Args:
        ui: NiceGUI 命名空间
        interval_seconds: 自动刷新周期(默认 10s — 平衡及时性 vs daemon 压力)
        navigate_to: 可选;chip 点击时调 ``navigate_to("file:model_settings.yaml")``

    Returns:
        ``refresh()`` callable — 给"启动 / 停止"等手动操作后立即重新刷新
    """
    services: List[str] = list(_DISPLAY_NAMES.keys())

    container = ui.row().classes("items-center w-full").style(
        "padding: 6px 12px; gap: 8px; "
        "background: #fafbfc; border-bottom: 1px solid #e5e7eb; "
        "min-height: 36px; flex-wrap: wrap;"
    )

    # mount 一组占位 chip(初始全 missing 灰色),probe 回来后再 patch 状态
    chips: Dict[str, Any] = {}
    with container:
        ui.label("容器:").style(
            "color: #6b7280; font-size: 11px; font-weight: 500;"
        )
        for svc in services:
            display = _DISPLAY_NAMES[svc]
            chip = _make_chip(ui, svc, display, "missing", navigate_to)
            chips[svc] = chip

        # 右侧 "全部 healthy / N/M running" 摘要
        summary_label = ui.label("加载中...").style(
            "margin-left: auto; color: #6b7280; font-size: 11px;"
        )

    state_ctx: Dict[str, Any] = {
        "in_flight": False,
        "row": container,
        "chips": chips,
        "summary": summary_label,
    }

    async def _do_probe() -> None:
        if state_ctx["in_flight"]:
            return
        state_ctx["in_flight"] = True
        try:
            from chayuan.server.config_panel.container_lifecycle import (
                get_container_lifecycle, HealthState,
            )
            lc = get_container_lifecycle()
            try:
                results = await lc.health_many(services)
            except Exception as e:  # noqa: BLE001
                logger.debug("[health-strip] health_many failed: %r", e)
                return

            ready_count = 0
            running_count = 0
            for svc, h in results.items():
                state = h.state.value if hasattr(h.state, "value") else str(h.state)
                if state == "healthy":
                    ready_count += 1
                if state in ("healthy", "running"):
                    running_count += 1
                # 更新 chip 状态
                chip = state_ctx["chips"].get(svc)
                if chip is not None:
                    _update_chip(chip, svc, _DISPLAY_NAMES[svc], state)

            # 更新 summary
            try:
                if running_count == 0:
                    state_ctx["summary"].set_text("(无运行中容器)")
                else:
                    state_ctx["summary"].set_text(
                        f"{running_count}/{len(services)} 运行中 · {ready_count} healthy"
                    )
            except Exception:  # noqa: BLE001
                pass

            # 全 missing 时隐藏整条 — 用户没用 docker 类 service
            try:
                if running_count == 0 and ready_count == 0:
                    # 只在所有 chip 都 missing 时才隐藏
                    all_missing = all(
                        h.state.value == "missing" if hasattr(h.state, "value")
                        else str(h.state) == "missing"
                        for h in results.values()
                    )
                    state_ctx["row"].style(
                        "display: none" if all_missing else "display: flex",
                    )
            except Exception:  # noqa: BLE001
                pass
        finally:
            state_ctx["in_flight"] = False

    def _refresh_async() -> None:
        try:
            asyncio.create_task(_do_probe())
        except Exception as e:  # noqa: BLE001
            logger.debug("[health-strip] schedule refresh failed: %r", e)

    # 启动时立即探一次
    _refresh_async()

    # 周期刷新
    try:
        from chayuan.server.config_panel._safe_ui import safe_timer_cb
        ui.timer(
            interval_seconds,
            safe_timer_cb(_refresh_async),
        )
    except Exception:  # noqa: BLE001
        # _safe_ui 不可用就直接挂 timer
        try:
            ui.timer(interval_seconds, _refresh_async)
        except Exception:  # noqa: BLE001
            pass

    return _refresh_async


# ============================================================================
# 内部 helper
# ============================================================================


def _make_chip(
    ui: Any,
    service: str,
    display: str,
    state: str,
    navigate_to: Optional[Callable[[str], None]],
) -> Any:
    """创建一个 chip,初始状态 = state。返回 chip 元素 ref(用于后续 _update_chip)。"""
    style = _STATE_STYLE.get(state, _STATE_STYLE["missing"])

    chip_props = "dense outline" if state == "missing" else "dense"
    handler = None
    if navigate_to is not None:
        # 点击 chip 跳到模型配置页(那里能看到详细)
        handler = lambda _e=None: _safe_call(navigate_to, "file:model_settings.yaml")

    chip = ui.chip(
        display,
        icon=style["icon"],
    ).props(chip_props).style(
        f"color: {style['color']}; "
        f"background: {style['bg']}; "
        f"border: 1px solid {style['border']}; "
        f"font-size: 11px;"
    )
    if handler is not None:
        chip.on("click", handler)
    # 把 service / 当前 state 挂到 chip,后续 _update_chip 用
    try:
        chip._chayuan_service = service
        chip._chayuan_state = state
    except Exception:  # noqa: BLE001
        pass
    return chip


def _update_chip(chip: Any, service: str, display: str, state: str) -> None:
    """patch chip 的样式 + 文字,反映新状态。"""
    style = _STATE_STYLE.get(state, _STATE_STYLE["missing"])
    try:
        # NiceGUI chip 的文字通过 .set_text(...) 改;但 chip 顶层文字是
        # 在 props 里的 ``label``,改 props 即可
        chip.props(f'label="{display}" icon="{style["icon"]}"')
        chip.style(
            f"color: {style['color']}; "
            f"background: {style['bg']}; "
            f"border: 1px solid {style['border']}; "
            f"font-size: 11px;",
            replace="color background border font-size",
        )
        chip._chayuan_state = state  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _safe_call(fn: Callable, *args: Any) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        pass


__all__ = ["render_container_health_strip"]
