"""GuidanceCard —— NiceGUI 端三段式 What / Why / How 引导卡片。

与 chayuan-client 端 ``packages/ui/src/components/GuidanceCard.tsx`` 是组件孪
生:三段固定语义、平台 Tab、"复制命令"按钮、design token 同源(``--cy-warning-*``
等通过 NiceGUI ``ui.add_head_html`` 已在主入口注入)。

为什么不直接嵌入 React?
* NiceGUI 是独立人格的 server admin 面板,不应依赖前端构建产物。
* 共享文案 / 数据契约即可保持视觉一致;component twin 而非 webview。

用法::

    from chayuan.server.config_panel._guidance_card import (
        GuidancePlatformBlock,
        GuidanceStep,
        render_guidance_card,
    )

    render_guidance_card(
        ui,
        tone="warning",
        what="启动失败:macOS Gatekeeper 拦截",
        why="因为安装包尚未公证 (notarization),首次启动会被拦截。",
        how=[
            GuidancePlatformBlock(
                platform="macos",
                steps=[
                    GuidanceStep(text="在 Finder 中右键点击 chayuan.app → 打开"),
                    GuidanceStep(
                        text="或在终端解除隔离属性",
                        command="xattr -dr com.apple.quarantine /Applications/chayuan.app",
                    ),
                ],
            ),
        ],
    )
"""
from __future__ import annotations

import platform as _platform
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Literal, Optional

logger = logging.getLogger("chayuan.config_panel._guidance_card")


GuidanceTone = Literal["warning", "info", "success", "danger"]
GuidancePlatform = Literal["macos", "windows", "linux", "all"]


@dataclass
class GuidanceStep:
    text: str
    command: Optional[str] = None


@dataclass
class GuidancePlatformBlock:
    platform: GuidancePlatform
    steps: List[GuidanceStep]
    label: Optional[str] = None  # 默认按 platform 取
    doc_href: Optional[str] = None


_TONE_TOKENS = {
    "warning": {
        "bg": "var(--cy-warning-50, #FFF7ED)",
        "border": "var(--cy-warning-500, #F97316)",
        "icon": "warning",
        "icon_color": "var(--cy-warning-600, #EA580C)",
        "title_color": "var(--cy-warning-700, #C2410C)",
    },
    "info": {
        "bg": "var(--cy-info-50, #EFF6FF)",
        "border": "var(--cy-info-500, #3B82F6)",
        "icon": "info",
        "icon_color": "var(--cy-info-600, #2563EB)",
        "title_color": "var(--cy-info-700, #1D4ED8)",
    },
    "success": {
        "bg": "var(--cy-success-50, #ECFDF5)",
        "border": "var(--cy-success-500, #10B981)",
        "icon": "check_circle",
        "icon_color": "var(--cy-success-600, #059669)",
        "title_color": "var(--cy-success-700, #047857)",
    },
    "danger": {
        "bg": "var(--cy-danger-50, #FEF2F2)",
        "border": "var(--cy-danger-500, #EF4444)",
        "icon": "error",
        "icon_color": "var(--cy-danger-600, #DC2626)",
        "title_color": "var(--cy-danger-700, #B91C1C)",
    },
}

_PLATFORM_LABELS = {"macos": "macOS", "windows": "Windows", "linux": "Linux", "all": "通用"}


def _detect_server_platform() -> GuidancePlatform:
    sys_name = _platform.system().lower()
    if "darwin" in sys_name:
        return "macos"
    if "windows" in sys_name:
        return "windows"
    if "linux" in sys_name:
        return "linux"
    return "all"


