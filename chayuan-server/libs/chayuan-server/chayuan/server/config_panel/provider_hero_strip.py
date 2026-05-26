"""模型设置页 · 横向卡片 strip + 更多对话框(同时给"模型厂商"和"本地模型"两行复用)。

历史动机（用户 2026-05-02 反馈）:

> "在模型设置页面 第三行横向展示8个模型供应商 不再展示ollama等本地模型服务了
>  因为上面已经有ollama等本地模型配置了 ... 这8个横向卡片 如果已经配置了
>  且启用了 那么有个图标显示可用状态  8个卡片的最后面右下角有个更多 点击
>  更多后展示所有供应商 可以搜索 可以添加 点击具体的供应商可以配置"

后续(2026-05-05)用户要求新增"本地模型"行,与"模型厂商"行**完全同源**渲染。
本模块抽出 ``kind`` 参数:

* ``kind="cloud"``  = 模型厂商行(过滤掉 tags 含"本地")
* ``kind="local"``  = 本地模型行(只保留 tags 含"本地")

排序、卡片样式、"更多"对话框、自定义条目入口、刷新钩子完全复用。

模块化要点
---------

*  本文件**不依赖**具体平台实现，只是渲染层；调用方提供：
    - ``providers``：``List[ProviderMeta]``（来源 ``model_config.PROVIDER_CATALOG``）
    - ``state_of(pid)``：返回 ``(enabled, configured, model_count)`` 三元组
    - ``on_pick(pid)``：用户点了某张卡或"更多→列表里"挑选了一个时回调；
       调用方把 ``selection["pid"] = pid`` 然后刷新右栏即可。
    - ``on_add_custom()``：用户在"更多"对话框点了"添加自定义" 按钮；
       调用方打开自己的"新增服务商"对话框。

* 排序(cloud)：**已启用 → 推荐 → 国外主流 → 其余**;本地厂商被剔除。
* 排序(local)：**已启用 → 推荐 → 其余**;只显示本地厂商。

* 性能：8 张卡片直接 DOM；"更多"对话框里搜索是客户端 ``ui.input`` +
   重渲染 ``ui.column``，O(N) 但 N≈60，肉眼瞬时。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.config_panel.provider_hero_strip")

# kind 字符串常量 — 避免拼写错误,IDE 也能跳转
KIND_CLOUD = "cloud"
KIND_LOCAL = "local"


@dataclass
class ProviderRowState:
    """渲染时需要的厂商状态——抽出 ProviderMeta 的依赖，方便单测。"""
    pid: str
    display_name: str
    color: str
    tags: Tuple[str, ...]
    apply_key_url: str
    enabled: bool
    configured: bool      # 至少一项 api_key 被设置过
    model_count: int


def _is_local_provider(p: Any) -> bool:
    """tags 含"本地"即为本地厂商(可与其他 tag 共存)。"""
    tags = tuple(getattr(p, "tags", ()) or ())
    return "本地" in tags


def _provider_passes_kind(p: Any, kind: str) -> bool:
    """``kind="cloud"`` 排除本地;``kind="local"`` 仅保留本地。"""
    if kind == KIND_LOCAL:
        return _is_local_provider(p)
    # 默认 cloud — 兼容老调用方(没传 kind)
    return not _is_local_provider(p)


def select_top_providers(
    providers: List[Any],
    state_lookup: Callable[[str], Tuple[bool, bool, int]],
    *,
    limit: int = 8,
    kind: str = KIND_CLOUD,
) -> List[ProviderRowState]:
    """挑选首屏要展示的 ``limit`` 个厂商。

    Args:
        kind: ``"cloud"`` 排除本地厂商(默认);``"local"`` 仅保留本地厂商。

    优先级（同档按 catalog 原顺序）：
      1. ``enabled=True`` 的厂商(无论 configured) — **永远不会因为份额不足被裁掉**
      2. ``configured=True``(已填 api_key) 但未启用
      3. tags 含"推荐"
      4. (cloud only) tags 含"国外"主流
      5. 其余

    Returns:
        长度 ≤ limit 的列表；调用方据此渲染卡片。
    """
    rows: List[ProviderRowState] = []
    for p in providers:
        if not _provider_passes_kind(p, kind):
            continue
        enabled, configured, n = state_lookup(p.pid)
        rows.append(ProviderRowState(
            pid=p.pid,
            display_name=p.display_name,
            color=getattr(p, "color", "#6b7280") or "#6b7280",
            tags=tuple(getattr(p, "tags", ()) or ()),
            apply_key_url=getattr(p, "apply_key_url", "") or "",
            enabled=enabled,
            configured=configured,
            model_count=n,
        ))

    def _bucket(r: ProviderRowState) -> int:
        # 启用 → 最优先(无论 configured;对齐 docstring "永远不会因为份额不足被裁掉")
        # 历史规则要求 enabled+configured 才进 0,导致只 enable 但未 fetch_models 拉到
        # 模型清单的厂商被挤到末档,与"已启用永远优先"语义不符
        if r.enabled:
            return 0
        # 已配置 (填了 api_key) 但开关关闭 → 次优先
        if r.configured:
            return 1
        # 推荐 → 第三档 (catalog 推荐, 让用户先看到主流选项)
        if "推荐" in r.tags:
            return 2
        # cloud 行额外把"国外主流"提前;local 行没有此分档
        if kind == KIND_CLOUD and "国外" in r.tags:
            return 3
        return 4

    rows.sort(key=lambda r: (_bucket(r), r.pid))
    return rows[:limit]


# 按 (where, kind) 二元 key 隔离;where ∈ {"strip","more_dialog"};kind ∈ {"cloud","local"}
# 这样 cloud strip / local strip / cloud more dialog / local more dialog 各自独立
_REFRESHERS: Dict[Tuple[str, str], List[Callable[[], None]]] = {
    ("strip", KIND_CLOUD): [],
    ("strip", KIND_LOCAL): [],
    ("more_dialog", KIND_CLOUD): [],
    ("more_dialog", KIND_LOCAL): [],
}


def _trigger(where: str, kind: Optional[str] = None) -> None:
    """触发指定 where(+ kind) 的所有已注册刷新钩子。kind=None 表示两 kind 都触发。"""
    keys = (
        [(where, kind)] if kind in (KIND_CLOUD, KIND_LOCAL)
        else [(where, KIND_CLOUD), (where, KIND_LOCAL)]
    )
    for k in keys:
        for fn in list(_REFRESHERS.get(k, [])):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass


def trigger_hero_strip_refresh(kind: Optional[str] = None) -> None:
    """外部(如 model_config._save_all 保存厂商配置后)调用,刷新顶层卡片。

    Args:
        kind: ``None`` 表示两 strip 都刷;指定 ``"cloud"`` / ``"local"`` 则只刷一行。
    """
    _trigger("strip", kind)


def trigger_more_dialog_refresh(kind: Optional[str] = None) -> None:
    """刷新当前打开的"全部模型厂商"弹窗里的卡片(如果有的话)。

    场景:用户在 more dialog 选了 deepseek 弹出配置 dialog → 配置完保存关闭 →
    回到 more dialog 时,deepseek 卡片应即时显示"已启用"绿勾。
    """
    _trigger("more_dialog", kind)


def render_provider_hero_strip(
    ui: Any,
    *,
    providers: List[Any],
    state_lookup: Callable[[str], Tuple[bool, bool, int]],
    on_pick: Callable[[str], None],
    on_add_custom: Optional[Callable[[], None]] = None,
    limit: int = 8,
    kind: str = KIND_CLOUD,
    title: Optional[str] = None,
    icon: Optional[str] = None,
    hint_template: Optional[str] = None,
    more_dialog_title: Optional[str] = None,
    more_dialog_hint: Optional[str] = None,
) -> Callable[[], None]:
    """渲染一行 ``limit`` 张厂商/本地卡片 + "更多"按钮。返回 ``refresh()`` 钩子。

    Args:
        kind: ``"cloud"`` / ``"local"``;决定过滤 + 默认文案。
        title: 行标题;缺省按 kind 给"模型厂商" / "本地模型"。
        icon: 行标题左侧 Material icon;缺省按 kind 给 ``storefront`` / ``computer``。
        hint_template: 行标题右侧灰色提示;支持 ``{n}`` ``{enabled_n}`` 占位符。
        more_dialog_title: "更多"对话框标题。
        more_dialog_hint: "更多"对话框副标题。
    """
    # 按 kind 设默认文案 — 调用方传入 title 等会覆盖
    if title is None:
        title = "本地模型" if kind == KIND_LOCAL else "模型厂商"
    if icon is None:
        icon = "computer" if kind == KIND_LOCAL else "storefront"
    if hint_template is None:
        hint_template = (
            "展示 {n} 家 · 已启用 {enabled_n} · 本地推理服务器(OpenAI 兼容)"
            if kind == KIND_LOCAL
            else "展示 {n} 家 · 已启用 {enabled_n} · "
                 "本地框架已在第一行管理，此处只列云端 / 第三方"
        )
    if more_dialog_title is None:
        more_dialog_title = "全部本地模型服务" if kind == KIND_LOCAL else "全部模型厂商"
    if more_dialog_hint is None:
        more_dialog_hint = (
            "下面列出 catalog 内 **所有本地** 推理服务;点卡片切到该服务配置;"
            "点右上『添加自定义』 创建新条目。"
            if kind == KIND_LOCAL
            else "下面列出 catalog 内 **所有非本地** 厂商；点卡片切到该厂商配置；"
                 "点右上『添加自定义』 创建新条目。"
        )

    container = ui.column().classes("w-full q-mb-sm").style("gap: 6px;")

    def _refresh() -> None:
        # 62 题:client 已死 → silent return + self-remove(防保存厂商后
        # _do_cascade_refresh 操作旧 client,触发 NiceGUI "Client has been
        # deleted" 警告,事件循环 race 让后续卡片点击失效)
        from chayuan.server.config_panel._safe_ui import is_client_alive
        if not is_client_alive(container):
            try:
                _REFRESHERS[("strip", kind)].remove(_refresh)
            except (ValueError, KeyError):
                pass
            return
        rows = select_top_providers(providers, state_lookup, limit=limit, kind=kind)
        container.clear()
        with container:
            with ui.card().classes("w-full").props("flat bordered").style(
                "background: #fafbfc; padding: 10px 12px;"
            ):
                with ui.row().classes("items-center w-full no-wrap").style("gap: 10px;"):
                    ui.icon(icon, size="18px").classes("text-grey-7")
                    ui.label(title).classes("text-subtitle2")
                    enabled_n = sum(1 for r in rows if r.enabled)
                    ui.label(
                        hint_template.format(n=len(rows), enabled_n=enabled_n)
                    ).classes("text-caption text-grey-6")
                    ui.space()
                    # "更多" 在 "刷新" 之前 — 顺序: ... [更多] [刷新]
                    # 用户更频繁需要的是"看全部 / 添加自定义",刷新是次要操作
                    ui.button(
                        "更多", icon="apps",
                        on_click=lambda _e=None: _open_more_dialog(),
                    ).props("dense flat color=primary size=sm").tooltip(
                        "查看 / 搜索 / 添加全部厂商"
                    )
                    ui.button(
                        "刷新", icon="refresh", on_click=lambda: _refresh(),
                    ).props("dense flat color=primary size=sm")

                # 8 张厂商卡片整齐排布 (4 列 wrap;8 张正好 2 行)
                with ui.row().classes("w-full q-mt-sm").style(
                    "gap: 8px; flex-wrap: wrap;"
                ):
                    for r in rows:
                        _render_card(r)

    def _render_card(r: ProviderRowState) -> None:
        # 卡片 = 1/4 列宽（每行 4 张，第二行剩下 4 张+更多）
        with ui.card().props("flat bordered").style(
            "flex: 0 0 calc((100% - 32px) / 4); min-width: 220px; "
            "padding: 10px 12px; cursor: pointer; "
            f"border-color: {'#a7f3d0' if r.enabled else '#e5e7eb'}; "
            f"background: {'#f0fdf4' if r.enabled else 'white'};"
        ).on("click", lambda _e=None, pid=r.pid: on_pick(pid)):
            with ui.row().classes("items-center no-wrap w-full").style("gap: 8px;"):
                # logo 占位（大圆 + 首字母）
                first = r.display_name[:1] if r.display_name else "?"
                ui.html(
                    f'<div style="width:28px;height:28px;border-radius:6px;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'background:{r.color};color:white;font-weight:600;'
                    f'font-size:13px;flex:0 0 auto;">{first}</div>'
                )
                ui.label(r.display_name).style(
                    "font-weight: 600; font-size: 13px; flex: 1 1 auto; "
                    "white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
                )
                # 状态徽标
                if r.enabled:
                    ui.icon("check_circle", size="18px").classes("text-positive").tooltip(
                        f"已启用 · {r.model_count} 个模型"
                    )
                elif r.configured:
                    ui.icon("settings", size="16px").classes("text-grey-6").tooltip(
                        "已填密钥但未启用"
                    )
            # tags + 模型数（次行）
            with ui.row().classes("items-center q-mt-xs").style(
                "gap: 4px; flex-wrap: wrap;"
            ):
                for t in r.tags[:3]:
                    ui.label(t).classes("text-caption").style(
                        "background: #f3f4f6; border-radius: 4px; "
                        "padding: 0 5px; font-size: 10px; color: #4b5563;"
                    )
                if r.enabled and r.model_count > 0:
                    ui.label(f"{r.model_count} 模型").classes("text-caption").style(
                        "background: #ecfdf5; color: #065f46; "
                        "border-radius: 4px; padding: 0 5px; font-size: 10px;"
                    )

    def _render_more_card() -> None:
        with ui.card().props("flat bordered").style(
            "flex: 0 0 calc((100% - 32px) / 4); min-width: 220px; "
            "padding: 10px 12px; cursor: pointer; "
            "border-color: #c7d2fe; background: #eef2ff; "
            "display: flex; align-items: center; justify-content: center;"
        ).on("click", lambda _e=None: _open_more_dialog()):
            with ui.column().classes("items-center").style("gap: 2px;"):
                ui.icon("apps", size="22px").classes("text-indigo-7")
                ui.label("更多").style(
                    "font-weight: 600; font-size: 13px; color: #3730a3;"
                )
                ui.label("查看全部 / 搜索 / 添加").classes(
                    "text-caption text-grey-7"
                ).style("font-size: 10px;")

    def _open_more_dialog() -> None:
        with ui.dialog() as dialog, ui.card().style(
            "min-width: 720px; max-width: 880px; max-height: 80vh;"
        ):
            ui.label(more_dialog_title).classes("text-h6 q-mb-xs")
            ui.label(more_dialog_hint).classes("text-caption text-grey-7")

            with ui.row().classes("items-center w-full q-mt-sm").style("gap: 8px;"):
                search = ui.input(
                    placeholder="搜索厂商名（中英都行）...",
                ).props("dense outlined clearable").classes("col-grow")
                if on_add_custom is not None:
                    # 不关闭厂商列表 — 用户配完后能继续选别的厂商
                    ui.button(
                        "添加自定义",
                        icon="add",
                        on_click=lambda: on_add_custom(),
                    ).props("dense unelevated color=primary")
                ui.button("关闭", on_click=dialog.close).props("dense flat")

            grid_container = ui.column().classes("w-full q-mt-sm").style(
                "max-height: 60vh; overflow: auto;"
            )

            def _render_full_list() -> None:
                q = (search.value or "").strip().lower()
                grid_container.clear()
                with grid_container:
                    with ui.row().classes("w-full").style(
                        "gap: 8px; flex-wrap: wrap;"
                    ):
                        for p in providers:
                            if not _provider_passes_kind(p, kind):
                                continue
                            if q and q not in p.pid.lower() and q not in p.display_name.lower():
                                continue
                            enabled, configured, n = state_lookup(p.pid)
                            r = ProviderRowState(
                                pid=p.pid, display_name=p.display_name,
                                color=getattr(p, "color", "#6b7280") or "#6b7280",
                                tags=tuple(getattr(p, "tags", ()) or ()),
                                apply_key_url=getattr(p, "apply_key_url", "") or "",
                                enabled=enabled, configured=configured, model_count=n,
                            )
                            _render_full_card(r, dialog)

            search.on(
                "update:model-value",
                lambda _e=None: _render_full_list(),
            )
            _render_full_list()
        # 注册:配置 dialog 保存后调 trigger_more_dialog_refresh(kind) 即可刷新本列表
        # NiceGUI dialog 用户主动关闭后,_render_full_list 闭包仍可调
        # (NiceGUI 容器已无主,DOM 操作会安静失败 — trigger 里有 try/except 兜底)
        # 同 kind 单 dialog 场景:直接覆盖,不累积
        _MORE_KEY = ("more_dialog", kind)
        _REFRESHERS[_MORE_KEY].clear()
        _REFRESHERS[_MORE_KEY].append(_render_full_list)
        try:
            # 用户点"关闭"按钮或 ESC 关 dialog 时,移除 hook
            dialog.on("hide", lambda _e=None: _REFRESHERS[_MORE_KEY].clear())
        except Exception:  # noqa: BLE001
            pass
        dialog.open()

    def _render_full_card(r: ProviderRowState, dialog: Any) -> None:
        # click → 直接 on_pick(打开该厂商配置 dialog),**不关**厂商列表 dialog
        # 这样配置 dialog 叠在列表 dialog 上方,用户关掉配置 dialog 后还能继续从列表选别的厂商
        # 关闭厂商列表只能通过右上"关闭"按钮显式触发
        with ui.card().props("flat bordered").style(
            "flex: 0 0 calc((100% - 24px) / 3); min-width: 200px; "
            "padding: 8px 10px; cursor: pointer;"
        ).on("click", lambda _e=None, pid=r.pid: on_pick(pid)):
            with ui.row().classes("items-center no-wrap w-full").style("gap: 8px;"):
                first = r.display_name[:1] if r.display_name else "?"
                ui.html(
                    f'<div style="width:24px;height:24px;border-radius:6px;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'background:{r.color};color:white;font-weight:600;'
                    f'font-size:11px;flex:0 0 auto;">{first}</div>'
                )
                ui.label(r.display_name).style(
                    "font-weight: 500; font-size: 12px; flex: 1 1 auto; "
                    "white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
                )
                if r.enabled:
                    ui.icon("check_circle", size="14px").classes("text-positive")

    _refresh()
    # 注册到全局 hook,供外部(model_config._save_all 后)统一调
    _REFRESHERS[("strip", kind)].append(_refresh)
    return _refresh


__all__ = [
    "KIND_CLOUD",
    "KIND_LOCAL",
    "ProviderRowState",
    "select_top_providers",
    "render_provider_hero_strip",
    "trigger_hero_strip_refresh",
    "trigger_more_dialog_refresh",
]
