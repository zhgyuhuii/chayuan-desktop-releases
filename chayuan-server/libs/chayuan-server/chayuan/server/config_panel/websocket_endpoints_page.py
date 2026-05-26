"""「WebSocket 端点」配置页（NiceGUI）。

与「自定义工具」页同款卡片网格 + 编辑弹窗 + 测试弹窗：
- 卡片显示：URL / auth 类型 / 启用状态 / 描述（3 行省略）；
- 编辑弹窗：基础信息 + auth（4 种类型）+ on_connect 初始帧 + 主请求模板 +
  params schema（同 HTTP 版）+ 接收行为；
- 「测试连接」：按当前 spec 与样本参数真实跑一次 ``probe_websocket``，
  把握手耗时 / 发送帧 / 接收帧按时序可视化。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List

from chayuan.server.config_panel.websocket_endpoints_store import (
    WSAuth,
    WSEndpointSpec,
    WSParamSpec,
    delete_endpoint,
    list_endpoints,
    save_endpoint,
    validate_spec,
)
from chayuan.server.shared.websocket_client import AuthSpec, probe_websocket


logger = logging.getLogger("chayuan.config_panel.ws")


def render_ws_endpoints_page(
    ui, *, mark_restart_needed: Callable[[], None] | None = None,
) -> None:
    state: Dict[str, Any] = {"endpoints": list_endpoints()}

    def _reload() -> None:
        state["endpoints"] = list_endpoints()
        grid.clear()
        with grid:
            _render_list(ui, state, mark_restart_needed, _reload)

    with ui.row().classes("items-center w-full q-mb-sm no-wrap").style("gap:8px"):
        ui.label(
            "WebSocket 端点：把任意 ws:// / wss:// 接口包装成 Agent 工具。"
            "LLM 给参数 → Chayuan 建立 WS → 发请求 → 收 N 条或超时 → 按固定格式返回。"
        ).classes("text-sm text-grey-7").style("flex:1;min-width:0")
        ui.button(
            "新建端点", icon="add_link",
            on_click=lambda: _open_editor(ui, None, mark_restart_needed, _reload),
        ).props("color=primary")
        ui.button(
            icon="refresh",
            on_click=lambda: (_reload(), ui.notify("已重新载入", color="info")),
        ).props("flat round dense").tooltip("重新读取 websocket_endpoints.yaml")

    grid = ui.element("div").classes("w-full").style(
        "display:grid;"
        "grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));"
        "gap:12px;"
    )
    with grid:
        _render_list(ui, state, mark_restart_needed, _reload)


def _render_list(
    ui, state: Dict[str, Any],
    mark_restart: Callable[[], None] | None, refresh: Callable[[], None],
) -> None:
    endpoints: List[WSEndpointSpec] = state["endpoints"]
    if not endpoints:
        with ui.column().classes("w-full items-center q-pa-lg").style(
            "grid-column: 1 / -1"
        ):
            ui.icon("cable").classes("text-grey-5").style("font-size:56px")
            ui.label("还没有 WebSocket 端点").classes("text-base text-grey-7 q-mt-sm")
            ui.label("点右上角「新建端点」定义第一个。").classes("text-xs text-grey-7")
        return
    for e in endpoints:
        _render_card(ui, e, mark_restart, refresh)


def _render_card(
    ui, spec: WSEndpointSpec,
    mark_restart: Callable[[], None] | None, refresh: Callable[[], None],
) -> None:
    card = ui.card().classes("q-pa-none").style(
        "width:100%;min-height:200px;display:flex;flex-direction:column;overflow:hidden"
    )
    with card:
        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style(
            "gap:8px"
        ):
            ui.icon("cable").classes("text-primary").style("font-size:28px;flex:none")
            with ui.column().classes("q-gutter-none flex-1").style("min-width:0"):
                with ui.row().classes("items-center no-wrap").style("gap:6px"):
                    ui.label(spec.title or spec.name).classes("text-base font-semibold")
                    ui.badge(spec.auth.type or "none").props("color=secondary")
                    if spec.enabled:
                        ui.badge("已启用").props("color=positive outline")
                    else:
                        ui.badge("未启用").props("color=grey outline")
                ui.label(spec.url).classes("text-xs text-grey-8").style(
                    "font-family:monospace;white-space:nowrap;"
                    "overflow:hidden;text-overflow:ellipsis"
                )

        with ui.element("div").classes("q-px-md").style(
            "flex:1 1 auto;min-height:0;overflow:hidden"
        ):
            ui.label(spec.description or "（无描述）").style(
                "font-size:12px;color:#555;line-height:1.45;"
                "display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;"
                "overflow:hidden;text-overflow:ellipsis;"
            ).tooltip(spec.description)

        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style(
            "gap:6px;border-top:1px solid #eee"
        ):
            meta = (f"recv≤{spec.max_messages}  "
                    f"timeout={int(spec.receive_timeout)}s  "
                    f"fmt={spec.message_format}")
            ui.label(meta).classes("text-xs text-grey-7").style(
                "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
                "flex:1;min-width:0"
            )
            ui.button(
                "测试连接", icon="play_circle",
                on_click=lambda s=spec: _open_test_dialog(ui, s),
            ).props("flat dense size=sm color=primary")
            ui.button(
                "编辑", icon="edit",
                on_click=lambda s=spec: _open_editor(ui, s, mark_restart, refresh),
            ).props("flat dense size=sm color=primary")
            ui.button(
                icon="delete_outline",
                on_click=lambda s=spec: _confirm_delete(ui, s, mark_restart, refresh),
            ).props("flat dense size=sm color=negative").tooltip("删除")


# ---------------------------------------------------------------------------
# 编辑
# ---------------------------------------------------------------------------

def _open_editor(
    ui, spec: WSEndpointSpec | None,
    mark_restart: Callable[[], None] | None, refresh: Callable[[], None],
) -> None:
    creating = spec is None
    if creating:
        spec = WSEndpointSpec(name="")

    ctx: Dict[str, Any] = {"params_rows": [], "on_connect_rows": []}

    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(860px, 94vw); max-width: 94vw; "
        "max-height: 92vh; overflow: auto;"
    ):
        ui.label("新建 WebSocket 端点" if creating else f"编辑：{spec.title or spec.name}")\
            .classes("text-xl font-semibold")
        ui.label(
            "description 会作为 LLM 工具说明；request_template / on_connect 支持 {name} 占位。"
        ).classes("text-xs text-grey-7 q-mb-sm")

        with ui.grid(columns=2).classes("w-full q-gutter-sm"):
            name_in = ui.input(label="name（唯一，工具 ID）", value=spec.name).props(
                "outlined dense"
            )
            title_in = ui.input(label="title（中文展示名）", value=spec.title).props(
                "outlined dense"
            )
            url_in = ui.input(
                label="URL（ws:// 或 wss://）", value=spec.url,
            ).props("outlined dense").classes("col-span-2")
            max_in = ui.number(
                label="最多接收消息数", value=spec.max_messages, min=1, max=500, step=1,
            ).props("outlined dense")
            timeout_in = ui.number(
                label="接收超时（秒）", value=spec.receive_timeout, min=1, max=300, step=1,
            ).props("outlined dense")
            fmt_in = ui.select(
                ["json", "text"], label="消息格式", value=spec.message_format,
            ).props("outlined dense")
            close_first_in = ui.switch("首条即关", value=spec.close_after_first)
            enabled_in = ui.switch("启用", value=spec.enabled)

        desc_in = ui.textarea(
            label="description（LLM 提示词）", value=spec.description,
        ).props("outlined dense autogrow input-style='min-height:5em'")\
            .classes("w-full q-mt-sm")

        # 鉴权
        ui.label("鉴权").classes("text-sm font-semibold q-mt-md")
        with ui.grid(columns=3).classes("w-full q-gutter-sm"):
            auth_type_in = ui.select(
                ["none", "header", "query", "bearer"],
                label="类型", value=spec.auth.type or "none",
            ).props("outlined dense")
            auth_key_in = ui.input(
                label="Header 名 / Query 键",
                value=spec.auth.key,
            ).props("outlined dense")
            auth_value_in = ui.input(
                label="值（token / secret）",
                value=spec.auth.value, password=True, password_toggle_button=True,
            ).props("outlined dense")

        # on_connect 帧列表
        ui.label("on_connect：连上后自动发送的初始帧").classes(
            "text-sm font-semibold q-mt-md"
        )
        on_connect_container = ui.column().classes("w-full q-mt-xs")

        def _redraw_on_connect():
            on_connect_container.clear()
            with on_connect_container:
                for i, val in enumerate(ctx["on_connect_rows"]):
                    _render_on_connect_row(ui, ctx, i, val, _redraw_on_connect)

        ctx["on_connect_rows"] = list(spec.on_connect or [])
        _redraw_on_connect()
        with ui.row().classes("q-mt-xs"):
            ui.button(
                "+ 添加初始帧", icon="add",
                on_click=lambda: (ctx["on_connect_rows"].append(""), _redraw_on_connect()),
            ).props("flat dense color=primary")

        # 请求模板
        req_in = ui.textarea(
            label="request_template（支持 {name} 占位；空=不发主消息，只订阅 on_connect）",
            value=spec.request_template,
        ).props(
            "outlined dense autogrow "
            "input-style='min-height:4em;font-family:monospace'"
        ).classes("w-full q-mt-md")

        # 响应抽取
        resp_in = ui.input(
            label="response_path（可选 dotted，如 data.price）",
            value=spec.response_path,
        ).props("outlined dense").classes("w-full q-mt-sm")

        # 参数 schema
        ui.label("参数 schema（LLM 会按此填参）").classes("text-sm font-semibold q-mt-md")
        params_container = ui.column().classes("w-full q-mt-xs")

        def _redraw_params():
            params_container.clear()
            with params_container:
                for i, row in enumerate(ctx["params_rows"]):
                    _render_param_row(ui, ctx, i, row, _redraw_params)

        for p in spec.params:
            ctx["params_rows"].append({
                "name": p.name, "type": p.type, "description": p.description,
                "required": p.required,
                "default": "" if p.default is None else str(p.default),
            })
        _redraw_params()
        with ui.row().classes("q-mt-xs"):
            ui.button(
                "+ 添加参数", icon="add",
                on_click=lambda: (
                    ctx["params_rows"].append({
                        "name": "", "type": "string", "description": "",
                        "required": False, "default": "",
                    }),
                    _redraw_params(),
                ),
            ).props("flat dense color=primary")

        test_slot = ui.column().classes("w-full q-mt-md")

        def _gather() -> WSEndpointSpec:
            params: List[WSParamSpec] = []
            for row in ctx["params_rows"]:
                params.append(WSParamSpec(
                    name=str(row.get("name", "")).strip(),
                    type=str(row.get("type", "string") or "string"),
                    description=str(row.get("description", "") or ""),
                    required=bool(row.get("required", False)),
                    default=(row.get("default") or None) or None,
                ))
            return WSEndpointSpec(
                name=(name_in.value or "").strip(),
                title=(title_in.value or "").strip(),
                description=desc_in.value or "",
                url=(url_in.value or "").strip(),
                auth=WSAuth(
                    type=auth_type_in.value or "none",
                    key=auth_key_in.value or "",
                    value=auth_value_in.value or "",
                ),
                on_connect=[x for x in ctx["on_connect_rows"] if x],
                request_template=req_in.value or "",
                message_format=fmt_in.value or "json",
                response_path=resp_in.value or "",
                max_messages=int(max_in.value or 5),
                receive_timeout=float(timeout_in.value or 10),
                close_after_first=bool(close_first_in.value),
                params=params,
                enabled=bool(enabled_in.value),
            )

        def _do_test():
            s = _gather()
            errs = validate_spec(s)
            if errs:
                ui.notify("；".join(errs), color="negative", multi_line=True)
                return
            # 样本参数
            sample: Dict[str, Any] = {}
            for p in s.params:
                if p.default is not None and p.default != "":
                    sample[p.name] = p.default
                elif p.required:
                    sample[p.name] = ("?" if p.type == "string" else
                                      1 if p.type == "integer" else
                                      1.0 if p.type == "number" else True)
            try:
                req = s.request_template.format(**sample) if s.request_template else None
            except Exception:  # noqa: BLE001
                req = s.request_template or None
            on_conn = [x.format(**sample) if "{" in x else x for x in s.on_connect]

            res = probe_websocket(
                url=s.url,
                auth=AuthSpec(type=s.auth.type, key=s.auth.key, value=s.auth.value),
                on_connect_messages=on_conn,
                request_message=req,
                max_messages=s.max_messages,
                receive_timeout=s.receive_timeout,
                close_after_first=s.close_after_first,
                response_path=s.response_path,
            )
            _render_probe_result(ui, test_slot, res)

        def _do_save():
            s = _gather()
            try:
                save_endpoint(s)
            except Exception as e:  # noqa: BLE001
                ui.notify(f"保存失败：{e}", color="negative", multi_line=True)
                return
            if mark_restart is not None:
                try:
                    mark_restart()
                except Exception:  # noqa: BLE001
                    pass
            ui.notify(f"已保存 {s.name}", color="positive")
            dlg.close()
            refresh()

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("取消", on_click=dlg.close).props("flat")
            ui.button("测试连接", icon="play_arrow", on_click=_do_test).props(
                "color=secondary"
            )
            ui.button("保存", icon="save", on_click=_do_save).props("color=primary")

    dlg.open()


def _render_on_connect_row(
    ui, ctx: Dict[str, Any], idx: int, value: str, redraw: Callable[[], None],
) -> None:
    with ui.row().classes("items-center w-full no-wrap q-mb-xs").style("gap:6px"):
        el = ui.input(
            label=f"帧 #{idx+1}（文本或 JSON 字符串）", value=value,
        ).props("outlined dense").classes("flex-1")

        def _sync(_=None, _i=idx, _el=el):
            ctx["on_connect_rows"][_i] = _el.value or ""

        el.on_value_change(_sync)

        def _del(_=None, _i=idx):
            ctx["on_connect_rows"].pop(_i)
            redraw()

        ui.button(icon="close", on_click=_del).props(
            "flat dense round size=sm color=negative"
        )


def _render_param_row(
    ui, ctx: Dict[str, Any], idx: int, row: Dict[str, Any],
    redraw: Callable[[], None],
) -> None:
    with ui.row().classes("items-center w-full no-wrap q-mb-xs").style("gap:6px"):
        name_el = ui.input(label="name", value=row.get("name", "")).props(
            "outlined dense"
        ).style("width:140px")
        type_el = ui.select(
            ["string", "integer", "number", "boolean"],
            label="type", value=row.get("type", "string"),
        ).props("outlined dense").style("width:120px")
        desc_el = ui.input(label="描述", value=row.get("description", "")).props(
            "outlined dense"
        ).classes("flex-1")
        required_el = ui.switch("必填", value=bool(row.get("required", False)))
        default_el = ui.input(
            label="default（可选）",
            value="" if row.get("default") is None else str(row.get("default")),
        ).props("outlined dense").style("width:140px")

        def _sync(_=None, _r=row, _n=name_el, _t=type_el, _d=desc_el,
                  _rq=required_el, _df=default_el):
            _r["name"] = _n.value
            _r["type"] = _t.value
            _r["description"] = _d.value
            _r["required"] = bool(_rq.value)
            _r["default"] = _df.value if _df.value != "" else None

        for el in (name_el, type_el, desc_el, required_el, default_el):
            el.on_value_change(_sync)

        def _del(_=None, _i=idx):
            ctx["params_rows"].pop(_i)
            redraw()

        ui.button(icon="close", on_click=_del).props(
            "flat dense round size=sm color=negative"
        )


# ---------------------------------------------------------------------------
# 独立测试弹窗
# ---------------------------------------------------------------------------

def _open_test_dialog(ui, spec: WSEndpointSpec) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(720px, 94vw); max-width: 94vw; "
        "max-height: 92vh; overflow: auto;"
    ):
        ui.label(f"测试：{spec.title or spec.name}").classes("text-lg font-semibold")
        ui.label(spec.url).classes("text-xs text-grey-8 font-mono q-mb-sm")

        value_els: Dict[str, Any] = {}
        if spec.params:
            with ui.grid(columns=2).classes("w-full q-gutter-sm"):
                for p in spec.params:
                    default = p.default if p.default is not None else ""
                    if p.type == "boolean":
                        el = ui.switch(p.name, value=bool(default))
                    else:
                        el = ui.input(
                            label=f"{p.name}（{'必填' if p.required else '可选'}）",
                            value=str(default),
                        ).props("outlined dense")
                    value_els[p.name] = (p, el)

        out_slot = ui.column().classes("w-full q-mt-md")

        def _run():
            kwargs: Dict[str, Any] = {}
            for name, (p, el) in value_els.items():
                raw = getattr(el, "value", None)
                if raw in (None, ""):
                    if p.required and p.default is None:
                        ui.notify(f"缺少必填参数 {name}", color="warning")
                        return
                    continue
                kwargs[name] = _coerce(raw, p.type)

            try:
                req = spec.request_template.format(**kwargs) if spec.request_template else None
            except Exception:  # noqa: BLE001
                req = spec.request_template or None
            on_conn = [x.format(**kwargs) if "{" in x else x for x in spec.on_connect]

            res = probe_websocket(
                url=spec.url,
                auth=AuthSpec(
                    type=spec.auth.type, key=spec.auth.key, value=spec.auth.value,
                ),
                on_connect_messages=on_conn,
                request_message=req,
                max_messages=spec.max_messages,
                receive_timeout=spec.receive_timeout,
                close_after_first=spec.close_after_first,
                response_path=spec.response_path,
            )
            _render_probe_result(ui, out_slot, res)

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("关闭", on_click=dlg.close).props("flat")
            ui.button("发起连接", icon="play_arrow", on_click=_run).props("color=primary")

    dlg.open()


def _coerce(raw: Any, typ: str):
    if typ == "integer":
        return int(raw)
    if typ == "number":
        return float(raw)
    if typ == "boolean":
        return bool(raw)
    return str(raw)


# ---------------------------------------------------------------------------
# 结果渲染
# ---------------------------------------------------------------------------

def _render_probe_result(ui, slot, res) -> None:
    slot.clear()
    with slot:
        color = "positive" if res.ok else "negative"
        header = (
            f"{'✅ 连接成功' if res.ok else '❌ 连接失败'}   "
            f"握手 {res.connect_ms}ms   持续 {res.duration_ms}ms   "
            f"消息 {len(res.messages)} 条"
        )
        ui.label(header).classes(f"text-sm text-{color}")
        if res.error:
            ui.label(f"error: {res.error}").classes("text-sm text-negative")

        # 消息时序
        for m in res.messages:
            arrow = "→" if m.direction == "sent" else "←"
            head = f"{arrow} {m.direction:<8} +{int(m.ts*1000):>5}ms   kind={m.kind}"
            ui.label(head).classes(
                "text-xs " + ("text-primary" if m.direction == "sent" else "text-positive")
            ).style("font-family:monospace")
            pretty = (
                json.dumps(m.body, ensure_ascii=False, indent=2)
                if m.kind == "json" else str(m.body or "")
            )
            ui.code(pretty, language="json" if m.kind == "json" else "text")\
                .classes("w-full").style("max-height:240px;overflow:auto")
            if m.parsed is not None:
                ui.label(f"response_path 提取：{m.parsed!r}")\
                    .classes("text-xs text-grey-8")


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------

def _confirm_delete(
    ui, spec: WSEndpointSpec,
    mark_restart: Callable[[], None] | None, refresh: Callable[[], None],
) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width:360px"):
        ui.label(f"确认删除 {spec.title or spec.name}？").classes(
            "text-base font-semibold"
        )
        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("取消", on_click=dlg.close).props("flat")

            def _do():
                if delete_endpoint(spec.name):
                    if mark_restart is not None:
                        try:
                            mark_restart()
                        except Exception:  # noqa: BLE001
                            pass
                    ui.notify(f"已删除 {spec.name}", color="positive")
                else:
                    ui.notify("未找到", color="warning")
                dlg.close()
                refresh()

            ui.button("删除", color="negative", on_click=_do)
    dlg.open()
