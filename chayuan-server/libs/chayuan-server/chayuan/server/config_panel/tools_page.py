"""「工具配置」页（NiceGUI）。

设计要点（对齐用户需求）：
- 不再沿用通用 ``_render_file_page``：
    * 去掉「原始 YAML」tab；
    * 去掉页面级大标题；
- 卡片网格：``grid-template-columns: repeat(auto-fill, minmax(300px, 1fr))``
  每张卡固定 ``min-height: 200px``，每行根据视口宽度自适应；
- 卡片上点「设置」按钮弹出 ``ui.dialog`` 进行编辑（描述 + 参数），
  取代了旧版的「点卡片直接展开参数」布局；
- **描述可编辑**：卡片上的描述可在设置弹窗里修改，结果写回
  ``tool_settings.yaml/<key>._desc``（以下划线开头表示是面板元信息，
  不影响 ``get_tool_config(key)`` 的工具运行逻辑）；
- **目录化**：工具元数据（title / icon / summary / description / fields /
  defaults）收在 ``chayuan/data/tools_catalog.json`` 中（内置），用户可在
  ``CHAYUAN_ROOT/tools_catalog.override.json`` 追加或覆盖，模拟「从官网下载
  保存到本地」的流程；
- **添加工具**：顶部「添加工具」按钮打开弹窗，左上方搜索框按「标题 / 简介 /
  标签 / 分类」模糊过滤，候选卡片带 checkbox，点击「确认添加」后把默认值
  写入 ``tool_settings.yaml``（``use: false``），随后用户再进设置填参数。

``text2images.model`` 依然与 ``model_settings.yaml/MODEL_PLATFORMS`` 联动：
``widget == "model_select"`` 的字段动态查 ``get_config_models(model_type=...)``。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from chayuan.server.config_panel import yaml_store
from chayuan.server.tools.catalog import (
    ToolField,
    ToolSpec,
    bundled_catalog_path as _shared_bundled_path,
    load_catalog as _shared_load_catalog,
    override_catalog_path as _shared_override_path,
)
from chayuan.settings import CHAYUAN_ROOT

logger = logging.getLogger("chayuan.config_panel.tools")


_FILENAME = "tool_settings.yaml"
_DESC_OVERRIDE_KEY = "_desc"  # tool_settings.yaml 里描述覆盖字段


# ---------------------------------------------------------------------------
# 目录数据类与加载（已迁到 chayuan/server/tools/catalog.py 共享）
# 此处保留兼容别名,避免下方代码大改;新代码请直接 import 共享版本。
# ---------------------------------------------------------------------------

# 透传共享路径（NiceGUI 面板里仍按原名调用）
_bundled_catalog_path = _shared_bundled_path
_override_catalog_path = _shared_override_path
_load_catalog = _shared_load_catalog


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    """局部小工具：只在本文件用,共享版藏在 catalog._read_json 里不公开。"""
    try:
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        logger.exception("读取工具目录 JSON 失败：%s", path)
        return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def render_tool_settings_page(ui, *, mark_restart_needed: Callable[[], None] | None = None) -> None:
    """「工具配置」主页：目录卡片网格 + 设置弹窗 + 添加工具弹窗。"""
    specs, categories = _load_catalog()

    state: Dict[str, Any] = {
        "load_res": yaml_store.load_yaml(_FILENAME),
        "specs": specs,
        "categories": categories,
        "filter_text": "",
        "filter_cat": "ALL",
        "filter_status": "ALL",  # ALL / on / off / missing
    }

    # 顶部工具栏（搜索 + 分类 + 状态 + 添加/刷新）
    with ui.row().classes("items-center w-full q-mb-sm no-wrap").style("gap:8px"):
        search = ui.input(placeholder="搜索工具（标题 / 简介 / 标签）").props(
            "outlined dense clearable"
        ).classes("flex-1").style("max-width:320px")
        search.on_value_change(lambda e: (_set_filter(state, "filter_text", e.value),
                                          _redraw(ui, state, mark_restart_needed, list_slot)))

        cat_options = {"ALL": "全部分类"}
        cat_options.update(categories)
        cat_sel = ui.select(cat_options, value="ALL", label="分类").props(
            "outlined dense"
        ).style("min-width:160px")
        cat_sel.on_value_change(lambda e: (_set_filter(state, "filter_cat", e.value),
                                           _redraw(ui, state, mark_restart_needed, list_slot)))

        status_sel = ui.select(
            {"ALL": "全部状态", "on": "已启用", "off": "未启用", "missing": "未加入"},
            value="ALL", label="状态",
        ).props("outlined dense").style("min-width:140px")
        status_sel.on_value_change(
            lambda e: (_set_filter(state, "filter_status", e.value),
                       _redraw(ui, state, mark_restart_needed, list_slot))
        )

        ui.space()
        ui.button(
            "添加工具", icon="add_box",
            on_click=lambda: _open_add_dialog(ui, state, mark_restart_needed,
                                              lambda: _redraw(ui, state, mark_restart_needed, list_slot)),
        ).props("color=primary outline")
        ui.button(
            icon="refresh",
            on_click=lambda: (_reload_catalog(state),
                              _redraw(ui, state, mark_restart_needed, list_slot),
                              ui.notify("已从磁盘重新载入目录与 yaml。", color="info")),
        ).props("flat round dense").tooltip("重新载入 tools_catalog.json 与 tool_settings.yaml")

    # 卡片网格容器——使用 CSS Grid auto-fill 300px，每行自适应
    list_slot = ui.element("div").classes("w-full").style(
        "display:grid;"
        "grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));"
        "gap:12px;"
    )
    with list_slot:
        _render_cards(ui, state, mark_restart_needed, list_slot)


# ---------------------------------------------------------------------------
# 刷新 & 过滤
# ---------------------------------------------------------------------------

def _set_filter(state: Dict[str, Any], key: str, value: Any) -> None:
    state[key] = value if value is not None else ""


def _reload_catalog(state: Dict[str, Any]) -> None:
    state["specs"], state["categories"] = _load_catalog()
    state["load_res"] = yaml_store.load_yaml(_FILENAME)


def _redraw(ui, state: Dict[str, Any], mark_restart: Callable[[], None] | None, list_slot) -> None:
    # 每次重绘都重新读一次 yaml 以反映最新状态
    state["load_res"] = yaml_store.load_yaml(_FILENAME)
    list_slot.clear()
    with list_slot:
        _render_cards(ui, state, mark_restart, list_slot)


def _apply_filters(state: Dict[str, Any]) -> List[ToolSpec]:
    doc = state["load_res"].doc or {}
    text = (state.get("filter_text") or "").strip().lower()
    cat = state.get("filter_cat") or "ALL"
    status = state.get("filter_status") or "ALL"

    def _match_text(s: ToolSpec) -> bool:
        if not text:
            return True
        blob = " ".join([
            s.key, s.title, s.summary, s.description, " ".join(s.tags),
        ]).lower()
        return text in blob

    def _match_cat(s: ToolSpec) -> bool:
        return cat == "ALL" or s.category == cat

    def _match_status(s: ToolSpec) -> bool:
        present = _tool_present(doc, s.key)
        use_on = bool(_get_tool_value(doc, s.key, "use", default=False))
        if status == "ALL":
            return True
        if status == "on":
            return present and use_on
        if status == "off":
            return present and not use_on
        if status == "missing":
            return not present
        return True

    return [s for s in state["specs"] if _match_text(s) and _match_cat(s) and _match_status(s)]


# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------

def _render_cards(ui, state: Dict[str, Any], mark_restart: Callable[[], None] | None, list_slot) -> None:
    specs = _apply_filters(state)
    if not specs:
        with ui.column().classes("w-full items-center q-pa-lg").style("grid-column: 1 / -1"):
            ui.icon("inbox").classes("text-grey-5").style("font-size:48px")
            ui.label("没有匹配的工具。").classes("text-sm text-grey-7")
        return
    for spec in specs:
        _render_card(ui, spec, state, mark_restart,
                     lambda: _redraw(ui, state, mark_restart, list_slot))


def _render_card(
    ui,
    spec: ToolSpec,
    state: Dict[str, Any],
    mark_restart: Callable[[], None] | None,
    refresh: Callable[[], None],
) -> None:
    doc = state["load_res"].doc or {}
    present = _tool_present(doc, spec.key)
    use_on = bool(_get_tool_value(doc, spec.key, "use", default=False))
    effective_desc = _effective_description(doc, spec)

    card = ui.card().classes("q-pa-none").style(
        "width:100%;min-height:200px;display:flex;flex-direction:column;overflow:hidden"
    )
    with card:
        # --- 行 1：图标 + 标题 + 状态 switch（未加入时变 disabled） ---
        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style("gap:8px"):
            ui.icon(spec.icon).classes("text-primary").style("font-size:28px;flex:none")
            with ui.column().classes("q-gutter-none flex-1").style("min-width:0"):
                ui.label(spec.title).classes("text-base font-semibold").style(
                    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
                )
                cat_label = state["categories"].get(spec.category, spec.category)
                ui.label(cat_label).classes("text-xs text-grey-7").style(
                    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
                )
            if present:
                sw = ui.switch(value=use_on).tooltip(
                    "启用后会出现在对话界面「选择工具」下拉中"
                )

                def _on_toggle(e, _key=spec.key, _mark=mark_restart, _refresh=refresh) -> None:
                    try:
                        _, bak, changes = yaml_store.save_updates(
                            _FILENAME, {f"{_key}.use": bool(e.value)}
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("toggle %s.use failed", _key)
                        ui.notify(f"切换失败：{type(exc).__name__}: {exc}", color="negative")
                        return
                    if changes and _mark is not None:
                        try:
                            _mark()
                        except Exception:  # noqa: BLE001
                            logger.exception("mark_restart_needed failed")
                    bak_note = f"；备份：{bak.name}" if bak else ""
                    ui.notify(
                        f"{'已启用' if e.value else '已禁用'} {spec.title}{bak_note}",
                        color="positive" if changes else "info",
                    )
                    _refresh()
                sw.on_value_change(_on_toggle)
            else:
                ui.badge("未加入").props("color=grey outline")

        # --- 行 2：可编辑描述（只读显示；-webkit-line-clamp 限制 3 行） ---
        with ui.element("div").classes("q-px-md").style(
            "flex:1 1 auto;min-height:0;overflow:hidden;"
        ):
            ui.label(effective_desc or "（暂无描述）").style(
                "font-size:12px;color:#555;line-height:1.45;"
                "display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;"
                "overflow:hidden;text-overflow:ellipsis;"
            ).tooltip(effective_desc)

        # --- 行 3：标签 chip + 设置 / 官网 ---
        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style(
            "gap:6px;border-top:1px solid #eee"
        ):
            tag_text = "  ".join(f"#{t}" for t in spec.tags[:3]) if spec.tags else ""
            if tag_text:
                ui.label(tag_text).classes("text-xs text-grey-7").style(
                    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0"
                )
            else:
                ui.space()
            if spec.docs_url:
                ui.link("官网", spec.docs_url, new_tab=True).classes("text-xs")
            ui.button(
                "设置", icon="settings",
                on_click=lambda s=spec: _open_settings_dialog(
                    ui, s, state, mark_restart, refresh
                ),
            ).props(
                "flat dense color=primary size=sm"
                if present else "outline dense color=primary size=sm"
            )


def _effective_description(doc: Any, spec: ToolSpec) -> str:
    """优先返回 yaml 里用户编辑过的描述（`<key>._desc`），否则回落到目录原描述。"""
    if not isinstance(doc, dict):
        return spec.description
    node = doc.get(spec.key)
    if isinstance(node, dict):
        val = node.get(_DESC_OVERRIDE_KEY)
        if isinstance(val, str) and val.strip():
            return val
    return spec.description


# ---------------------------------------------------------------------------
# 设置弹窗
# ---------------------------------------------------------------------------

def _open_settings_dialog(
    ui,
    spec: ToolSpec,
    state: Dict[str, Any],
    mark_restart: Callable[[], None] | None,
    refresh: Callable[[], None],
) -> None:
    doc = state["load_res"].doc or {}
    present = _tool_present(doc, spec.key)
    use_on = bool(_get_tool_value(doc, spec.key, "use", default=False)) if present else False
    current_desc = _effective_description(doc, spec)

    bound: Dict[str, Tuple[ToolField, Any]] = {}

    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(720px, 92vw); max-width: 92vw; "
        "max-height: 92vh; overflow: auto;"
    ):
        # 头部
        with ui.row().classes("items-center w-full q-mb-md no-wrap").style("gap:12px"):
            ui.icon(spec.icon).classes("text-primary").style("font-size:30px;flex:none")
            with ui.column().classes("q-gutter-none flex-1").style("min-width:0"):
                ui.label(spec.title).classes("text-xl font-semibold")
                meta_parts = [state["categories"].get(spec.category, spec.category)]
                if spec.tags:
                    meta_parts.append("  ".join(f"#{t}" for t in spec.tags))
                ui.label("  ·  ".join(p for p in meta_parts if p)).classes(
                    "text-xs text-grey-7"
                )
            if spec.docs_url:
                ui.link("官网 ↗", spec.docs_url, new_tab=True).classes("text-sm")
            ui.button(icon="close", on_click=dlg.close).props(
                "flat round dense"
            ).tooltip("关闭")

        # 启用开关（设置弹窗内也可切换 use）
        use_switch = ui.switch("启用该工具", value=use_on).tooltip(
            "启用后会出现在对话界面「选择工具」下拉中"
        )
        bound[f"{spec.key}.use"] = (ToolField("use", "启用", "switch"), use_switch)

        # 描述（可编辑，保存到 yaml 的 _desc 字段）
        with ui.card().classes("w-full q-pa-sm q-mt-sm").style("border-left:3px solid #1976D2"):
            with ui.row().classes("items-center w-full no-wrap q-mb-xs"):
                ui.icon("description").classes("text-primary")
                ui.label("工具描述（展示在卡片上，可按需改写）").classes(
                    "text-sm font-semibold"
                )
            desc_input = ui.textarea(value=current_desc).props(
                "outlined dense autogrow input-style='min-height:4em'"
            ).classes("w-full")
            bound[f"{spec.key}.{_DESC_OVERRIDE_KEY}"] = (
                ToolField(_DESC_OVERRIDE_KEY, "描述", "textarea"),
                desc_input,
            )
            with ui.row().classes("q-mt-xs"):
                ui.button(
                    "恢复默认描述", icon="restart_alt",
                    on_click=lambda: (setattr(desc_input, "value", spec.description),
                                      ui.notify("已恢复为目录原描述（未保存）。", color="info")),
                ).props("flat dense size=sm")

        # 参数区
        if spec.fields:
            ui.label("参数设置").classes("text-sm font-semibold q-mt-md q-mb-xs")
            with ui.grid(columns=2).classes("w-full q-gutter-sm"):
                for fld in spec.fields:
                    abs_path = f"{spec.key}.{fld.path}"
                    current = _get_tool_value(doc, spec.key, fld.path, default=fld.default)
                    el = _build_tool_input(ui, fld, current)
                    bound[abs_path] = (fld, el)
        else:
            ui.label("该工具无需额外参数。").classes("text-sm text-grey-7 q-mt-sm")

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("取消", on_click=dlg.close).props("flat")
            if present:
                ui.button(
                    "移除该工具", icon="delete_outline",
                    on_click=lambda: _remove_tool(ui, spec, mark_restart, refresh, dlg),
                ).props("flat color=negative")
            ui.button(
                "保存", icon="save",
                on_click=lambda: _save_dialog(ui, spec, bound, mark_restart,
                                              state, refresh, dlg),
            ).props("color=primary")

    dlg.open()


def _save_dialog(
    ui,
    spec: ToolSpec,
    bound: Dict[str, Tuple[ToolField, Any]],
    mark_restart: Callable[[], None] | None,
    state: Dict[str, Any],
    refresh: Callable[[], None],
    dlg,
) -> None:
    updates: Dict[str, Any] = {}
    try:
        for abs_path, (fld, el) in bound.items():
            raw = getattr(el, "value", None)
            widget = fld.widget
            # model_select 实质是个带动态选项的字符串字段
            if widget == "model_select":
                widget = "text"
            updates[abs_path] = yaml_store.coerce_for_widget(widget, raw)
    except ValueError as e:
        ui.notify(f"字段校验失败：{e}", color="negative")
        return

    # 若描述被用户改回与目录原描述一致，就把 _desc 清掉（None→删除效果需要特殊处理，
    # 此处设置空字符串即可：_effective_description 会把空字符串视为未覆盖，回落到
    # spec.description）
    desc_key = f"{spec.key}.{_DESC_OVERRIDE_KEY}"
    if updates.get(desc_key, "").strip() in ("", spec.description.strip()):
        updates[desc_key] = ""

    try:
        path, bak, changes = yaml_store.save_updates(_FILENAME, updates)
    except Exception as e:  # noqa: BLE001
        logger.exception("save tool %s failed", spec.key)
        ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
        return

    if changes and mark_restart is not None:
        try:
            mark_restart()
        except Exception:  # noqa: BLE001
            logger.exception("mark_restart_needed failed")

    if not changes:
        ui.notify("没有变更需要保存。", color="info")
    else:
        bak_note = f"；备份：{bak.name}" if bak else ""
        ui.notify(
            f"已保存 {spec.title}：{len(changes)} 项（会在重启后完全生效）{bak_note}",
            color="positive",
        )
    dlg.close()
    refresh()


def _remove_tool(
    ui,
    spec: ToolSpec,
    mark_restart: Callable[[], None] | None,
    refresh: Callable[[], None],
    dlg,
) -> None:
    """从 yaml 中删除该工具 mapping；下次需要时可再次「添加工具」恢复默认。"""
    try:
        res = yaml_store.load_yaml(_FILENAME)
        doc = res.doc if res.exists else {}
        if isinstance(doc, dict) and spec.key in doc:
            del doc[spec.key]
            from chayuan.pydantic_settings_file import import_yaml

            bak = res.path.with_suffix(res.path.suffix + ".bak")
            try:
                import shutil as _sh
                if res.path.is_file():
                    _sh.copy2(res.path, bak)
            except Exception:  # noqa: BLE001
                bak = None
            with open(res.path, "w", encoding="utf-8") as f:
                import_yaml().dump(doc, f)
            if mark_restart is not None:
                try:
                    mark_restart()
                except Exception:  # noqa: BLE001
                    logger.exception("mark_restart_needed failed")
            ui.notify(
                f"已从 tool_settings.yaml 移除 {spec.title}"
                f"{'；备份：' + bak.name if bak else ''}",
                color="positive",
            )
        else:
            ui.notify("该工具未在 yaml 中定义。", color="info")
    except Exception as e:  # noqa: BLE001
        logger.exception("remove tool %s failed", spec.key)
        ui.notify(f"移除失败：{type(e).__name__}: {e}", color="negative")
        return
    dlg.close()
    refresh()


# ---------------------------------------------------------------------------
# 「添加工具」弹窗
# ---------------------------------------------------------------------------

def _open_add_dialog(
    ui,
    state: Dict[str, Any],
    mark_restart: Callable[[], None] | None,
    refresh: Callable[[], None],
) -> None:
    doc = state["load_res"].doc or {}
    missing = [s for s in state["specs"] if not _tool_present(doc, s.key)]

    if not missing:
        with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width:360px"):
            ui.label("已没有可添加的新工具").classes("text-base font-semibold")
            ui.label(
                "当前 tool_settings.yaml 已覆盖目录里的全部工具。"
                "想要扩展，可把从官网下载的 JSON 放到 "
                f"{_override_catalog_path().name}，或在「设置」里修改现有工具。"
            ).classes("text-sm text-grey-8 q-mb-md")
            ui.button("知道了", on_click=dlg.close).props("color=primary")
        dlg.open()
        return

    sel_state: Dict[str, Any] = {
        "selected": set(),
        "text": "",
        "cat": "ALL",
    }

    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style(
        "min-width: min(880px, 94vw); max-width: 94vw; "
        "max-height: 92vh; overflow: auto;"
    ):
        with ui.row().classes("items-center w-full q-mb-sm no-wrap"):
            ui.icon("widgets").classes("text-primary").style("font-size:28px;flex:none")
            with ui.column().classes("q-gutter-none flex-1"):
                ui.label("添加工具").classes("text-xl font-semibold")
                ui.label(
                    f"目录来源：内置 + {_override_catalog_path().name}（如存在）。"
                    "勾选后保存，会把工具以 use=false 写入 yaml，再到主列表里「设置」填参数并启用。"
                ).classes("text-xs text-grey-7")
            ui.button(icon="close", on_click=dlg.close).props(
                "flat round dense"
            ).tooltip("关闭")

        cat_options = {"ALL": "全部分类"}
        cat_options.update(state["categories"])

        with ui.row().classes("items-center w-full q-mb-sm no-wrap").style("gap:8px"):
            search = ui.input(placeholder="搜索（标题 / 描述 / 标签）").props(
                "outlined dense clearable"
            ).classes("flex-1")
            cat_sel = ui.select(cat_options, value="ALL", label="分类").props(
                "outlined dense"
            ).style("min-width:160px")
            refresh_btn = ui.button(
                icon="cloud_download",
                on_click=lambda: _ui_catalog_reload_hint(ui),
            ).props("flat round dense").tooltip(
                "从磁盘重新加载目录 JSON。想要「从官网下载更新目录」，请把 JSON 保存到 "
                + str(_override_catalog_path())
            )
            _ = refresh_btn  # 占位，避免未使用告警

        grid = ui.element("div").classes("w-full q-mt-sm").style(
            "display:grid;"
            "grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));"
            "gap:10px;"
        )

        def _redraw_add_grid() -> None:
            text = (sel_state["text"] or "").strip().lower()
            cat = sel_state["cat"] or "ALL"
            grid.clear()
            found = 0
            with grid:
                for spec in missing:
                    if cat != "ALL" and spec.category != cat:
                        continue
                    if text:
                        blob = " ".join([
                            spec.key, spec.title, spec.summary, spec.description,
                            " ".join(spec.tags),
                        ]).lower()
                        if text not in blob:
                            continue
                    found += 1
                    _render_candidate_card(ui, spec, state, sel_state)
                if found == 0:
                    with ui.column().classes("w-full items-center q-pa-md").style(
                        "grid-column: 1 / -1"
                    ):
                        ui.icon("search_off").classes("text-grey-5").style(
                            "font-size:40px"
                        )
                        ui.label("没有匹配的工具。").classes("text-sm text-grey-7")
                        ui.label(
                            "若想接入更多工具，可把 JSON 追加到 "
                            + str(_override_catalog_path())
                        ).classes("text-xs text-grey-7")

        search.on_value_change(
            lambda e: (sel_state.__setitem__("text", e.value or ""), _redraw_add_grid())
        )
        cat_sel.on_value_change(
            lambda e: (sel_state.__setitem__("cat", e.value or "ALL"), _redraw_add_grid())
        )

        _redraw_add_grid()

        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("取消", on_click=dlg.close).props("flat")
            ui.button(
                "确认添加", icon="add",
                on_click=lambda: _do_add(
                    ui, state, sel_state, missing, mark_restart, refresh, dlg
                ),
            ).props("color=primary")

    dlg.open()


def _render_candidate_card(
    ui,
    spec: ToolSpec,
    state: Dict[str, Any],
    sel_state: Dict[str, Any],
) -> None:
    """在「添加工具」弹窗里渲染一张候选卡，带 checkbox。"""
    card = ui.card().classes("q-pa-none").style(
        "width:100%;min-height:200px;display:flex;flex-direction:column;overflow:hidden;"
    )
    with card:
        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style("gap:8px"):
            cb = ui.checkbox(value=spec.key in sel_state["selected"])
            ui.icon(spec.icon).classes("text-primary").style("font-size:26px;flex:none")
            with ui.column().classes("q-gutter-none flex-1").style("min-width:0"):
                ui.label(spec.title).classes("text-sm font-semibold").style(
                    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis"
                )
                cat_label = state["categories"].get(spec.category, spec.category)
                ui.label(cat_label).classes("text-xs text-grey-7")

        with ui.element("div").classes("q-px-md").style(
            "flex:1 1 auto;min-height:0;overflow:hidden;"
        ):
            ui.label(spec.description).style(
                "font-size:12px;color:#555;line-height:1.45;"
                "display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;"
                "overflow:hidden;text-overflow:ellipsis;"
            ).tooltip(spec.description)

        with ui.row().classes("items-center w-full q-px-md q-py-sm no-wrap").style(
            "gap:6px;border-top:1px solid #eee"
        ):
            tag_text = "  ".join(f"#{t}" for t in spec.tags[:3]) if spec.tags else ""
            if tag_text:
                ui.label(tag_text).classes("text-xs text-grey-7").style(
                    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0"
                )
            else:
                ui.space()
            if spec.docs_url:
                ui.link("官网", spec.docs_url, new_tab=True).classes("text-xs")

        def _on_cb_change(e, _k=spec.key) -> None:
            if bool(e.value):
                sel_state["selected"].add(_k)
            else:
                sel_state["selected"].discard(_k)

        cb.on_value_change(_on_cb_change)


def _do_add(
    ui,
    state: Dict[str, Any],
    sel_state: Dict[str, Any],
    missing: List[ToolSpec],
    mark_restart: Callable[[], None] | None,
    refresh: Callable[[], None],
    dlg,
) -> None:
    keys = list(sel_state["selected"])
    if not keys:
        ui.notify("请先勾选至少一个工具。", color="warning")
        return
    specs_by_key = {s.key: s for s in missing}
    updates: Dict[str, Any] = {}
    for k in keys:
        sp = specs_by_key.get(k)
        if not sp:
            continue
        _flatten_defaults(updates, sp.key, sp.defaults)

    try:
        path, bak, changes = yaml_store.save_updates(_FILENAME, updates)
    except Exception as e:  # noqa: BLE001
        logger.exception("add tools failed")
        ui.notify(f"写入失败：{type(e).__name__}: {e}", color="negative")
        return

    if changes and mark_restart is not None:
        try:
            mark_restart()
        except Exception:  # noqa: BLE001
            logger.exception("mark_restart_needed failed")

    bak_note = f"；备份：{bak.name}" if bak else ""
    ui.notify(
        f"已添加 {len(keys)} 个工具（{', '.join(keys)}）{bak_note}，"
        "请到主列表点「设置」补齐参数后启用。",
        color="positive",
        timeout=6000,
    )
    dlg.close()
    refresh()


def _ui_catalog_reload_hint(ui) -> None:
    """提示用户目录刷新的操作方式。"""
    bundled = _bundled_catalog_path()
    override = _override_catalog_path()
    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width: min(560px, 92vw)"):
        ui.label("从官网 / 仓库更新工具目录").classes("text-base font-semibold")
        ui.label(
            "为避免在线拉取失败阻塞面板，目录统一走本地 JSON 文件。请按以下路径操作："
        ).classes("text-sm text-grey-8 q-mb-sm")
        ui.label("① 内置目录（只读）").classes("text-sm font-semibold q-mt-sm")
        ui.label(str(bundled)).classes("text-xs font-mono text-grey-8")
        ui.label("② 覆盖 / 追加目录（放在 CHAYUAN_ROOT 下，随数据目录走）").classes(
            "text-sm font-semibold q-mt-sm"
        )
        ui.label(str(override)).classes("text-xs font-mono text-grey-8")
        ui.label(
            "把官方最新 JSON 下载保存到路径 ②，或把企业内部工具 JSON 放到那里，"
            "此处点「关闭」后再点右上角刷新即可加载。"
        ).classes("text-sm text-grey-8 q-mt-sm")
        with ui.row().classes("w-full justify-end q-mt-md"):
            ui.button("关闭", on_click=dlg.close).props("color=primary")
    dlg.open()


# ---------------------------------------------------------------------------
# 字段控件
# ---------------------------------------------------------------------------

def _build_tool_input(ui, fld: ToolField, current: Any):
    label = fld.label
    tip = fld.help or ""

    if fld.widget == "switch":
        el = ui.switch(label, value=bool(current) if current is not None else False)
        if tip:
            el.tooltip(tip)
        return el

    if fld.widget == "number":
        val: Any = current if current not in (None, "") else None
        el = ui.number(
            label=label, value=val,
            min=fld.min, max=fld.max, step=fld.step or 1, format=None,
        ).props("outlined dense").classes("w-full")
        if tip:
            el.tooltip(tip)
        return el

    if fld.widget == "select":
        choices = list(fld.choices or [])
        if current is not None and current not in choices:
            choices = [current, *choices]
        el = ui.select(choices, label=label, value=current).props(
            "outlined dense"
        ).classes("w-full")
        if tip:
            el.tooltip(tip)
        return el

    if fld.widget == "model_select":
        choices = _list_models_by_type(fld.model_type or "")
        val = current if current is not None else ""
        if val and val not in choices:
            choices = [val, *choices]
        el = ui.select(choices, label=label, value=val, with_input=True).props(
            "outlined dense use-input input-debounce=0 clearable"
        ).classes("w-full")

        async def _refresh_choices(_=None, _el=el, _mt=fld.model_type) -> None:
            new_choices = _list_models_by_type(_mt or "")
            cur = _el.value
            if cur and cur not in new_choices:
                new_choices = [cur, *new_choices]
            try:
                _el.options = new_choices
                _el.update()
            except Exception:  # noqa: BLE001
                pass
            ui.notify(
                f"已刷新模型列表（{len(new_choices)} 个 {_mt} 模型）",
                color="info",
            )

        with el.add_slot("append"):
            ui.button(icon="refresh", on_click=_refresh_choices).props(
                "flat round dense size=sm color=primary"
            ).tooltip(
                f"从 model_settings.yaml 重新拉取 {fld.model_type} 模型清单"
            )
        if tip:
            el.tooltip(tip)
        return el

    if fld.widget == "password":
        el = ui.input(
            label=label,
            value=str(current) if current is not None else "",
            password=True,
            password_toggle_button=bool(fld.password_toggle),
        ).props("outlined dense").classes("w-full")
        if tip:
            el.tooltip(tip)
        return el

    if fld.widget == "textarea":
        el = ui.textarea(
            label=label,
            value=str(current) if current is not None else "",
        ).props(
            "outlined dense autogrow input-style='font-family: monospace;min-height:6em'"
        ).classes("w-full")
        if tip:
            el.tooltip(tip)
        return el

    el = ui.input(
        label=label,
        value="" if current is None else str(current),
        placeholder=fld.placeholder,
    )
    props = "outlined dense"
    if fld.placeholder:
        props += " stack-label"
    el.props(props).classes("w-full")
    if tip:
        el.tooltip(tip)
    return el


def _list_models_by_type(model_type: str) -> List[str]:
    if not model_type:
        return []
    try:
        from chayuan.server.utils import get_config_models
        return sorted(get_config_models(model_type=model_type).keys())
    except Exception:  # noqa: BLE001
        logger.exception("list models for %s failed", model_type)
        return []


# ---------------------------------------------------------------------------
# 杂项
# ---------------------------------------------------------------------------

def _tool_present(doc: Any, tool_key: str) -> bool:
    if not isinstance(doc, dict):
        return False
    node = doc.get(tool_key)
    return isinstance(node, dict)


def _get_tool_value(doc: Any, tool_key: str, sub_path: str, default: Any = None) -> Any:
    return yaml_store.get_by_path(doc, f"{tool_key}.{sub_path}", default=default)


def _flatten_defaults(
    updates: Dict[str, Any],
    prefix: str,
    value: Any,
) -> None:
    """把嵌套 dict 展平成 ``{dotted_path: value}`` 形式。

    嵌套 dict 里的 list / 空 dict 直接整段写入，不再继续展平。
    """
    if isinstance(value, dict):
        if not value:
            updates[prefix] = {}
            return
        for k, v in value.items():
            _flatten_defaults(updates, f"{prefix}.{k}", v)
        return
    updates[prefix] = value
