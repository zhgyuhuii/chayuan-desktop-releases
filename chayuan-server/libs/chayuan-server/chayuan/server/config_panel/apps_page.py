"""「开放平台 · App 管理」配置页。

对标微信开放平台 / 飞书开发者后台：
- 顶部「创建应用」按钮：填 name / callback_url / scopes / callback_events，
  点确认后自动生成 ``app_id`` + ``app_secret``；
- 列表：每张卡展示完整 AppKey / AppSecret（secret 默认隐藏，可显示/复制）/ 名称 / scopes / 启用状态 / 创建时间；
- 卡上操作：编辑 / 轮换 secret（再次明文一次）/ 删除 / 回调测试；
- 回调测试：向该 App 的 ``callback_url`` 投递一个签名 ping，直接显示投递结果，
  方便对接方在自己服务端调通签名验证。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

from chayuan.server.config_panel.apps_store import (
    ALLOWED_EVENTS,
    AppSpec,
    create_app,
    delete_app,
    list_apps,
    rotate_secret,
    update_app,
)
from chayuan.server.shared.callback_dispatcher import dispatch_event
from chayuan.server.shared.scopes import SCOPE_GROUPS


logger = logging.getLogger("chayuan.config_panel.apps")


def render_apps_page(ui) -> None:
    state: Dict[str, Any] = {"apps": list_apps()}

    def _reload():
        state["apps"] = list_apps()
        grid.clear()
        with grid:
            _render_list(ui, state, _reload)

    with ui.row().classes("items-center w-full q-mb-sm no-wrap").style("gap:8px"):
        ui.label(
            "App 开放平台：对外暴露签名 OpenAPI（/openapi/v1/*），外部系统用 "
            "app_id / secret 发起调用；也可注册回调地址接收事件推送。"
        ).classes("text-sm text-grey-7").style("flex:1;min-width:0")
        ui.button(
            "创建应用", icon="add_business",
            on_click=lambda: _open_create_dialog(ui, _reload),
        ).props("color=primary")
        ui.button(
            icon="refresh",
            on_click=lambda: (_reload(), ui.notify("已重新载入 apps.yaml", color="info")),
        ).props("flat round dense").tooltip("重新读取 yaml")

    grid = ui.element("div").classes("w-full").style(
        "display:grid;"
        "grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));"
        "gap:12px;"
    )
    with grid:
        _render_list(ui, state, _reload)


# ---------------------------------------------------------------------------
# 列表 / 卡片
# ---------------------------------------------------------------------------

def _render_list(ui, state: Dict[str, Any], refresh: Callable[[], None]) -> None:
    apps: List[AppSpec] = state["apps"]
    if not apps:
        with ui.column().classes("w-full items-center q-pa-lg").style(
            "grid-column: 1 / -1"
        ):
            ui.icon("business").classes("text-grey-5").style("font-size:56px")
            ui.label("还没有应用").classes("text-base text-grey-7 q-mt-sm")
            ui.label("点右上角「创建应用」注册第一个对接方。")\
                .classes("text-xs text-grey-7")
        return
    for a in apps:
        _render_card(ui, a, refresh)


def _render_card(ui, app: AppSpec, refresh: Callable[[], None]) -> None:
    card = ui.card().classes("q-pa-none").style(
        "width:100%;min-height:260px;display:flex;flex-direction:column;overflow:hidden"
    )
    with card:
        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style(
            "gap:8px"
        ):
            ui.icon("business_center").classes("text-primary").style(
                "font-size:28px;flex:none"
            )
            with ui.column().classes("q-gutter-none flex-1").style("min-width:0"):
                ui.label(app.name or "(未命名)").classes("text-base font-semibold")
                ui.label(app.app_id).classes("text-xs text-grey-8 font-mono").style(
                    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
                )
            if app.enabled:
                ui.badge("启用").props("color=positive")
            else:
                ui.badge("禁用").props("color=grey")

        with ui.element("div").classes("q-px-md").style(
            "flex:1 1 auto;min-height:0;font-size:12px;color:#666;line-height:1.5"
        ):
            _credential_row(ui, "AppKey (app_id)", app.app_id)
            _credential_row(ui, "AppSecret (app_secret)", app.app_secret, secret=True)
            ui.label(f"scopes: {', '.join(app.scopes) or '-'}")
            ui.label(_grant_summary(app))
            ui.label(f"callback_url: {app.callback_url or '-'}").style(
                "white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
            )
            ui.label(f"events: {', '.join(app.callback_events) or '-'}")
            ui.label(f"created: {app.created_at or '-'}")

        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style(
            "gap:6px;border-top:1px solid #eee"
        ):
            ui.button(
                "回调测试", icon="send",
                on_click=lambda a=app: _test_callback(ui, a),
            ).props("flat dense size=sm color=primary")
            ui.button(
                "编辑", icon="edit",
                on_click=lambda a=app: _open_edit_dialog(ui, a, refresh),
            ).props("flat dense size=sm color=primary")
            ui.button(
                "授权", icon="admin_panel_settings",
                on_click=lambda a=app: _open_authorize_dialog(ui, a, refresh),
            ).props("flat dense size=sm color=primary")
            ui.button(
                "轮换 secret", icon="key",
                on_click=lambda a=app: _rotate(ui, a, refresh),
            ).props("flat dense size=sm color=warning")
            ui.button(
                icon="delete_outline",
                on_click=lambda a=app: _confirm_delete(ui, a, refresh),
            ).props("flat dense size=sm color=negative").tooltip("删除")


def _copy_to_clipboard(ui, value: str, *, message: str = "已复制完整凭证") -> None:
    ui.run_javascript(f"navigator.clipboard.writeText({value!r})")
    ui.notify(message, color="info")


def _credential_row(
    ui,
    label: str,
    value: str,
    *,
    secret: bool = False,
) -> None:
    with ui.row().classes("items-center w-full q-mb-xs no-wrap").style("gap:6px"):
        ui.label(label).classes("text-sm font-semibold").style(
            "width:138px;flex:none"
        )
        code = ui.input(
            value=value,
            password=secret,
            password_toggle_button=secret,
        ).props("outlined dense readonly").classes("flex-1")
        code.style(
            "min-width:0;"
            "font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
        )
        ui.button(
            icon="content_copy",
            on_click=lambda v=value: _copy_to_clipboard(ui, v),
        ).props("flat round dense size=sm color=primary").tooltip("复制完整值")


def _grant_summary(app: AppSpec) -> str:
    try:
        from chayuan.server.auth.app_acl import list_grants_for_app
        doc_count = len(list_grants_for_app(app.app_id))
    except Exception:
        doc_count = 0
    ku_count = len(getattr(app, "ku_ids", None) or [])
    tool_count = len(getattr(app, "tool_ids", None) or [])
    return f"授权: 文档KB {doc_count} 个 / 智库 {ku_count} 个 / 工具 {tool_count} 个"


# ---------------------------------------------------------------------------
# 创建 / 编辑 / 轮换 / 删除
# ---------------------------------------------------------------------------

def _common_form(ui, spec: AppSpec | None):
    name_in = ui.input(
        label="应用名称", value=spec.name if spec else "",
    ).props("outlined dense").classes("w-full")
    callback_in = ui.input(
        label="回调 URL（可选）", value=spec.callback_url if spec else "",
        placeholder="https://your-domain/chayuan/callback",
    ).props("outlined dense").classes("w-full")

    # ---- 细粒度 scope 勾选：按分组展示 + 每组一个通配一键全选 ----
    ui.label("权限范围 scopes").classes("text-sm font-semibold q-mt-sm")
    ui.label(
        "按「资源：动作」二元组授权；同时勾 chat:read + chat:write 表示两个都给。"
        "勾「chat:*」等价于该组全部勾选。"
    ).classes("text-xs text-grey-7 q-mb-xs")
    existing = set((spec.scopes or []) if spec else ["chat:read"])
    scope_state: dict[str, Any] = {"selected": set(existing)}
    scope_checkboxes: dict[str, Any] = {}

    with ui.card().classes("w-full q-pa-sm q-mb-sm"):
        for group_name, group_scopes in SCOPE_GROUPS.items():
            wildcard = f"{group_name}:*"
            with ui.row().classes("items-center w-full no-wrap q-mb-xs").style("gap:8px"):
                ui.label(group_name).classes(
                    "text-sm font-semibold text-primary"
                ).style("width:70px")
                # 细粒度 checkbox
                for s in group_scopes:
                    cb = ui.checkbox(s, value=(s in existing) or (wildcard in existing))
                    scope_checkboxes[s] = cb

                    def _sync(e, _s=s):
                        if bool(e.value):
                            scope_state["selected"].add(_s)
                        else:
                            scope_state["selected"].discard(_s)

                    cb.on_value_change(_sync)
                # 该组通配
                wc_cb = ui.checkbox(wildcard, value=wildcard in existing)
                scope_checkboxes[wildcard] = wc_cb

                def _sync_wc(e, _g=group_name, _w=wildcard, _ss=group_scopes):
                    if bool(e.value):
                        scope_state["selected"].add(_w)
                        # 通配选中 → 细粒度一并打勾（仅 UI，保存以通配为准）
                        for s in _ss:
                            ckb = scope_checkboxes.get(s)
                            if ckb is not None:
                                ckb.value = True
                    else:
                        scope_state["selected"].discard(_w)

                wc_cb.on_value_change(_sync_wc)

        # 超管 *:*
        with ui.row().classes("items-center w-full no-wrap q-mt-xs").style("gap:8px"):
            ui.label("super").classes(
                "text-sm font-semibold text-negative"
            ).style("width:70px")
            star = ui.checkbox("*:*（超管，含所有 scope）", value="*" in existing or "*:*" in existing)

            def _sync_star(e):
                if bool(e.value):
                    scope_state["selected"].add("*:*")
                else:
                    scope_state["selected"].discard("*:*")

            star.on_value_change(_sync_star)

    events_sel = ui.select(
        list(ALLOWED_EVENTS), multiple=True, label="订阅的回调事件",
        value=list(spec.callback_events) if spec else [],
    ).props("outlined dense").classes("w-full")
    enabled_in = ui.switch("启用", value=spec.enabled if spec else True)
    return name_in, callback_in, scope_state, events_sel, enabled_in


def _open_create_dialog(ui, refresh: Callable[[], None]) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(560px, 92vw)"
    ):
        ui.label("创建应用").classes("text-xl font-semibold")
        ui.label("提交后会自动生成 AppKey (app_id) 与 AppSecret (app_secret)，支持完整复制。")\
            .classes("text-xs text-grey-7 q-mb-sm")

        name_in, cb_in, sc_state, ev_sel, enabled_in = _common_form(ui, None)

        def _do():
            if not (name_in.value or "").strip():
                ui.notify("请填应用名称", color="warning")
                return
            scopes = sorted(sc_state["selected"]) or ["chat:read"]
            spec = create_app(
                name=name_in.value,
                callback_url=cb_in.value or "",
                callback_events=list(ev_sel.value or []),
                scopes=scopes,
            )
            # 立即禁用开关反转就更新一下
            if not enabled_in.value:
                update_app(spec.app_id, enabled=False)
            dlg.close()
            _show_secret_once(ui, spec, context="已创建应用")
            refresh()

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("取消", on_click=dlg.close).props("flat")
            ui.button("创建", icon="check", on_click=_do).props("color=primary")
    dlg.open()


def _open_edit_dialog(
    ui, app: AppSpec, refresh: Callable[[], None],
) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(560px, 92vw)"
    ):
        ui.label(f"编辑：{app.name}").classes("text-xl font-semibold")
        ui.label(app.app_id).classes("text-xs text-grey-8 font-mono q-mb-sm")

        name_in, cb_in, sc_state, ev_sel, enabled_in = _common_form(ui, app)

        def _do():
            update_app(
                app.app_id,
                name=name_in.value or app.name,
                callback_url=cb_in.value or "",
                callback_events=list(ev_sel.value or []),
                scopes=sorted(sc_state["selected"]),
                enabled=bool(enabled_in.value),
            )
            ui.notify("已保存", color="positive")
            dlg.close()
            refresh()

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("取消", on_click=dlg.close).props("flat")
            ui.button("保存", icon="save", on_click=_do).props("color=primary")
    dlg.open()


def _open_authorize_dialog(ui, app: AppSpec, refresh: Callable[[], None]) -> None:
    doc_items, ku_items = _load_authorizable_knowledge()
    tool_items = _load_authorizable_tools()
    current_doc_roles = _load_doc_grant_roles(app.app_id)
    doc_state = dict(current_doc_roles)
    ku_state = set(getattr(app, "ku_ids", None) or [])
    tool_state = set(getattr(app, "tool_ids", None) or [])

    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width:min(760px,96vw);max-height:88vh;overflow:auto"
    ):
        ui.label(f"授权：{app.name or app.app_id}").classes("text-xl font-semibold")
        ui.label(
            "勾选后 App 只能搜索被授权的知识库/智库；工具调用也只允许白名单内的工具。"
        ).classes("text-xs text-grey-7 q-mb-sm")

        with ui.tabs().classes("w-full") as tabs:
            kb_tab = ui.tab("文档知识库")
            ku_tab = ui.tab("结构化/向量智库")
            tool_tab = ui.tab("工具")
        with ui.tab_panels(tabs, value=kb_tab).classes("w-full"):
            with ui.tab_panel(kb_tab):
                if not doc_items:
                    ui.label("暂无文档知识库").classes("text-grey-7")
                for item in doc_items:
                    name = item["name"]
                    with ui.row().classes("items-center w-full no-wrap q-mb-xs").style("gap:8px"):
                        cb = ui.checkbox(
                            item["label"],
                            value=name in doc_state,
                        ).classes("flex-1")
                        role_sel = ui.select(
                            {"reader": "只读(reader)", "editor": "可写(editor)"},
                            value=doc_state.get(name, "reader"),
                        ).props("dense outlined").style("width:150px")

                        def _sync_cb(e, _name=name, _role=role_sel):
                            if bool(e.value):
                                doc_state[_name] = _role.value or "reader"
                            else:
                                doc_state.pop(_name, None)

                        def _sync_role(e, _name=name, _cb=cb):
                            if bool(_cb.value):
                                doc_state[_name] = e.value or "reader"

                        cb.on_value_change(_sync_cb)
                        role_sel.on_value_change(_sync_role)
            with ui.tab_panel(ku_tab):
                if not ku_items:
                    ui.label("暂无结构化 / 图像 / 向量智库").classes("text-grey-7")
                for item in ku_items:
                    kid = item["ku_id"]
                    cb = ui.checkbox(
                        f"{item['display_name']} ({kid}, {item['kind']})",
                        value=kid in ku_state,
                    ).classes("w-full")

                    def _sync_ku(e, _kid=kid):
                        if bool(e.value):
                            ku_state.add(_kid)
                        else:
                            ku_state.discard(_kid)

                    cb.on_value_change(_sync_ku)
            with ui.tab_panel(tool_tab):
                if not tool_items:
                    ui.label("暂无可调用工具").classes("text-grey-7")
                ui.checkbox("全部工具 (*)", value="*" in tool_state).on_value_change(
                    lambda e: (tool_state.add("*") if bool(e.value) else tool_state.discard("*"))
                )
                for item in tool_items:
                    tid = item["name"]
                    cb = ui.checkbox(
                        f"{item['title']} ({tid})",
                        value=tid in tool_state,
                    ).classes("w-full")

                    def _sync_tool(e, _tid=tid):
                        if bool(e.value):
                            tool_state.add(_tid)
                        else:
                            tool_state.discard(_tid)

                    cb.on_value_change(_sync_tool)

        def _save():
            try:
                from chayuan.server.auth.app_acl import add_grant, remove_grant
                for old in set(current_doc_roles) - set(doc_state):
                    remove_grant(app.app_id, old)
                for name, role in doc_state.items():
                    add_grant(app.app_id, name, role=role or "reader")
                scopes = set(app.scopes or [])
                if doc_state or ku_state:
                    scopes.add("kb:read")
                if any(role == "editor" for role in doc_state.values()):
                    scopes.add("kb:write")
                if tool_state:
                    scopes.update({"tools:read", "tools:call"})
                update_app(
                    app.app_id,
                    scopes=sorted(scopes),
                    ku_ids=sorted(ku_state),
                    tool_ids=sorted(tool_state),
                )
                ui.notify("授权已保存", color="positive")
                dlg.close()
                refresh()
            except Exception as e:  # noqa: BLE001
                logger.exception("save app grants failed")
                ui.notify(f"授权保存失败: {e}", color="negative")

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("取消", on_click=dlg.close).props("flat")
            ui.button("保存授权", icon="save", on_click=_save).props("color=primary")
    dlg.open()


def _load_doc_grant_roles(app_id: str) -> Dict[str, str]:
    try:
        from chayuan.server.auth.app_acl import list_grants_for_app
        return {
            str(g.get("kb_name")): str(g.get("role") or "reader")
            for g in list_grants_for_app(app_id)
            if g.get("kb_name")
        }
    except Exception:
        return {}


def _load_authorizable_knowledge() -> tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    try:
        from chayuan.server.api_server.knowledge_universe_routes import list_universe_items
        items = list_universe_items(user={"id": 0, "role": "admin"}, include_vector=True)
    except Exception:
        items = []
    docs: List[Dict[str, str]] = []
    others: List[Dict[str, str]] = []
    for item in items:
        kind = str(item.get("kind") or "document")
        if kind == "document":
            name = str(item.get("name") or "")
            if not name:
                continue
            docs.append({
                "name": name,
                "label": f"{item.get('display_name') or name} ({name})",
            })
        else:
            kid = str(item.get("ku_id") or "")
            if not kid:
                continue
            others.append({
                "ku_id": kid,
                "display_name": str(item.get("display_name") or item.get("name") or kid),
                "kind": kind,
            })
    return docs, others


def _load_authorizable_tools() -> List[Dict[str, str]]:
    try:
        from chayuan.server.utils import get_tool, get_tool_config
        tools = get_tool()
        out = []
        for name, tool in (tools or {}).items():
            cfg = get_tool_config(name) or {}
            if cfg and not cfg.get("use", False):
                continue
            out.append({
                "name": str(name),
                "title": str(getattr(tool, "title", None) or name),
            })
        return sorted(out, key=lambda x: x["title"])
    except Exception:
        logger.exception("load tools for app grants failed")
        return []


def _rotate(ui, app: AppSpec, refresh: Callable[[], None]) -> None:
    with ui.dialog() as confirm, ui.card().classes("q-pa-md").style(
        "min-width:360px"
    ):
        ui.label(f"确认轮换 {app.name} 的 app_secret？").classes(
            "text-base font-semibold"
        )
        ui.label("旧 secret 将立即失效，所有已经部署到对接方的签名实现必须更新。")\
            .classes("text-sm text-negative")
        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("取消", on_click=confirm.close).props("flat")

            def _go():
                fresh = rotate_secret(app.app_id)
                confirm.close()
                if fresh:
                    _show_secret_once(ui, fresh, context="已轮换 app_secret")
                    refresh()
                else:
                    ui.notify("未找到该应用", color="warning")

            ui.button("确认轮换", color="warning", on_click=_go)
    confirm.open()


def _confirm_delete(ui, app: AppSpec, refresh: Callable[[], None]) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width:360px"):
        ui.label(f"确认删除 {app.name}？").classes("text-base font-semibold")
        ui.label("删除后对应 app_id 立即失效，无法恢复。").classes("text-sm text-grey-8")
        with ui.row().classes("w-full justify-end q-mt-sm"):
            ui.button("取消", on_click=dlg.close).props("flat")

            def _do():
                if delete_app(app.app_id):
                    ui.notify(f"已删除 {app.name}", color="positive")
                else:
                    ui.notify("未找到该应用", color="warning")
                dlg.close()
                refresh()

            ui.button("删除", color="negative", on_click=_do)
    dlg.open()


# ---------------------------------------------------------------------------
# 凭证回显（创建 / 轮换后重点提示保存）
# ---------------------------------------------------------------------------

def _show_secret_once(ui, app: AppSpec, *, context: str) -> None:
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(640px, 94vw)"
    ):
        ui.label(context).classes("text-xl font-semibold")
        ui.label("请复制保存完整 AppKey / AppSecret；AppSecret 默认隐藏，可点眼睛显示。")\
            .classes("text-sm text-negative q-mb-sm")

        _credential_row(ui, "AppKey (app_id)", app.app_id)
        _credential_row(ui, "AppSecret (app_secret)", app.app_secret, secret=True)

        ui.label(
            "对接方示例（伪代码）：\n"
            "  headers = {\n"
            "    'X-App-Id': app_id,\n"
            "    'X-Timestamp': str(int(time.time())),\n"
            "    'X-Sign': hmac_sha256(app_secret, ts + '\\n' + raw_body).hex(),\n"
            "  }\n"
            "  POST /openapi/v1/ping   （期望 200 + {ok:true}）"
        ).classes("text-xs text-grey-8 font-mono").style("white-space:pre-wrap")

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("我已保存", color="primary", on_click=dlg.close)
    dlg.open()


# ---------------------------------------------------------------------------
# 回调测试
# ---------------------------------------------------------------------------

def _test_callback(ui, app: AppSpec) -> None:
    if not app.callback_url:
        ui.notify("该应用未填 callback_url", color="warning")
        return

    # 暂存 events，保证事件命中
    original_events = list(app.callback_events)
    try:
        if "app.created" not in original_events:
            update_app(app.app_id, callback_events=original_events + ["app.created"])
        results = dispatch_event("app.created", {
            "test": True, "note": "这是一次面板发起的回调测试",
        })
    finally:
        update_app(app.app_id, callback_events=original_events)

    # 仅展示与当前 app 有关的那一条
    hit = next((r for r in results if r.get("app_id") == app.app_id), None)
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(520px, 92vw)"
    ):
        ui.label(f"回调测试：{app.name}").classes("text-lg font-semibold")
        ui.label(app.callback_url).classes("text-xs text-grey-8 font-mono q-mb-sm")
        if hit is None:
            ui.label("未发出请求（可能是事件订阅同步异常）")\
                .classes("text-negative")
        elif hit.get("ok"):
            ui.label(f"✅ 投递成功  HTTP {hit.get('status')}  "
                     f"attempt={hit.get('attempt')}").classes("text-positive")
        else:
            ui.label(f"❌ 投递失败  attempt={hit.get('attempt')}")\
                .classes("text-negative")
            ui.label(f"error: {hit.get('error')}").classes("text-sm font-mono")
        ui.label(
            "事件体格式：{\"event\":\"app.created\",\"ts\":...,\"data\":{...}}\n"
            "Header: X-App-Id / X-Timestamp / X-Sign （HMAC-SHA256 签名）"
        ).classes("text-xs text-grey-7 q-mt-sm").style("white-space:pre-wrap")
        with ui.row().classes("w-full justify-end"):
            ui.button("关闭", on_click=dlg.close).props("color=primary")
    dlg.open()