def render_guidance_card(
    ui: Any,
    *,
    tone: GuidanceTone = "warning",
    what: str,
    why: Optional[str] = None,
    how: List[GuidancePlatformBlock],
    action_label: Optional[str] = None,
    on_action: Optional[Callable[[], Any]] = None,
) -> None:
    """渲染一张引导卡片到当前 NiceGUI 上下文。

    本函数无副作用(除了 ``ui.notify`` 复制成功提示)。所有跳转 / 安装动作
    交给 ``on_action`` 调用方。
    """
    tokens = _TONE_TOKENS.get(tone, _TONE_TOKENS["warning"])
    server_platform = _detect_server_platform()
    # 按 server 平台优先排序;再按 all 兜底
    initial = next((b for b in how if b.platform == server_platform), how[0] if how else None)

    card_style = (
        f"background: {tokens['bg']};"
        f"border-radius: 12px;"
        f"border: 1px solid var(--cy-border-subtle, #E4E4E7);"
        f"border-left: 4px solid {tokens['border']};"
        f"padding: 16px;"
        f"box-shadow: var(--cy-shadow-sm, 0 1px 2px 0 rgba(0,0,0,0.04));"
    )
    with ui.card().classes("w-full q-mb-sm").style(card_style):
        # ---- 行 1: 图标 + What/Why + 右上角 actionLabel ----
        with ui.row().classes("w-full no-wrap items-start gap-3"):
            ui.icon(tokens["icon"]).style(
                f"color: {tokens['icon_color']}; font-size: 22px; margin-top: 2px;"
            )
            with ui.column().classes("flex-1 min-w-0 gap-1"):
                ui.label(what).style(
                    f"color: {tokens['title_color']}; font-size: 14px; font-weight: 600; line-height: 1.35;"
                )
                if why:
                    ui.label(why).style(
                        "color: var(--cy-text-secondary, #52525B);"
                        " font-size: 12px; line-height: 1.5;"
                    )
            if action_label and on_action:
                ui.button(action_label, on_click=on_action).props(
                    'unelevated dense color=dark text-color=white'
                ).style("border-radius: 8px; font-size: 12px; padding: 6px 12px;")

        # ---- 行 2: 平台 Tab + 步骤列表 ----
        if not how:
            return
        # NiceGUI tabs 需 ``ui.tabs()`` + ``ui.tab_panels()``;但它们要求每 tab
        # 一个 panel。我们的 panel 数动态,因此手写 toggle + 内容切换更轻。
        active_state = {"platform": initial.platform if initial else "all"}
        # 容器: 切换内容时清空再渲染
        steps_container = ui.column().classes("w-full gap-2 q-mt-sm")

        def _render_block(block: GuidancePlatformBlock) -> None:
            steps_container.clear()
            with steps_container:
                for i, step in enumerate(block.steps, start=1):
                    with ui.row().classes("w-full no-wrap items-start gap-2"):
                        ui.label(str(i)).style(
                            "background: var(--cy-surface-base, #FFF);"
                            " color: var(--cy-text-secondary, #52525B);"
                            " width: 16px; height: 16px; border-radius: 9999px;"
                            " font-size: 10px; font-weight: 600;"
                            " display: flex; align-items: center; justify-content: center;"
                            " flex-shrink: 0; margin-top: 2px;"
                        )
                        with ui.column().classes("flex-1 min-w-0 gap-1"):
                            ui.label(step.text).style(
                                "color: var(--cy-text-primary, #0A0A0A);"
                                " font-size: 12px; line-height: 1.5;"
                            )
                            if step.command:
                                _render_command_row(ui, step.command)
                if block.doc_href:
                    ui.html(
                        f'<a href="{block.doc_href}" target="_blank" rel="noopener noreferrer"'
                        f' style="color: var(--cy-brand-600, #2563EB); font-size: 12px;'
                        f' text-decoration: none;">了解更多 →</a>'
                    )

        # 平台 toggle
        if len(how) > 1:
            with ui.row().classes("q-mt-sm gap-1").style(
                "background: var(--cy-surface-2, #F4F4F6);"
                " border-radius: 9999px; padding: 2px; display: inline-flex;"
            ):
                tab_buttons: List[Any] = []
                for block in how:
                    label = block.label or _PLATFORM_LABELS.get(block.platform, block.platform)
                    is_active = block.platform == active_state["platform"]
                    btn = ui.button(label).props(
                        f'flat dense {"" if is_active else ""}'
                    )
                    btn.style(
                        f"border-radius: 9999px; padding: 4px 12px; font-size: 12px;"
                        f" font-weight: 500; min-height: 24px;"
                        f" background: {'var(--cy-surface-base, #FFF)' if is_active else 'transparent'};"
                        f" color: {'var(--cy-text-primary, #0A0A0A)' if is_active else 'var(--cy-text-secondary, #52525B)'};"
                    )
                    tab_buttons.append((block, btn))

                def _make_handler(target: GuidancePlatformBlock):
                    def _handler() -> None:
                        active_state["platform"] = target.platform
                        # 重新渲染所有 tab 按钮样式
                        for blk, btn_w in tab_buttons:
                            btn_w.style(
                                f"border-radius: 9999px; padding: 4px 12px; font-size: 12px;"
                                f" font-weight: 500; min-height: 24px;"
                                f" background: {'var(--cy-surface-base, #FFF)' if blk.platform == target.platform else 'transparent'};"
                                f" color: {'var(--cy-text-primary, #0A0A0A)' if blk.platform == target.platform else 'var(--cy-text-secondary, #52525B)'};"
                            )
                        _render_block(target)
                    return _handler

                for block, btn in tab_buttons:
                    btn.on("click", _make_handler(block))

        if initial is not None:
            _render_block(initial)


def _render_command_row(ui: Any, command: str) -> None:
    """命令行 + 复制按钮内嵌行。"""
    with ui.row().classes("w-full no-wrap items-center gap-2 q-mt-xs").style(
        "background: var(--cy-surface-base, #FFF);"
        " border-radius: 4px; padding: 6px 8px;"
    ):
        ui.label(command).style(
            "font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;"
            " font-size: 11px; color: var(--cy-text-primary, #0A0A0A);"
            " flex: 1; min-width: 0; overflow-x: auto; white-space: pre;"
        )

        def _copy() -> None:
            try:
                # NiceGUI 透过 JS 写剪贴板;下行在所有现代浏览器可用
                ui.run_javascript(
                    f"navigator.clipboard.writeText({command!r})"
                )
                ui.notify("已复制命令", type="positive", position="bottom")
            except Exception as e:  # noqa: BLE001
                # 客户端已断开时 ui.run_javascript 会触发警告;静默 debug
                logger.debug("guidance_card copy skipped: %s", e)
                try:
                    ui.notify("复制失败", type="negative", position="bottom")
                except Exception:  # noqa: BLE001
                    pass

        ui.button(icon="content_copy", on_click=_copy).props(
            'flat dense round size=sm'
        ).style("color: var(--cy-text-tertiary, #A1A1AA);")


__all__ = [
    "GuidanceTone",
    "GuidancePlatform",
    "GuidanceStep",
    "GuidancePlatformBlock",
    "render_guidance_card",
]
