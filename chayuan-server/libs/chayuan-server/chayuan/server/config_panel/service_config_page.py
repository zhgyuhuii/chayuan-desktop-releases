"""「服务配置」—— 系统上线前的第一站。**所有连接配置的单一来源**。

布局
----
- 页顶状态条：所有 critical 服务是否全绿，红/黄/绿三档；
- 拓扑区：API 居中，左右辐射外部服务节点（卡片形式），颜色反映状态；
  服务列表动态扩展（DB / 向量库 / LLM / Embed / Redis / MinIO …），
  每个服务由 ``service_checks.all_checks()`` 提供；
- 卡片操作：
  * DB / Redis / MinIO → **「编辑」按钮内联打开 dialog**（复用 render_*_card），
    保存后立即重探活；其它页面已不再有这些字段的编辑入口。
  * 向量库 / LLM / Embed → 仍走「去配置」跳转（多平台 / yaml schema 复杂，
    后续 phase 再内联）。
- 底部「刷新探测」按钮：手动重跑 probes。

迁移历史
--------
原本同样的连接字段在 ②基础配置 / 服务管理 / 性能页 都有重复编辑入口，
现已收敛到本页：
- ``SQLALCHEMY_DATABASE_URI`` → 这里的 db 卡 ▸ 编辑
- ``REDIS_URL`` → redis 卡 ▸ 编辑
- ``FILE_STORAGE_BACKEND + MINIO_*`` → minio 卡 ▸ 编辑
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from chayuan.server.config_panel.service_checks import (
    ServiceStatus, probe_all, system_ready,
)


logger = logging.getLogger("chayuan.config_panel.service_config")


# ---------------------------------------------------------------------------
# 99 题:readiness probe 跨页缓存
# ---------------------------------------------------------------------------
# 之前 render_readiness_banner_if_needed 同步调 probe_all() — ThreadPool 并发探
# 5+ services × 3s timeout 各自,最坏 wall-clock 3-5s。这段时间在 dashboard
# 的 click → _render_page 同步 scope 内执行,**直接阻塞 NiceGUI socket.io 心跳**
# → 浏览器判定 client 死 → 整页 reload 回到 /dashboard 初始状态 → 用户感知为
# "点击模型厂商导致 connection lost"。
#
# 修复:
#   * 同步部分:仅 mount banner row(默认 hidden,8 个组件)— <10ms
#   * 异步部分:asyncio.to_thread(probe_all) — 不阻塞 event loop
#   * 30s TTL 缓存,跨页切复用;命中时同步渲染
#   * in-flight 去重,防止快速切页堆积多个并发 probe thread

_PROBE_CACHE_LOCK = threading.RLock()
_PROBE_CACHE: Dict[str, Any] = {"statuses": None, "ts": 0.0}
_PROBE_CACHE_TTL_S = 30.0
_PROBE_INFLIGHT_LOCK = threading.Lock()
_PROBE_INFLIGHT: Dict[str, Any] = {"running": False}


def _get_cached_readiness(*, max_age: float = _PROBE_CACHE_TTL_S) -> Optional[List[ServiceStatus]]:
    """读 readiness 缓存;过期返回 None。线程安全。"""
    with _PROBE_CACHE_LOCK:
        statuses = _PROBE_CACHE.get("statuses")
        ts = _PROBE_CACHE.get("ts", 0.0)
        if statuses is None:
            return None
        if (time.time() - ts) >= max_age:
            return None
        return list(statuses)


def _set_cached_readiness(statuses: List[ServiceStatus]) -> None:
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE["statuses"] = list(statuses)
        _PROBE_CACHE["ts"] = time.time()


def invalidate_readiness_cache() -> None:
    """供"刷新探测"按钮 / 保存配置后调用,强制下次重新 probe。"""
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE["statuses"] = None
        _PROBE_CACHE["ts"] = 0.0


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def render_service_config_page(ui, *, navigate_to: Callable[[str], None] | None = None) -> None:
    """``navigate_to(key)`` 由 dashboard 注入，允许卡片「去配置」跳到左栏对应项。"""
    state: Dict[str, Any] = {"statuses": probe_all()}

    def _refresh():
        state["statuses"] = probe_all()
        top.clear()
        grid.clear()
        with top:
            _render_status_bar(ui, state["statuses"])
        with grid:
            _render_topology(ui, state["statuses"], navigate_to, refresh=_refresh)

    # 顶部状态条
    top = ui.column().classes("w-full q-mb-md")
    with top:
        _render_status_bar(ui, state["statuses"])

    # 109 题:移除冗长的提示文字,只保留"重新探测"按钮(右对齐,紧凑)
    with ui.row().classes("items-center w-full q-mb-sm no-wrap").style("gap:8px"):
        ui.space()
        ui.button(
            "重新探测", icon="refresh",
            on_click=lambda: (_refresh(), ui.notify("已重新探测", color="info")),
        ).props("flat dense color=primary")

    # 91 题:固定 4 列两排布局
    #   第一排(基础设施): 业务库 / Redis / 文件存储 / 文件预览
    #   第二排(默认模型): 向量库 / 默认 LLM / 默认 Embedding / 默认 Rerank
    # 窄屏(< 1080px)自动塌缩到 2 列;移动端塌到 1 列。
    grid = ui.element("div").classes("w-full prereq-grid").style(
        "display:grid;"
        "grid-template-columns: repeat(4, minmax(220px, 1fr));"
        "gap:14px;"
    )
    # 内联响应式(老 NiceGUI 没法直接写 @media,塞进 head)
    ui.add_head_html(
        "<style>"
        "@media (max-width: 1080px){.prereq-grid{grid-template-columns:repeat(2,1fr) !important;}}"
        "@media (max-width: 600px){.prereq-grid{grid-template-columns:1fr !important;}}"
        "</style>"
    )
    with grid:
        _render_topology(ui, state["statuses"], navigate_to, refresh=_refresh)


# ---------------------------------------------------------------------------
# 状态条 + 拓扑
# ---------------------------------------------------------------------------

def _render_status_bar(ui, statuses: List[ServiceStatus]) -> None:
    """110 题:精简成一条紧凑提示行,可关闭(关闭后本会话不再显示)。

    旧实现是一个大 card(带 32px 图标 + h6 主标题 + 副文本),占两行高度。
    用户反馈"一条提示信息即可,不要太占地方,可以关闭"。
    现在:
        * 一行 row,16px 图标 + 单行文字
        * 右上 ✕ 按钮,点击 ``row.style("display:none")`` 立即收起
        * ready 时:绿色背景"系统已就绪"
        * 未就绪时:红色背景"尚有 N 项未就绪 · DB/Redis/..."(点也能关,但
          重新探测后又会出现 — _refresh 会重新 mount status bar)
    """
    ready = system_ready(statuses)
    crit_bad = [s for s in statuses if s.level == "critical" and not s.ok]
    color = "#21ba45" if ready else "#db2828"
    bg = "#f0fdf4" if ready else "#fef2f2"
    text = (
        "✅ 系统已就绪,可正常使用所有业务页面。" if ready
        else f"❗ 尚有 {len(crit_bad)} 项关键服务未就绪 · "
             f"{' / '.join(s.label for s in crit_bad)}"
    )

    bar = ui.row().classes("items-center w-full no-wrap").style(
        f"background:{bg}; border-left:3px solid {color}; "
        "padding:4px 10px; gap:8px; border-radius:4px;"
    )
    with bar:
        ui.icon(
            "check_circle" if ready else "warning",
            size="16px",
        ).classes(
            "text-positive" if ready else "text-negative"
        ).style("flex: none;")
        ui.label(text).classes(
            "text-caption " + ("text-positive" if ready else "text-negative")
        ).style(
            "font-size: 12px; line-height: 1.4; flex: 1; "
            "white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
        )
        ui.button(icon="close", on_click=lambda: bar.style("display:none")).props(
            "flat round dense size=xs color=grey-7"
        ).tooltip("关闭这条提示(重新探测后会再出现)")


# 91 题 / 95 题:固定显示顺序 — 4 列网格
#   第一排(基础设施):业务库 / Redis / 文件存储 / 文件预览
#   第二排(默认模型):向量库 / 默认 LLM / 默认 Embedding / 默认 Rerank
#   第三排(已配置 runtime):后续动态追加 ollama / vllm / infinity ...
# 列表里没有的 service 追加到末尾,保证 95 题动态加的 runtime 都能显示。
_DESIRED_ORDER: List[str] = [
    "db", "redis", "minio", "kkfileview",   # 第一排 — 基础设施
    "vs", "llm",                            # 第二排 — 默认模型(108 题:embed/rerank 迁出)
    # 95-4:其余 runtime(ollama / vllm / infinity / ...)按发现顺序追加
]


# ---------------------------------------------------------------------------
# 108 题:每个服务卡片的"说明标签"— 让用户一眼看出这个服务是干什么的
# ---------------------------------------------------------------------------

# 业务服务硬编码描述
_BUILTIN_SERVICE_DESCRIPTIONS: Dict[str, str] = {
    "db":          "主业务数据库 · 用户/会话/审计",
    "redis":       "缓存与异步队列",
    "minio":       "对象存储(S3 兼容)",
    "kkfileview":  "通用文档预览",
    "vs":          "知识库向量库",
    "llm":         "默认对话模型(LLM)",
}

# Framework capability → 中文描述
_CAPABILITY_DESCRIPTIONS: Dict[str, str] = {
    "chat":      "对话模型",
    "llm":       "对话模型",
    "embedding": "文本嵌入",
    "embed":     "文本嵌入",
    "clip":      "图像嵌入",
    "rerank":    "重排",
    "t2i":       "文生图",
    "t2v":       "文生视频",
    "tts":       "语音合成",
    "asr":       "语音识别",
    "ocr":       "图像识别文字(OCR)",
    "image":     "图像生成",
    "collab":    "文档协同",
    "custom":    "自定义服务",
    "base":      "基础设施",
}


def _describe_service(service_id: str) -> str:
    """返回服务卡片的"说明标签"文字。

    优先级:
        1. 业务服务硬编码描述(``db`` / ``redis`` / ``llm`` / ...)
        2. Framework spec.capabilities 翻译合并(如 infinity = ``("embedding","clip","rerank")``
           → "文本嵌入 / 图像嵌入 / 重排"运行)
        3. 兜底返回空串(UI 不显示)
    """
    # 1. 业务服务
    if service_id in _BUILTIN_SERVICE_DESCRIPTIONS:
        return _BUILTIN_SERVICE_DESCRIPTIONS[service_id]
    # 2. Framework spec
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            get_framework_spec_by_name,
        )
        spec = get_framework_spec_by_name(service_id)
        if spec is None or not spec.capabilities:
            return ""
        zh = [
            _CAPABILITY_DESCRIPTIONS.get(cap, cap)
            for cap in spec.capabilities
            if cap and cap not in ("base",)
        ]
        if not zh:
            return ""
        return " / ".join(zh) + " 运行"
    except Exception:  # noqa: BLE001
        return ""


def _sort_for_render(statuses: List[ServiceStatus]) -> List[ServiceStatus]:
    """按 _DESIRED_ORDER 排序;不在表里的(未来新增)追加到末尾。"""
    by_id = {s.service: s for s in statuses}
    out: List[ServiceStatus] = []
    seen: set = set()
    for sid in _DESIRED_ORDER:
        if sid in by_id:
            out.append(by_id[sid])
            seen.add(sid)
    for s in statuses:
        if s.service not in seen:
            out.append(s)
    return out


def _render_topology(
    ui, statuses: List[ServiceStatus],
    navigate_to: Callable[[str], None] | None,
    refresh: Callable[[], None] | None = None,
) -> None:
    # 服务 id → 仍需"跳去其它页"的目标（多平台/复杂 schema 暂未内联）
    # 62 题:llm/embed 直接跳到 ④ 默认模型 tab (file:xxx#tab 协议),
    # dashboard._render_page 会解析 # 后面的 tab id 并传给 render_model_settings_v2
    # 91 题:rerank 同样跳 ④ 默认模型 tab(暂未独立编辑入口)
    # 106 题:llm/embed/rerank tab id 改名 — defaults 在 page.py 仍叫 "defaults"
    jump_targets = {
        "vs": "file:kb_settings.yaml",
        "llm": "file:model_settings.yaml#defaults",
        "embed": "file:model_settings.yaml#defaults",
        "rerank": "file:model_settings.yaml#defaults",
        # kkFileView 的 KKFILEVIEW_URL 字段在 kb_settings.yaml 里,
        # 跳到知识库配置页让用户编辑;后续可考虑内联编辑
        "kkfileview": "file:kb_settings.yaml",
    }
    # 106 题:framework 卡片(ollama/vllm/infinity/comfyui/llamacpp/xinference/...)
    # 不在 base 已知列表里 — 自动 fallback 到"② 模型厂商"tab,用户在那里点对应
    # 厂商 hero card 打开配置 dialog(配 URL / api_key / models)。
    _BASE_KNOWN_SERVICES = {
        "db", "redis", "minio", "kkfileview",
        "vs", "llm", "embed", "rerank",
    }
    # 服务 id → 内联 edit dialog 用哪个 renderer（单值连接配置，已迁至本页）
    inline_editors = {
        "db": "main_db",
        "redis": "redis",
        "minio": "storage",
    }

    for s in _sort_for_render(statuses):
        # 106 题:framework 卡片(不在 base 已知 service 列表里)→ 自动跳到
        # ② 模型厂商 tab,用户在那里走对应厂商的 dialog 配 URL / api_key / models
        jump_key = jump_targets.get(s.service, "")
        if not jump_key and s.service not in _BASE_KNOWN_SERVICES:
            jump_key = "file:model_settings.yaml#providers"
        _render_service_card(
            ui, s,
            jump_key=jump_key,
            inline_editor=inline_editors.get(s.service, ""),
            navigate_to=navigate_to,
            refresh=refresh,
        )


def _render_service_card(
    ui, s: ServiceStatus,
    *,
    jump_key: str = "",
    inline_editor: str = "",
    navigate_to: Callable[[str], None] | None = None,
    refresh: Callable[[], None] | None = None,
) -> None:
    color, header_bg, emoji = _severity_style(s)

    with ui.card().classes("q-pa-none").style(
        f"border-top:5px solid {color};display:flex;flex-direction:column;"
        f"min-height:200px;overflow:hidden"
    ):
        with ui.row().classes("items-center w-full q-px-md q-py-sm").style(
            f"background:{header_bg};gap:8px"
        ):
            ui.label(emoji).style("font-size:22px;flex:none")
            with ui.column().classes("q-gutter-none flex-1").style("min-width:0"):
                with ui.row().classes("items-center no-wrap").style("gap:6px"):
                    ui.label(s.label).classes("text-base font-semibold")
                    if s.level == "critical":
                        ui.badge("必填").props("color=red")
                    elif s.level == "recommended":
                        ui.badge("推荐").props("color=orange")
                    else:
                        ui.badge("可选").props("color=grey")
                # 108 题:服务说明标签 — 让用户一眼看清这个服务是干什么的
                description = _describe_service(s.service)
                if description:
                    ui.label(description).classes("text-xs text-grey-7").style(
                        "font-size: 11px; line-height: 1.3;"
                    )
                ui.label(f"{'已配置' if s.configured else '未配置'}"
                         f"{'  |  连接 OK' if s.ok else '  |  未就绪'}")\
                    .classes("text-xs text-grey-8")

        with ui.element("div").classes("q-px-md q-py-sm").style(
            "flex:1 1 auto;min-height:0;overflow:hidden;font-size:12px"
        ):
            if s.endpoint:
                ui.label(f"endpoint：{s.endpoint}").classes(
                    "text-xs text-grey-8"
                ).style(
                    "font-family:monospace;white-space:nowrap;"
                    "overflow:hidden;text-overflow:ellipsis"
                )
            ui.label(s.detail or "-").style(
                "color:#555;line-height:1.4;"
                "display:-webkit-box;-webkit-line-clamp:3;"
                "-webkit-box-orient:vertical;overflow:hidden;"
            ).tooltip(s.detail)

        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style(
            "gap:6px;border-top:1px solid #eee"
        ):
            if s.latency_ms is not None:
                ui.label(f"{s.latency_ms} ms").classes("text-xs text-grey-7")
            else:
                ui.space()
            ui.button(
                "详情", icon="info",
                on_click=lambda _s=s: _open_detail_dialog(ui, _s),
            ).props("flat dense size=sm color=primary")
            if inline_editor:
                # 单值连接配置（DB / Redis / MinIO）→ 内联 dialog 编辑
                ui.button(
                    "编辑", icon="edit",
                    on_click=lambda _e=inline_editor: _open_inline_edit_dialog(
                        ui, _e, refresh,
                    ),
                ).props("unelevated dense size=sm color=primary")
            elif jump_key and navigate_to is not None:
                # 复杂多平台配置（向量库 / LLM / Embed）→ 仍跳左栏对应页
                ui.button(
                    "去配置", icon="open_in_new",
                    on_click=lambda _k=jump_key: navigate_to(_k),
                ).props("flat dense size=sm color=primary")


def _open_detail_dialog(ui, s: ServiceStatus) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(560px, 92vw)"
    ):
        ui.label(s.label).classes("text-lg font-semibold")
        ui.label(s.detail or "-").classes("text-sm text-grey-8 q-mb-md")

        if s.endpoint:
            ui.label("endpoint").classes("text-xs text-grey-7")
            ui.code(s.endpoint).classes("w-full").style("font-size:12px")

        if s.fields:
            ui.separator().classes("q-my-sm")
            ui.label("当前字段值（脱敏）").classes("text-xs text-grey-7")
            for f in s.fields:
                with ui.row().classes("w-full items-center").style("gap:8px"):
                    ui.label(f.get("label", f.get("name", ""))).classes(
                        "text-sm"
                    ).style("min-width:140px")
                    ui.label(f.get("value_masked") or "(未设置)").classes(
                        "text-sm font-mono text-grey-8"
                    )
                if f.get("help"):
                    ui.label(f["help"]).classes("text-xs text-grey-7")

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("关闭", on_click=dlg.close).props("color=primary")
    dlg.open()


def _open_inline_edit_dialog(
    ui, editor: str, refresh: Callable[[], None] | None,
) -> None:
    """单值连接配置内联编辑：复用 dashboard 现成的 render_*_card。

    editor 取值：
      - 'main_db' → render_main_db_card（写 SQLALCHEMY_DATABASE_URI）
      - 'redis'   → render_redis_card  （写 REDIS_URL）
      - 'storage' → render_storage_card（写 FILE_STORAGE_BACKEND + MINIO_*）

    保存路径完全沿用各 card 内部的逻辑（结构化字段 → 验证 → 写 yaml）；
    dialog 关闭时回调 refresh() 让本页拓扑卡颜色立刻更新。
    """
    title_map = {
        "main_db": "主业务数据库",
        "redis": "Redis 连接",
        "storage": "文件存储（KB 路径 / MinIO）",
    }
    title = title_map.get(editor, "服务配置")

    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(680px, 96vw); max-width: 96vw;"
        "max-height: 88vh; overflow-y: auto"
    ):
        ui.label(title).classes("text-lg font-semibold q-mb-sm")
        try:
            if editor == "main_db":
                from chayuan.server.config_panel.db_config import render_main_db_card
                render_main_db_card(ui)
            elif editor == "redis":
                from chayuan.server.config_panel.redis_config import render_redis_card
                render_redis_card(ui)
            elif editor == "storage":
                from chayuan.server.config_panel.storage_config import render_storage_card
                render_storage_card(ui, show_title=False)
            else:
                ui.label(f"未知 editor：{editor!r}").classes("text-negative")
        except Exception as e:  # noqa: BLE001
            logger.exception("inline edit dialog render failed: %s", editor)
            ui.label(f"渲染失败：{type(e).__name__}: {e}").classes(
                "text-negative text-sm"
            )

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button(
                "完成", icon="check",
                on_click=lambda: (
                    dlg.close(),
                    refresh() if refresh else None,
                ),
            ).props("color=primary unelevated")

    dlg.open()


def _severity_style(s: ServiceStatus):
    """返回 (border color, header bg, emoji)。"""
    if s.ok:
        return "#21ba45", "#f0fff4", "✅"
    if s.level == "critical":
        return "#db2828", "#fff5f5", "🔴"
    if s.level == "recommended":
        return "#f2711c", "#fffbf0", "🟡"
    return "#767676", "#f7f7f7", "⚪"


# ---------------------------------------------------------------------------
# 前置守卫：其它页面顶部挂红色 banner 提示未就绪
# ---------------------------------------------------------------------------

def render_readiness_banner_if_needed(ui) -> None:
    """由 dashboard._render_page 在渲染其它页面前调。**99 题非阻塞版**。

    立即 mount 隐藏 banner(<10ms),probe_all 走 ``asyncio.to_thread`` 后台跑;
    完成后通过修改已 mount 的元素属性回填(NiceGUI 自动 reactive 推到客户端)。

    缓存策略:
        * 同会话切页 30s 内直接读 ``_get_cached_readiness`` 同步渲染,跳过 thread
        * 缓存过期或首次进入 → 启 ``asyncio.create_task(_async_probe...)``
        * in-flight 去重:正在 probe 时再次调用不会再起 thread,只是 mount banner
          等首次 probe 完成时同时刷新所有已 mount 的 banner

    架构特点:
        * 调用方 API 完全不变(``render_readiness_banner_if_needed(ui)``)
        * 同步路径 mount 8 个组件,绝对不会卡 NiceGUI WS 心跳
        * Banner row 默认 ``display:none``,无关键服务故障时永远不显示
        * 关键服务故障时 banner 自动展开,文字异步填入
    """
    # 1. 同步:mount 隐藏 banner 占位(<10ms)
    banner_row = ui.row().classes("w-full q-pa-sm q-mb-md").style(
        "background:#fff5f5;border-left:5px solid #db2828;gap:10px;display:none;"
    )
    label_holder: Dict[str, Any] = {"label": None}
    with banner_row:
        ui.icon("report").classes("text-negative").style("font-size:24px;flex:none")
        with ui.column().classes("flex-1"):
            label_holder["label"] = ui.label("").classes(
                "text-sm text-negative font-semibold"
            )
            ui.label("先去「服务配置」页把红色卡片补齐,再回来。").classes(
                "text-xs text-grey-8"
            )

    # 2. 缓存命中 → 同步直接渲染(跨页切的常见路径)
    cached = _get_cached_readiness()
    if cached is not None:
        _apply_readiness_to_banner(cached, banner_row, label_holder["label"])
        return

    # 3. 缓存未命中 → 异步 probe,不阻塞 click handler
    _start_async_probe_and_fill(ui, banner_row, label_holder)


def _apply_readiness_to_banner(
    statuses: List[ServiceStatus],
    banner_row: Any,
    label: Any,
) -> None:
    """探活完成后填 banner;client 已死时静默退出。**纯无副作用**。"""
    try:
        bad = [
            s for s in statuses
            if getattr(s, "level", None) == "critical" and not getattr(s, "ok", True)
        ]
        if not bad:
            # 关键服务都 ok → 保持 hidden
            return
        if label is not None:
            try:
                label.set_text(
                    f"⚠️  关键服务尚未就绪({', '.join(s.label for s in bad)}),"
                    "本页的部分操作可能失败。"
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            banner_row.style(
                "background:#fff5f5;border-left:5px solid #db2828;gap:10px;"
                "display:flex;"
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        logger.debug("apply readiness probe to banner failed: %s", e)


def _start_async_probe_and_fill(
    ui: Any,
    banner_row: Any,
    label_holder: Dict[str, Any],
) -> None:
    """启 asyncio task 跑 probe_all,完成后回填 banner。

    in-flight 去重:同时只允许一个 probe thread 跑;若已有 in-flight,
    本次 banner 仍 mount 但等下一帧 cache 命中时再填(用户切页很快时常见)。
    """
    import asyncio

    # in-flight 去重 — 防止快速切页堆积多 probe thread
    with _PROBE_INFLIGHT_LOCK:
        if _PROBE_INFLIGHT.get("running"):
            # 已有在跑;本 banner 注册一个轮询,等 cache 命中再填
            _schedule_banner_recheck(ui, banner_row, label_holder)
            return
        _PROBE_INFLIGHT["running"] = True

    async def _do_probe() -> None:
        try:
            statuses = await asyncio.to_thread(probe_all)
            _set_cached_readiness(statuses)
            _apply_readiness_to_banner(
                statuses, banner_row, label_holder.get("label"),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("readiness probe in background failed: %s", e)
        finally:
            with _PROBE_INFLIGHT_LOCK:
                _PROBE_INFLIGHT["running"] = False

    coro = _do_probe()
    try:
        asyncio.create_task(coro)
    except Exception as e:  # noqa: BLE001
        # 没 event loop(测试或脚本场景)— 静默退出,banner 保持 hidden
        # 显式关 coroutine 避免 RuntimeWarning
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        logger.debug("readiness probe task creation failed: %s", e)
        with _PROBE_INFLIGHT_LOCK:
            _PROBE_INFLIGHT["running"] = False


def _schedule_banner_recheck(
    ui: Any,
    banner_row: Any,
    label_holder: Dict[str, Any],
) -> None:
    """已有 probe 在跑时,本 banner 用 ui.timer 短轮询等 cache 命中。

    最多轮询 5 次 × 1s = 5s,超过则放弃(probe 也应在 5s 内完成)。
    """
    state: Dict[str, int] = {"tries": 0}

    def _check() -> None:
        state["tries"] += 1
        cached = _get_cached_readiness()
        if cached is not None:
            _apply_readiness_to_banner(
                cached, banner_row, label_holder.get("label"),
            )
            return
        if state["tries"] >= 5:
            return
        try:
            ui.timer(1.0, _check, once=True)
        except Exception:  # noqa: BLE001
            pass

    try:
        ui.timer(1.0, _check, once=True)
    except Exception:  # noqa: BLE001
        pass


