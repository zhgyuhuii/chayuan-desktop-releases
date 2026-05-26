"""Config Panel · MCP 管理页。

面板侧统一管理 ``mcp_connection`` 与 ``mcp_profile`` 两张表：
- **通用配置 Profile**：超时、工作目录、环境变量（被所有连接共用作为默认值）；
- **连接列表 Connections**：每一项描述一个 MCP Server 的 stdio / sse 连接参数。

实现风格与 `kb_manage_page.py` / `users_page.py` 保持一致：
- 面板以 admin 身份直接调用 `chayuan.server.db.repository.mcp_connection_repository`；
- 不走 HTTP + JWT；
- 所有写操作二次确认；
- 表格化的布局 + 卡片化的详情，便于在一个页面看完所有连接状态。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.config_panel.mcp_manage")


_TRANSPORT_CHOICES: Dict[str, str] = {
    "stdio": "stdio（本地子进程）",
    "sse":   "sse（Server-Sent Events）",
}


# ---------------------------------------------------------------------------
# 数据访问封装
# ---------------------------------------------------------------------------

def _load_profile() -> Dict[str, Any]:
    from chayuan.server.db.repository.mcp_connection_repository import get_mcp_profile
    prof = get_mcp_profile()
    if prof is None:
        return {
            "timeout": 30,
            "working_dir": "/tmp",
            "env_vars": {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "PYTHONPATH": "/app",
                "HOME": "/tmp",
            },
            "update_time": "",
        }
    return {
        "timeout": int(prof.get("timeout") or 30),
        "working_dir": str(prof.get("working_dir") or "/tmp"),
        "env_vars": dict(prof.get("env_vars") or {}),
        "update_time": str(prof.get("update_time") or ""),
    }


def _save_profile(timeout: int, working_dir: str, env_vars: Dict[str, str]) -> None:
    from chayuan.server.db.repository.mcp_connection_repository import (
        create_mcp_profile, update_mcp_profile, get_mcp_profile,
    )
    if get_mcp_profile() is None:
        create_mcp_profile(
            timeout=int(timeout),
            working_dir=str(working_dir),
            env_vars=dict(env_vars or {}),
        )
    else:
        update_mcp_profile(
            timeout=int(timeout),
            working_dir=str(working_dir),
            env_vars=dict(env_vars or {}),
        )


def _reset_profile() -> bool:
    from chayuan.server.db.repository.mcp_connection_repository import (
        reset_mcp_profile, get_mcp_profile, create_mcp_profile,
    )
    if get_mcp_profile() is None:
        create_mcp_profile()
        return True
    return bool(reset_mcp_profile())


def _list_connections() -> List[Dict[str, Any]]:
    from chayuan.server.db.repository.mcp_connection_repository import (
        get_all_mcp_connections,
    )
    try:
        return list(get_all_mcp_connections() or [])
    except Exception:  # noqa: BLE001
        logger.exception("list mcp connections failed")
        return []


def _add_connection(**kw: Any) -> str:
    from chayuan.server.db.repository.mcp_connection_repository import (
        add_mcp_connection, get_mcp_connections_by_server_name,
    )
    name = str(kw.get("server_name") or "").strip()
    if not name:
        raise ValueError("server_name 不能为空")
    existing = get_mcp_connections_by_server_name(server_name=name)
    if existing:
        raise ValueError(f"服务器名称 '{name}' 已存在")
    return add_mcp_connection(
        server_name=name,
        args=list(kw.get("args") or []),
        env=dict(kw.get("env") or {}),
        cwd=str(kw.get("cwd") or "") or None,
        transport=str(kw.get("transport") or "stdio"),
        timeout=int(kw.get("timeout") or 30),
        enabled=bool(kw.get("enabled", True)),
        description=str(kw.get("description") or ""),
        config=dict(kw.get("config") or {}),
    )


def _update_connection(connection_id: str, **kw: Any) -> Optional[str]:
    from chayuan.server.db.repository.mcp_connection_repository import (
        update_mcp_connection,
    )
    # 只传非 None 的字段，保留原库的"部分更新"语义
    clean: Dict[str, Any] = {}
    for k in ("server_name", "args", "env", "cwd", "transport",
              "timeout", "enabled", "description", "config"):
        if k in kw and kw[k] is not None:
            clean[k] = kw[k]
    return update_mcp_connection(connection_id=connection_id, **clean)


def _delete_connection(connection_id: str) -> bool:
    from chayuan.server.db.repository.mcp_connection_repository import (
        delete_mcp_connection,
    )
    return bool(delete_mcp_connection(connection_id))


def _set_enabled(connection_id: str, enabled: bool) -> bool:
    from chayuan.server.db.repository.mcp_connection_repository import (
        enable_mcp_connection, disable_mcp_connection,
    )
    return bool(enable_mcp_connection(connection_id) if enabled
                else disable_mcp_connection(connection_id))


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render_mcp_manage_page(
    ui,
    *,
    mark_restart_needed: Optional[Callable[[], None]] = None,
) -> None:
    """渲染 MCP 管理页。"""
    ui.add_head_html(
        """
        <style>
        .mcp-card {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 12px 14px;
            display: flex; flex-direction: column; gap: 6px;
            transition: border-color 160ms ease, box-shadow 160ms ease;
        }
        .mcp-card:hover {
            border-color: #a5b4fc;
            box-shadow: 0 6px 18px rgba(79, 70, 229, 0.08);
        }
        .mcp-chip {
            display: inline-flex; align-items: center; padding: 2px 10px;
            border-radius: 999px; font-size: 11px; line-height: 1.5;
            font-weight: 600; white-space: nowrap;
        }
        .mcp-chip.on    { background: #dcfce7; color: #166534; }
        .mcp-chip.off   { background: #fee2e2; color: #991b1b; }
        .mcp-chip.stdio { background: #eef2ff; color: #4338ca; }
        .mcp-chip.sse   { background: #fef3c7; color: #92400e; }
        .mcp-kv-row {
            display: grid; grid-template-columns: 1fr 1fr auto;
            gap: 6px; align-items: center;
        }
        .mcp-cmd {
            background: #0b1020; color: #e5e7eb;
            padding: 6px 10px; border-radius: 6px;
            font-family: ui-monospace, Menlo, monospace;
            font-size: 12px; line-height: 1.45;
            word-break: break-all;
        }
        </style>
        """
    )

    state: Dict[str, Any] = {
        "connections": [],
        "search": "",
        "enabled_filter": "全部",
    }

    def _mark_changed() -> None:
        if mark_restart_needed is not None:
            try:
                mark_restart_needed()
            except Exception:  # noqa: BLE001
                pass

    # =============== 通用配置 Profile ===============================
    with ui.card().classes("w-full q-mb-md").props("flat bordered").style(
        "padding: 14px 16px; "
        "background: linear-gradient(180deg, #f9fafb 0%, #ffffff 100%);"
    ):
        with ui.row().classes("items-center w-full no-wrap").style("gap: 8px;"):
            ui.icon("settings_applications", size="22px").classes("text-indigo-7")
            ui.label("通用配置 (Profile)").classes("text-subtitle1").style(
                "font-weight: 600;"
            )
            ui.label(
                "这些默认值会应用到未单独设置的 MCP 连接上"
            ).classes("text-caption text-grey-7")
            ui.space()
            last_updated_label = ui.label("").classes(
                "text-caption text-grey-6 font-mono"
            )

        prof_form_row = ui.row().classes("w-full").style(
            "gap: 10px; margin-top: 8px;"
        )
        env_container = ui.column().classes("w-full").style("gap: 4px;")
        prof_action_row = ui.row().classes(
            "items-center w-full no-wrap q-mt-sm"
        ).style("gap: 8px;")

    profile_state: Dict[str, Any] = {
        "env_rows": [],   # List[{"key": str, "value": str, "row": ui.row}]
    }

    def _render_profile() -> None:
        prof_form_row.clear()
        env_container.clear()
        prof_action_row.clear()
        profile_state["env_rows"] = []

        prof = _load_profile()
        last_updated_label.set_text(
            f"上次更新：{prof.get('update_time') or '—'}"
        )

        with prof_form_row:
            timeout_input = ui.number(
                label="默认超时 timeout (秒)",
                value=prof["timeout"], min=5, max=600, step=1,
            ).props("dense outlined").classes("col-grow")
            timeout_input.tooltip(
                "新建 MCP 连接时若未单独配置 timeout，则使用此默认值。"
            )
            cwd_input = ui.input(
                label="默认工作目录 working_dir",
                value=prof["working_dir"],
                placeholder="/tmp",
            ).props("dense outlined").classes("col-grow")

        with env_container:
            with ui.row().classes("items-center w-full no-wrap q-mb-xs").style(
                "gap: 6px;"
            ):
                ui.icon("eco", size="18px").classes("text-grey-7")
                ui.label("环境变量 env_vars").classes("text-subtitle2").style(
                    "font-weight: 600;"
                )
                ui.label(
                    "(MCP 子进程继承的环境变量)"
                ).classes("text-caption text-grey-6")
                ui.space()
                ui.button(
                    "新增", icon="add",
                    on_click=lambda: _add_env_row("", ""),
                ).props("flat dense size=sm color=primary")

            env_rows_container = ui.column().classes("w-full").style(
                "gap: 4px;"
            )

            def _add_env_row(k: str, v: str) -> None:
                with env_rows_container:
                    with ui.row().classes(
                        "items-center w-full no-wrap mcp-kv-row"
                    ) as row:
                        key_el = ui.input(
                            label="Key", value=k,
                        ).props("dense outlined")
                        val_el = ui.input(
                            label="Value", value=v,
                        ).props("dense outlined")

                        def _rm():
                            try:
                                profile_state["env_rows"].remove(entry)
                            except ValueError:
                                pass
                            # NiceGUI: 通过 clear + 隐藏让该行从视觉上消失；
                            # `Element.delete()` 在部分 NiceGUI 版本不稳定，改用稳妥方案。
                            row.clear()
                            row.visible = False

                        ui.button(
                            icon="close", on_click=_rm,
                        ).props("flat round dense size=sm color=grey-7")

                    entry = {"key": key_el, "value": val_el, "row": row}
                    profile_state["env_rows"].append(entry)

            for k, v in sorted((prof["env_vars"] or {}).items()):
                _add_env_row(str(k), str(v))

        def _collect_env() -> Dict[str, str]:
            out: Dict[str, str] = {}
            for entry in profile_state["env_rows"]:
                k = str(entry["key"].value or "").strip()
                v = str(entry["value"].value or "")
                if k:
                    out[k] = v
            return out

        def _do_save_profile() -> None:
            try:
                _save_profile(
                    timeout=int(timeout_input.value or 30),
                    working_dir=str(cwd_input.value or "/tmp"),
                    env_vars=_collect_env(),
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("save mcp profile failed")
                ui.notify(
                    f"保存失败：{type(e).__name__}: {e}", type="negative",
                )
                return
            ui.notify("通用配置已保存", type="positive")
            _mark_changed()
            _render_profile()

        def _do_reset_profile() -> None:
            with ui.dialog() as d, ui.card().style("min-width: 360px;"):
                ui.label("重置为默认值？").classes(
                    "text-subtitle1 q-mb-xs"
                ).style("font-weight: 600;")
                ui.label(
                    "timeout=30、working_dir=/tmp、env_vars={PATH, PYTHONPATH, HOME}"
                ).classes("text-caption text-grey-7 q-mb-sm").style(
                    "line-height: 1.5;"
                )
                with ui.row().classes("w-full justify-end").style("gap: 6px;"):
                    ui.button("取消", on_click=d.close).props("flat dense")

                    def _ok() -> None:
                        try:
                            _reset_profile()
                        except Exception as e:  # noqa: BLE001
                            logger.exception("reset mcp profile failed")
                            ui.notify(
                                f"重置失败：{type(e).__name__}: {e}",
                                type="negative",
                            )
                            return
                        ui.notify("已重置", type="info")
                        d.close()
                        _render_profile()

                    ui.button(
                        "确认重置", icon="restart_alt", on_click=_ok,
                    ).props("unelevated dense color=warning")
            d.open()

        with prof_action_row:
            ui.label(
                "保存后立即生效；重启 MCP Server 进程后完全应用新的环境变量。"
            ).classes("text-caption text-grey-7")
            ui.space()
            ui.button(
                "重置默认值", icon="restart_alt", on_click=_do_reset_profile,
            ).props("flat dense color=grey-7")
            ui.button(
                "保存配置", icon="save", on_click=_do_save_profile,
            ).props("unelevated dense color=primary")

    _render_profile()

    # =============== 连接列表 Connections ===========================
    ui.separator().classes("q-my-md")
    with ui.row().classes("items-center w-full no-wrap q-mb-sm").style(
        "gap: 8px;"
    ):
        ui.icon("cable", size="22px").classes("text-indigo-7")
        ui.label("MCP 连接列表").classes("text-subtitle1").style(
            "font-weight: 600;"
        )
        ui.space()

        search = ui.input(
            placeholder="搜索服务器名 / 描述...",
        ).props("dense outlined clearable").classes("col-shrink").style(
            "max-width: 260px;"
        )

        enabled_toggle = ui.toggle(
            ["全部", "启用", "禁用"], value="全部",
        ).props("dense").classes("col-shrink")

        ui.button(
            "刷新", icon="refresh", on_click=lambda: _reload(),
        ).props("flat dense")

        ui.button(
            "新增连接", icon="add",
            on_click=lambda: _open_edit_dialog(None),
        ).props("color=primary unelevated dense")

    stats_row = ui.row().classes("items-center w-full q-mb-sm").style("gap: 8px;")
    grid_container = ui.grid(columns=2).classes("w-full").style("gap: 12px;")

    def _on_search(e) -> None:
        state["search"] = str(e.value or "")
        _render_grid()

    def _on_enabled_filter(e) -> None:
        state["enabled_filter"] = str(e.value or "全部") or "全部"
        _render_grid()

    search.on_value_change(_on_search)
    enabled_toggle.on_value_change(_on_enabled_filter)

    def _reload() -> None:
        state["connections"] = _list_connections()
        _render_stats()
        _render_grid()

    def _filtered() -> List[Dict[str, Any]]:
        q = (state["search"] or "").strip().lower()
        ef = state["enabled_filter"]
        out: List[Dict[str, Any]] = []
        for c in state["connections"]:
            en = bool(c.get("enabled"))
            if ef == "启用" and not en:
                continue
            if ef == "禁用" and en:
                continue
            if q:
                hay = " ".join([
                    str(c.get("server_name", "")),
                    str(c.get("description", "")),
                    str(c.get("transport", "")),
                ]).lower()
                if q not in hay:
                    continue
            out.append(c)
        return out

    def _render_stats() -> None:
        stats_row.clear()
        conns = state["connections"] or []
        total = len(conns)
        en = sum(1 for c in conns if c.get("enabled"))
        dis = total - en
        stdio = sum(1 for c in conns if c.get("transport") == "stdio")
        sse = sum(1 for c in conns if c.get("transport") == "sse")
        with stats_row:
            def _stat(icon: str, label: str, value: Any, color: str) -> None:
                ui.html(
                    f'<div style="display:flex;align-items:center;gap:8px;'
                    f'padding:6px 12px;background:#ffffff;border:1px solid #e5e7eb;'
                    f'border-radius:8px;">'
                    f'<span class="material-icons" style="color:{color};font-size:18px;">'
                    f'{icon}</span>'
                    f'<span style="font-size:12px;color:#6b7280;">{label}</span>'
                    f'<span style="font-weight:600;color:#111827;">{value}</span></div>'
                )

            _stat("cable", "连接总数", total, "#4338ca")
            _stat("check_circle", "启用中", en, "#16a34a")
            _stat("pause_circle", "已禁用", dis, "#dc2626")
            if stdio:
                _stat("terminal", "stdio", stdio, "#4338ca")
            if sse:
                _stat("wifi_tethering", "sse", sse, "#b45309")

    def _render_grid() -> None:
        grid_container.clear()
        rows = _filtered()
        if not rows:
            with grid_container:
                ui.label("没有匹配的连接。").classes(
                    "text-grey-7 q-pa-md"
                ).style("grid-column: span 2;")
            return
        with grid_container:
            for c in rows:
                _render_card(c)

    def _render_card(c: Dict[str, Any]) -> None:
        cid = str(c.get("id") or "")
        name = str(c.get("server_name") or "")
        transport = str(c.get("transport") or "stdio")
        enabled = bool(c.get("enabled"))
        description = str(c.get("description") or "")
        timeout = int(c.get("timeout") or 30)
        args: List[str] = list(c.get("args") or [])
        config: Dict[str, Any] = dict(c.get("config") or {})

        with ui.element("div").classes("mcp-card"):
            with ui.row().classes("items-center w-full no-wrap").style("gap: 8px;"):
                ui.icon(
                    "terminal" if transport == "stdio" else "wifi_tethering",
                    size="20px",
                ).classes(
                    "text-indigo-7" if transport == "stdio" else "text-amber-8"
                )
                ui.label(name).classes("text-subtitle1").style(
                    "font-weight: 600; flex: 1 1 auto; min-width: 0; "
                    "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                )
                ui.html(
                    f'<span class="mcp-chip {transport}">{transport}</span>'
                )
                ui.html(
                    '<span class="mcp-chip on">已启用</span>'
                    if enabled
                    else '<span class="mcp-chip off">已禁用</span>'
                )

            if description:
                ui.label(description).classes(
                    "text-body2 text-grey-8"
                ).style(
                    "display:-webkit-box;-webkit-line-clamp:2;"
                    "-webkit-box-orient:vertical;overflow:hidden;"
                )

            # 命令预览 / URL 预览
            preview = _format_preview(transport, config, args)
            if preview:
                ui.html(f'<div class="mcp-cmd">{_escape(preview)}</div>')

            # 元信息
            with ui.row().classes("items-center w-full").style(
                "gap: 12px; font-size: 11px; color:#6b7280;"
            ):
                ui.html(
                    f'<span><span class="material-icons" '
                    f'style="font-size:13px;vertical-align:-2px;">schedule</span> '
                    f'超时 {timeout}s</span>'
                )
                env_cnt = len((c.get("env") or {}))
                ui.html(
                    f'<span><span class="material-icons" '
                    f'style="font-size:13px;vertical-align:-2px;">eco</span> '
                    f'env {env_cnt}</span>'
                )
                ut = str(c.get("update_time") or "")
                if ut:
                    ui.html(
                        f'<span style="font-family:ui-monospace,Menlo,monospace;">'
                        f'{_escape(ut)}</span>'
                    )

            # 操作行
            with ui.row().classes("items-center w-full no-wrap").style(
                "gap: 4px;"
            ):
                def _toggle_enabled(e, conn_id=cid) -> None:
                    try:
                        _set_enabled(conn_id, bool(e.value))
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("toggle mcp enable failed")
                        ui.notify(
                            f"切换失败：{type(exc).__name__}: {exc}",
                            type="negative",
                        )
                        _reload()
                        return
                    ui.notify(
                        f"{name} 已{'启用' if e.value else '禁用'}",
                        type="positive" if e.value else "info",
                    )
                    _mark_changed()
                    _reload()

                ui.switch(
                    value=enabled, on_change=_toggle_enabled,
                ).props("dense").tooltip(
                    "启用 / 禁用此连接（影响后端自动加载）"
                )
                ui.space()
                ui.button(
                    "编辑", icon="edit",
                    on_click=lambda _=None, conn=c: _open_edit_dialog(conn),
                ).props("flat dense size=sm color=grey-8")
                ui.button(
                    "删除", icon="delete_outline",
                    on_click=lambda _=None, conn=c: _confirm_delete(conn),
                ).props("flat dense size=sm color=negative")

    # ----- 编辑 / 新增 对话框 --------------------------------------
    def _open_edit_dialog(existing: Optional[Dict[str, Any]]) -> None:
        is_new = existing is None
        data: Dict[str, Any] = {
            "server_name": "",
            "transport": "stdio",
            "timeout": 30,
            "enabled": True,
            "description": "",
            "args": [],
            "env": {},
            "cwd": "",
            "config": {},
        }
        if existing:
            data.update({
                "server_name": existing.get("server_name") or "",
                "transport": existing.get("transport") or "stdio",
                "timeout": int(existing.get("timeout") or 30),
                "enabled": bool(existing.get("enabled", True)),
                "description": existing.get("description") or "",
                "args": list(existing.get("args") or []),
                "env": dict(existing.get("env") or {}),
                "cwd": existing.get("cwd") or "",
                "config": dict(existing.get("config") or {}),
            })

        with ui.dialog() as dialog, ui.card().style(
            "min-width: 640px; max-width: 760px; max-height: 86vh; "
            "display:flex; flex-direction: column;"
        ):
            with ui.row().classes("items-center w-full no-wrap").style("gap: 8px;"):
                ui.icon(
                    "add_circle_outline" if is_new else "edit",
                    size="24px",
                ).classes("text-indigo-7")
                ui.label(
                    "新增 MCP 连接" if is_new else f"编辑：{data['server_name']}"
                ).classes("text-h6")
                ui.space()
                ui.button(icon="close", on_click=dialog.close).props(
                    "flat round dense"
                )

            ui.separator().classes("q-my-xs")

            body = ui.column().classes("w-full").style(
                "flex: 1 1 auto; overflow: auto; gap: 10px; padding-right: 4px;"
            )

            with body:
                # 基本字段
                with ui.grid(columns=2).classes("w-full").style("gap: 8px;"):
                    name_input = ui.input(
                        label="服务器名称 server_name",
                        value=data["server_name"],
                        placeholder="如：fs-local / github-remote",
                    ).props("dense outlined").classes("w-full")
                    if not is_new:
                        name_input.props("readonly=true")
                        name_input.tooltip("重命名会影响下游自动加载，此处不允许修改")

                    transport_sel = ui.select(
                        _TRANSPORT_CHOICES,
                        value=data["transport"],
                        label="传输 transport",
                    ).props("dense outlined").classes("w-full")

                    timeout_input = ui.number(
                        label="超时 timeout (秒)",
                        value=data["timeout"], min=1, max=600, step=1,
                    ).props("dense outlined").classes("w-full")

                    enabled_sel = ui.select(
                        {True: "启用 enabled=true", False: "禁用 enabled=false"},
                        value=data["enabled"],
                        label="启用状态",
                    ).props("dense outlined").classes("w-full")

                desc_input = ui.textarea(
                    label="描述 description（可选）",
                    value=data["description"],
                    placeholder="这条连接是做什么的？给未来的自己留个备忘。",
                ).props("dense outlined autogrow").classes("w-full")

                # 传输相关：command / args / cwd / config
                ui.label("传输参数").classes(
                    "text-subtitle2 q-mt-sm"
                ).style("font-weight: 600; color:#374151;")

                cmd_input = ui.input(
                    label="command（可执行文件 / 解释器路径）",
                    value=str(data["config"].get("command") or ""),
                    placeholder="stdio 必填，如 python / uvx / npx",
                ).props("dense outlined").classes("w-full")

                url_input = ui.input(
                    label="url（sse 端点）",
                    value=str(data["config"].get("url") or ""),
                    placeholder="http://host:port/sse",
                ).props("dense outlined").classes("w-full")

                # args 列表（每行一个参数）
                with ui.row().classes(
                    "items-center w-full no-wrap q-mt-xs"
                ).style("gap: 6px;"):
                    ui.icon("list_alt", size="16px").classes("text-grey-7")
                    ui.label("args（命令参数，每行一条）").classes(
                        "text-caption text-grey-7"
                    )
                args_text = ui.textarea(
                    value="\n".join([str(a) for a in data["args"]]),
                    placeholder="-m\nfastmcp\nrun",
                ).props("dense outlined").classes("w-full").style(
                    "min-height: 84px;"
                )

                cwd_input = ui.input(
                    label="工作目录 cwd（可选；留空使用通用配置）",
                    value=data["cwd"] or "",
                    placeholder="/path/to/working/dir",
                ).props("dense outlined").classes("w-full")

                # env 编辑器
                with ui.row().classes(
                    "items-center w-full no-wrap q-mt-sm"
                ).style("gap: 6px;"):
                    ui.icon("eco", size="16px").classes("text-grey-7")
                    ui.label("env（连接专属环境变量，会覆盖通用配置）").classes(
                        "text-caption text-grey-7"
                    )
                    ui.space()
                    add_env_btn = ui.button(
                        "新增", icon="add",
                    ).props("flat dense size=sm color=primary")

                env_rows_box = ui.column().classes("w-full").style("gap: 4px;")
                env_rows: List[Dict[str, Any]] = []

                def _add_env_row_dlg(k: str, v: str) -> None:
                    with env_rows_box:
                        with ui.row().classes(
                            "items-center w-full no-wrap mcp-kv-row"
                        ) as r:
                            ke = ui.input(label="Key", value=k).props(
                                "dense outlined"
                            )
                            ve = ui.input(label="Value", value=v).props(
                                "dense outlined"
                            )

                            def _rm() -> None:
                                try:
                                    env_rows.remove(entry)
                                except ValueError:
                                    pass
                                r.clear()
                                r.visible = False

                            ui.button(
                                icon="close", on_click=_rm,
                            ).props("flat round dense size=sm color=grey-7")

                        entry = {"key": ke, "value": ve, "row": r}
                        env_rows.append(entry)

                add_env_btn.on_click(lambda: _add_env_row_dlg("", ""))
                for k, v in sorted((data["env"] or {}).items()):
                    _add_env_row_dlg(str(k), str(v))

                # 额外 config (JSON)
                with ui.expansion(
                    "高级：其它 config 字段（JSON）", icon="code",
                ).classes("w-full").props("dense"):
                    extras = {
                        k: v for k, v in (data["config"] or {}).items()
                        if k not in ("command", "url")
                    }
                    try:
                        cfg_default = json.dumps(
                            extras, ensure_ascii=False, indent=2,
                        ) if extras else ""
                    except Exception:  # noqa: BLE001
                        cfg_default = str(extras)
                    config_text = ui.textarea(
                        label="其它 config (JSON 对象)",
                        value=cfg_default,
                        placeholder='{"auth_token": "..."}',
                    ).props("dense outlined").classes("w-full").style(
                        "min-height: 120px; font-family: ui-monospace, Menlo, monospace;"
                    )
                    ui.label(
                        "最终 config = {command, url} + 此处 JSON；解析失败会拒绝保存。"
                    ).classes("text-caption text-grey-6")

            # 底部操作
            with ui.row().classes(
                "w-full justify-end q-mt-sm"
            ).style("gap: 6px; padding-top: 8px; border-top: 1px solid #e5e7eb;"):
                ui.button("取消", on_click=dialog.close).props("flat dense")

                def _do_save() -> None:
                    name = str(name_input.value or "").strip()
                    if not name:
                        ui.notify("server_name 不能为空", type="warning")
                        return
                    transport = str(transport_sel.value or "stdio")
                    raw_args = str(args_text.value or "")
                    args_list = [ln.strip() for ln in raw_args.splitlines()
                                 if ln.strip()]
                    env_dict: Dict[str, str] = {}
                    for e in env_rows:
                        k = str(e["key"].value or "").strip()
                        v = str(e["value"].value or "")
                        if k:
                            env_dict[k] = v

                    # 组装 config
                    cfg: Dict[str, Any] = {}
                    cmd = str(cmd_input.value or "").strip()
                    url = str(url_input.value or "").strip()
                    if cmd:
                        cfg["command"] = cmd
                    if url:
                        cfg["url"] = url
                    extra_text = str(config_text.value or "").strip()
                    if extra_text:
                        try:
                            extras = json.loads(extra_text)
                            if not isinstance(extras, dict):
                                raise ValueError("JSON 根节点必须是对象 {}")
                        except Exception as exc:  # noqa: BLE001
                            ui.notify(
                                f"config JSON 解析失败：{exc}",
                                type="negative",
                            )
                            return
                        cfg.update(extras)

                    if transport == "stdio" and not cmd:
                        ui.notify(
                            "stdio 传输需要 command 字段",
                            type="warning",
                        )
                        return
                    if transport == "sse" and not url:
                        ui.notify(
                            "sse 传输需要 url 字段",
                            type="warning",
                        )
                        return

                    payload = {
                        "server_name": name,
                        "transport": transport,
                        "timeout": int(timeout_input.value or 30),
                        "enabled": bool(enabled_sel.value),
                        "description": str(desc_input.value or ""),
                        "args": args_list,
                        "env": env_dict,
                        "cwd": str(cwd_input.value or "") or None,
                        "config": cfg,
                    }

                    try:
                        if is_new:
                            _add_connection(**payload)
                        else:
                            payload.pop("server_name", None)  # 不允许改名
                            _update_connection(
                                str(existing.get("id") or ""), **payload
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("save mcp connection failed")
                        ui.notify(
                            f"保存失败：{type(exc).__name__}: {exc}",
                            type="negative",
                        )
                        return
                    ui.notify(
                        f"已{'创建' if is_new else '更新'} {name}",
                        type="positive",
                    )
                    _mark_changed()
                    dialog.close()
                    _reload()

                ui.button(
                    "保存", icon="save", on_click=_do_save,
                ).props("unelevated dense color=primary")

        dialog.open()

    def _confirm_delete(c: Dict[str, Any]) -> None:
        with ui.dialog() as d, ui.card().style("min-width: 380px;"):
            ui.label(f"删除连接「{c.get('server_name')}」？").classes(
                "text-subtitle1 q-mb-xs"
            ).style("font-weight: 600;")
            ui.label(
                "此操作会立即从数据库中移除该条连接；下游进程下次加载时将不再启动。"
            ).classes("text-caption text-grey-7 q-mb-sm").style(
                "line-height: 1.5;"
            )

            def _do() -> None:
                try:
                    _delete_connection(str(c.get("id") or ""))
                except Exception as e:  # noqa: BLE001
                    logger.exception("delete mcp connection failed")
                    ui.notify(
                        f"删除失败：{type(e).__name__}: {e}",
                        type="negative",
                    )
                    return
                ui.notify("已删除", type="info")
                _mark_changed()
                d.close()
                _reload()

            with ui.row().classes("w-full justify-end").style("gap: 6px;"):
                ui.button("取消", on_click=d.close).props("flat dense")
                ui.button(
                    "确认删除", icon="delete_outline", on_click=_do,
                ).props("unelevated dense color=negative")
        d.open()

    # ---- 启动 -------------------------------------------------------
    _reload()


def _format_preview(
    transport: str, config: Dict[str, Any], args: List[str],
) -> str:
    if transport == "sse":
        return str(config.get("url") or "")
    cmd = str(config.get("command") or "")
    if not cmd and not args:
        return ""
    parts = [cmd] if cmd else []
    parts.extend(str(a) for a in args)
    return " ".join(parts)


def _escape(s: str) -> str:
    return (
        str(s).replace("&", "&amp;")
              .replace("<", "&lt;")
              .replace(">", "&gt;")
              .replace('"', "&quot;")
    )


__all__ = ["render_mcp_manage_page"]
