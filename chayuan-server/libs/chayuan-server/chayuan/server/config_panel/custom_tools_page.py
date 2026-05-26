"""「自定义工具」配置页（NiceGUI）。

左栏卡片网格展示所有 custom_tools.yaml 里的工具；点击「编辑」打开大弹窗：
- 基础信息：name / title / description
- HTTP 调用：method / URL / headers
- 参数 schema：可动态添加参数行（name / type / desc / required / default / enum）
- body_template（POST/PUT/PATCH 专用）
- 「测试」按钮：按当前填好的 URL + 参数样本（required 的默认占位）实际发一次 HTTP，
  使用 ``shared.http_test.run_request``，返回结构化 ProbeResult

新增按钮：「新建工具」直接进入同一弹窗的空白态。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List

from chayuan.server.config_panel.custom_tools_store import (
    CustomToolSpec,
    ParamSpec,
    delete_tool,
    list_tools,
    save_tool,
    validate_spec,
)
from chayuan.server.shared.http_test import run_request


logger = logging.getLogger("chayuan.config_panel.custom_tools")


def render_custom_tools_page(
    ui, *, mark_restart_needed: Callable[[], None] | None = None,
) -> None:
    state: Dict[str, Any] = {"tools": list_tools()}

    def _reload() -> None:
        state["tools"] = list_tools()
        grid.clear()
        with grid:
            _render_list(ui, state, mark_restart_needed, _reload)

    with ui.row().classes("items-center w-full q-mb-sm no-wrap").style("gap:8px"):
        ui.label(
            "自定义工具：把任意 HTTP 接口包装成 Agent 工具，LLM 可按 description 里的"
            "提示词调用。"
        ).classes("text-sm text-grey-7").style("flex:1;min-width:0")
        ui.button(
            "新建工具", icon="add",
            on_click=lambda: _open_editor(
                ui, None, mark_restart_needed, _reload,
            ),
        ).props("color=primary")
        ui.button(
            icon="refresh",
            on_click=lambda: (_reload(), ui.notify("已重新载入 custom_tools.yaml", color="info")),
        ).props("flat round dense").tooltip("重新读取 yaml")

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
    tools: List[CustomToolSpec] = state["tools"]
    if not tools:
        with ui.column().classes("w-full items-center q-pa-lg").style(
            "grid-column: 1 / -1"
        ):
            ui.icon("inventory_2").classes("text-grey-5").style("font-size:56px")
            ui.label("还没有自定义工具").classes("text-base text-grey-7 q-mt-sm")
            ui.label(
                "点右上角「新建工具」定义第一个；字段包括接口 URL、请求方式、参数 schema。"
            ).classes("text-xs text-grey-7")
        return

    for t in tools:
        _render_card(ui, t, mark_restart, refresh)


def _render_card(
    ui, spec: CustomToolSpec,
    mark_restart: Callable[[], None] | None, refresh: Callable[[], None],
) -> None:
    card = ui.card().classes("q-pa-none").style(
        "width:100%;min-height:200px;display:flex;flex-direction:column;overflow:hidden",
    )
    with card:
        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style(
            "gap:8px"
        ):
            ui.icon("extension").classes("text-primary").style("font-size:28px;flex:none")
            with ui.column().classes("q-gutter-none flex-1").style("min-width:0"):
                with ui.row().classes("items-center no-wrap").style("gap:6px"):
                    ui.label(spec.title or spec.name).classes("text-base font-semibold")
                    ui.badge(spec.method.upper()).props(
                        f"color={'positive' if spec.enabled else 'grey'}"
                    )
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
            param_text = (
                "  ".join(f"{p.name}:{p.type}" for p in spec.params[:3]) or "无参数"
            )
            ui.label(param_text).classes("text-xs text-grey-7").style(
                "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;"
                "flex:1;min-width:0"
            )
            ui.button(
                "测试", icon="play_circle",
                on_click=lambda s=spec: _open_test_dialog(ui, s),
            ).props("flat dense size=sm color=primary")
            ui.button(
                "编辑", icon="edit",
                on_click=lambda s=spec: _open_editor(ui, s, mark_restart, refresh),
            ).props("flat dense size=sm color=primary")
            ui.button(
                icon="delete_outline",
                on_click=lambda s=spec: _confirm_delete(
                    ui, s, mark_restart, refresh,
                ),
            ).props("flat dense size=sm color=negative").tooltip("删除")


# ---------------------------------------------------------------------------
# 编辑弹窗
# ---------------------------------------------------------------------------

def _open_editor(
    ui, spec: CustomToolSpec | None,
    mark_restart: Callable[[], None] | None, refresh: Callable[[], None],
) -> None:
    creating = spec is None
    if creating:
        spec = CustomToolSpec(name="", method="GET")

    ctx: Dict[str, Any] = {
        "inputs": {},           # {path: ui element}
        "params_rows": [],      # list of {name, type, desc, required, default, enum}
    }

    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(860px, 94vw); max-width: 94vw; "
        "max-height: 92vh; overflow: auto;"
    ):
        ui.label("新建自定义工具" if creating else f"编辑：{spec.title or spec.name}")\
            .classes("text-xl font-semibold")
        ui.label(
            "description 会同时作为 LLM 看到的工具说明；name 必须是合法 Python 标识符。"
        ).classes("text-xs text-grey-7 q-mb-sm")

        with ui.grid(columns=2).classes("w-full q-gutter-sm"):
            name_in = ui.input(label="name（唯一，作为工具 ID）", value=spec.name).props(
                "outlined dense"
            )
            title_in = ui.input(label="title（中文展示名）", value=spec.title).props(
                "outlined dense"
            )
            method_in = ui.select(
                ["GET", "POST", "PUT", "PATCH", "DELETE"],
                label="HTTP 方法", value=spec.method.upper(),
            ).props("outlined dense")
            timeout_in = ui.number(
                label="超时（秒）", value=spec.timeout, min=1, max=120, step=1,
            ).props("outlined dense")
            url_in = ui.input(
                label="URL（支持 {name} 占位）", value=spec.url,
            ).props("outlined dense").classes("col-span-2")
            enabled_in = ui.switch("启用", value=spec.enabled)

        desc_in = ui.textarea(
            label="description（LLM 提示词 / 工具说明）", value=spec.description,
        ).props("outlined dense autogrow input-style='min-height:5em'")\
            .classes("w-full q-mt-sm")

        # ---- 参数 schema ----
        ui.label("参数 schema").classes("text-sm font-semibold q-mt-md")
        ui.label(
            "LLM 会按下面的字段调用工具；URL 中 {name} 占位会被参数值填充。"
        ).classes("text-xs text-grey-7")

        params_container = ui.column().classes("w-full q-mt-xs")

        def _redraw_params():
            params_container.clear()
            with params_container:
                for i, row in enumerate(ctx["params_rows"]):
                    _render_param_row(ui, ctx, i, row, _redraw_params)

        def _add_param():
            ctx["params_rows"].append(_blank_param_row())
            _redraw_params()

        for p in spec.params:
            ctx["params_rows"].append({
                "name": p.name, "type": p.type, "description": p.description,
                "required": p.required, "default": p.default,
                "enum": ", ".join(map(str, p.enum)) if p.enum else "",
            })
        _redraw_params()

        with ui.row().classes("q-mt-xs"):
            ui.button("+ 添加参数", icon="add", on_click=_add_param).props(
                "flat dense color=primary"
            )

        # ---- headers & body_template ----
        ui.label("Headers（每行 Key: Value）").classes("text-sm font-semibold q-mt-md")
        headers_text = "\n".join(f"{k}: {v}" for k, v in (spec.headers or {}).items())
        headers_in = ui.textarea(value=headers_text).props(
            "outlined dense autogrow input-style='min-height:3em;font-family:monospace'"
        ).classes("w-full")

        ui.label("Body 模板（仅 POST/PUT/PATCH，支持 {name} 占位）").classes(
            "text-sm font-semibold q-mt-md"
        )
        body_in = ui.textarea(value=spec.body_template or "").props(
            "outlined dense autogrow input-style='min-height:4em;font-family:monospace'"
        ).classes("w-full")

        # ---- 测试区（内嵌） ----
        test_out_slot = ui.column().classes("w-full q-mt-md")

        def _gather_spec() -> CustomToolSpec:
            headers = {}
            for line in (headers_in.value or "").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    if k.strip():
                        headers[k.strip()] = v.strip()
            params: List[ParamSpec] = []
            for row in ctx["params_rows"]:
                enum_list = [
                    e.strip() for e in (row.get("enum") or "").split(",") if e.strip()
                ]
                params.append(ParamSpec(
                    name=str(row.get("name", "")).strip(),
                    type=str(row.get("type", "string") or "string"),
                    description=str(row.get("description", "") or ""),
                    required=bool(row.get("required", False)),
                    default=row.get("default"),
                    enum=enum_list,
                ))
            return CustomToolSpec(
                name=(name_in.value or "").strip(),
                title=(title_in.value or "").strip(),
                description=desc_in.value or "",
                method=method_in.value or "GET",
                url=(url_in.value or "").strip(),
                params=params,
                headers=headers,
                body_template=(body_in.value or None) or None,
                timeout=float(timeout_in.value or 15),
                enabled=bool(enabled_in.value),
            )

        def _do_test():
            s = _gather_spec()
            errs = validate_spec(s)
            if errs:
                ui.notify("；".join(errs), color="negative", multi_line=True)
                return
            # 构造样本参数：required 用占位 "?"；非 required 用 default（若有）
            sample: Dict[str, Any] = {}
            for p in s.params:
                if p.default is not None:
                    sample[p.name] = p.default
                elif p.required:
                    sample[p.name] = p.enum[0] if p.enum else (
                        "?" if p.type == "string" else 1 if p.type == "integer"
                        else 1.0 if p.type == "number" else True
                    )
            # 渲染 URL
            try:
                url = s.url.format(**sample)
            except Exception:  # noqa: BLE001
                url = s.url
            remaining = {k: v for k, v in sample.items() if "{" + k + "}" not in s.url}
            if s.method == "GET":
                res = run_request(
                    s.method, url,
                    params=remaining, headers=s.headers, timeout=s.timeout,
                )
            else:
                if s.body_template:
                    try:
                        body = json.loads(s.body_template.format(**sample))
                    except Exception:  # noqa: BLE001
                        body = s.body_template.format(**sample)
                else:
                    body = remaining
                res = run_request(
                    s.method, url,
                    json_body=body, headers=s.headers, timeout=s.timeout,
                )
            test_out_slot.clear()
            with test_out_slot:
                color = "positive" if res.ok else "negative"
                ui.label(
                    f"HTTP {res.status_code or 'ERR'}   耗时 {res.latency_ms}ms   "
                    f"CT={res.content_type or '-'}   body_kind={res.body_kind}"
                ).classes(f"text-sm text-{color}")
                if res.error:
                    ui.label(f"error: {res.error}").classes("text-sm text-negative")
                pretty = (
                    json.dumps(res.body, ensure_ascii=False, indent=2)
                    if res.body_kind == "json" else str(res.body or "")
                )
                ui.code(pretty, language="json" if res.body_kind == "json" else "text")\
                    .classes("w-full").style("max-height:320px;overflow:auto")

        def _do_save():
            s = _gather_spec()
            try:
                save_tool(s)
            except Exception as e:  # noqa: BLE001
                ui.notify(f"保存失败：{e}", color="negative", multi_line=True)
                return
            if mark_restart is not None:
                try:
                    mark_restart()
                except Exception:  # noqa: BLE001
                    pass
            ui.notify(
                f"已保存 {s.name}；启用需重启服务（或在工具目录页点立即重启）。",
                color="positive",
            )
            dlg.close()
            refresh()

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("取消", on_click=dlg.close).props("flat")
            ui.button("测试接口", icon="play_arrow", on_click=_do_test).props(
                "color=secondary"
            )
            ui.button("保存", icon="save", on_click=_do_save).props("color=primary")

    dlg.open()


def _blank_param_row() -> Dict[str, Any]:
    return {
        "name": "", "type": "string", "description": "",
        "required": False, "default": None, "enum": "",
    }


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
        ).props("outlined dense").style("width:130px")
        enum_el = ui.input(label="enum（逗号分隔）", value=row.get("enum", "")).props(
            "outlined dense"
        ).style("width:160px")

        def _sync(
            _=None, _row=row,
            _n=name_el, _t=type_el, _d=desc_el, _r=required_el,
            _df=default_el, _e=enum_el,
        ) -> None:
            _row["name"] = _n.value
            _row["type"] = _t.value
            _row["description"] = _d.value
            _row["required"] = bool(_r.value)
            _row["default"] = _df.value if _df.value != "" else None
            _row["enum"] = _e.value

        for el in (name_el, type_el, desc_el, required_el, default_el, enum_el):
            el.on_value_change(_sync)

        def _del(_=None, _i=idx):
            ctx["params_rows"].pop(_i)
            redraw()

        ui.button(icon="close", on_click=_del).props(
            "flat dense round size=sm color=negative"
        ).tooltip("删除该参数")


# ---------------------------------------------------------------------------
# 独立测试弹窗（从卡片「测试」按钮调起）
# ---------------------------------------------------------------------------

def _open_test_dialog(ui, spec: CustomToolSpec) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(680px, 92vw); max-width: 92vw"
    ):
        ui.label(f"测试：{spec.title or spec.name}").classes("text-lg font-semibold")
        ui.label(f"{spec.method}  {spec.url}").classes(
            "text-xs text-grey-8 font-mono q-mb-sm"
        )

        value_els: Dict[str, Any] = {}
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
                if p.type == "integer":
                    try:
                        kwargs[name] = int(raw)
                    except Exception:  # noqa: BLE001
                        ui.notify(f"{name} 需要整数", color="negative")
                        return
                elif p.type == "number":
                    try:
                        kwargs[name] = float(raw)
                    except Exception:  # noqa: BLE001
                        ui.notify(f"{name} 需要数字", color="negative")
                        return
                elif p.type == "boolean":
                    kwargs[name] = bool(raw)
                else:
                    kwargs[name] = raw

            try:
                url = spec.url.format(**kwargs)
            except Exception:  # noqa: BLE001
                url = spec.url
            remaining = {k: v for k, v in kwargs.items() if "{" + k + "}" not in spec.url}

            if spec.method == "GET":
                res = run_request(
                    spec.method, url,
                    params=remaining, headers=spec.headers, timeout=spec.timeout,
                )
            else:
                if spec.body_template:
                    try:
                        body = json.loads(spec.body_template.format(**kwargs))
                    except Exception:  # noqa: BLE001
                        body = spec.body_template.format(**kwargs)
                else:
                    body = remaining
                res = run_request(
                    spec.method, url,
                    json_body=body, headers=spec.headers, timeout=spec.timeout,
                )

            out_slot.clear()
            with out_slot:
                color = "positive" if res.ok else "negative"
                ui.label(
                    f"HTTP {res.status_code or 'ERR'}   耗时 {res.latency_ms}ms   "
                    f"CT={res.content_type or '-'}   body_kind={res.body_kind}"
                ).classes(f"text-sm text-{color}")
                if res.error:
                    ui.label(f"error: {res.error}").classes("text-sm text-negative")
                pretty = (
                    json.dumps(res.body, ensure_ascii=False, indent=2)
                    if res.body_kind == "json" else str(res.body or "")
                )
                ui.code(pretty, language="json" if res.body_kind == "json" else "text")\
                    .classes("w-full").style("max-height:320px;overflow:auto")

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("关闭", on_click=dlg.close).props("flat")
            ui.button("发起调用", icon="play_arrow", on_click=_run).props("color=primary")

    dlg.open()


# ---------------------------------------------------------------------------
# 删除确认
# ---------------------------------------------------------------------------

def _confirm_delete(
    ui, spec: CustomToolSpec,
    mark_restart: Callable[[], None] | None, refresh: Callable[[], None],
) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width:360px"):
        ui.label(f"确认删除 {spec.title or spec.name}？").classes(
            "text-base font-semibold"
        )
        ui.label("删除后该工具立即从对话界面消失（运行时会在下一次 get_tool() reload 重建注册表）。")\
            .classes("text-sm text-grey-8")
        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("取消", on_click=dlg.close).props("flat")

            def _do():
                if delete_tool(spec.name):
                    if mark_restart is not None:
                        try:
                            mark_restart()
                        except Exception:  # noqa: BLE001
                            pass
                    ui.notify(f"已删除 {spec.name}", color="positive")
                else:
                    ui.notify("未找到该工具", color="warning")
                dlg.close()
                refresh()

            ui.button("删除", color="negative", on_click=_do)
    dlg.open()
