"""④ 默认模型 + 对话参数 — capability defaults + chat 参数表单。

UI 布局:
    ┌─ 9 类默认模型(capability) ────────────────────┐
    │ LLM / 嵌入 / 重排 / 文生图 / 图生文 /         │
    │ 语音识别 / 语音合成 / Agent / Embedding agent  │
    ├─ 对话参数 ─────────────────────────────────────┤
    │ HISTORY_LEN(对话历史轮数)                      │
    │ TEMPERATURE(温度,0~2)                         │
    │ MAX_TOKENS(最长生成长度,可空)                 │
    │                                  [💾 保存]    │
    └────────────────────────────────────────────────┘

数据源:
- capability defaults  → ``runtime_framework_panel.render_capability_defaults_row``
- 对话参数             → ``model_settings.yaml`` 顶层 ``HISTORY_LEN`` /
                          ``TEMPERATURE`` / ``MAX_TOKENS``
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from chayuan.server.config_panel import yaml_store
from chayuan.server.config_panel.model_settings import state_cache

logger = logging.getLogger(
    "chayuan.config_panel.model_settings.defaults_subpage"
)

_FILE = "model_settings.yaml"

# 对话参数 — 字段定义(供表单生成 + 保存)
_CHAT_PARAM_DEFS = [
    # (key, label, type, default, min, max, step, tooltip)
    ("HISTORY_LEN", "历史对话轮数", "int", 3, 0, 50, 1,
     "对话上下文携带几轮历史(0=只用当前问题)。轮数越多,上下文越完整,但 token 消耗越大。"),
    ("TEMPERATURE", "温度 (TEMPERATURE)", "float", 0.7, 0.0, 2.0, 0.1,
     "采样温度。0 = 确定性(每次输出几乎相同),0.7 = 平衡,1+ = 创造性更强。"),
    ("MAX_TOKENS", "最大生成长度 (MAX_TOKENS)", "int_optional", None, 1, 32768, 1,
     "单次生成最长 token 数。空 = 跟随模型默认。"),
]


def _load_chat_params() -> Dict[str, Any]:
    """从 model_settings.yaml 读对话参数,缺失则用默认值。"""
    try:
        result = yaml_store.load_yaml(_FILE)
        doc = result.doc if result and result.doc else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("read %s failed: %s", _FILE, e)
        doc = {}
    out: Dict[str, Any] = {}
    for key, _label, _type, default, *_ in _CHAT_PARAM_DEFS:
        out[key] = doc.get(key, default)
    return out


def _save_chat_params(values: Dict[str, Any]) -> None:
    """把对话参数写回 model_settings.yaml(只写本表关心的几个 key,其它不动)。"""
    updates: Dict[str, Any] = {}
    for key, _label, type_str, *_ in _CHAT_PARAM_DEFS:
        if key not in values:
            continue
        v = values[key]
        if type_str == "int" and v is not None:
            updates[key] = int(v)
        elif type_str == "float" and v is not None:
            updates[key] = float(v)
        elif type_str == "int_optional":
            if v in ("", None):
                updates[key] = None
            else:
                updates[key] = int(v)
        else:
            updates[key] = v
    yaml_store.save_updates(_FILE, updates)


def render_defaults_subpage(ui: Any) -> None:
    """渲染④默认模型 + 对话参数 tab 内容。"""
    # ===== 9 类 capability 默认模型 =====
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            render_capability_defaults_row,
        )
        # 80 题:每次进 ④ tab 都强制 invalidate state_cache,确保拿最新厂商配置。
        # 之前依赖 mark_tab_dirty + state_cache.invalidate 链,但有 race 风险
        # (_local_invalidator 因 client 死亡抛异常被自清,rendered 没被设 False),
        # 用户切到 ④ tab 时仍读旧 cache 看不到新配的 deepseek。
        # 强制 invalidate 是最稳的方案 — 重读 yaml 几十 ms 不卡。
        state_cache.invalidate("grouped", "defaults")
        render_capability_defaults_row(
            ui,
            _prefetched_grouped=state_cache.get_capability_grouped(),
            _prefetched_defaults=state_cache.get_capability_defaults(),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("render capability defaults failed: %s", e)
        ui.label(
            f"默认模型选择渲染失败:{type(e).__name__}: {e}"
        ).classes("text-negative text-sm")

    # ===== 对话参数 =====
    try:
        _render_chat_params(ui)
    except Exception as e:  # noqa: BLE001
        logger.exception("render chat params failed: %s", e)
        ui.label(
            f"对话参数渲染失败:{type(e).__name__}: {e}"
        ).classes("text-negative text-sm")


def _render_chat_params(ui: Any) -> None:
    values = _load_chat_params()
    widgets: Dict[str, Any] = {}

    with ui.card().classes("w-full q-mb-md").props("flat bordered").style(
        "padding: 12px 14px;"
    ):
        with ui.row().classes("items-center w-full no-wrap").style("gap: 8px;"):
            ui.icon("tune", size="18px").classes("text-grey-7")
            ui.label("对话参数").classes("text-subtitle2")
            ui.label("(影响所有 LLM 对话默认行为)").classes(
                "text-caption text-grey-6"
            )

        # 84 题:三列布局,3 个字段一行完全显示
        with ui.grid(columns=3).classes("w-full q-mt-sm").style("gap: 12px;"):
            for key, label, type_str, default, vmin, vmax, step, tooltip in _CHAT_PARAM_DEFS:
                cur = values.get(key, default)
                if type_str == "float":
                    w = ui.number(
                        label=label,
                        value=cur if cur is not None else default,
                        min=vmin, max=vmax, step=step,
                        precision=2,
                    ).props("dense outlined").classes("col-span-1")
                elif type_str == "int":
                    w = ui.number(
                        label=label,
                        value=cur if cur is not None else default,
                        min=vmin, max=vmax, step=step,
                    ).props("dense outlined").classes("col-span-1")
                else:  # int_optional
                    w = ui.input(
                        label=label,
                        value="" if cur in (None, "") else str(cur),
                        placeholder="留空=跟随模型默认",
                    ).props("dense outlined clearable").classes("col-span-1")
                w.tooltip(tooltip)
                widgets[key] = w

        with ui.row().classes("items-center justify-end w-full q-mt-sm").style("gap: 8px;"):
            status_label = ui.label("").classes("text-caption text-grey-6")

            def _on_save() -> None:
                values_now: Dict[str, Any] = {}
                for key, _label, type_str, *_ in _CHAT_PARAM_DEFS:
                    raw = getattr(widgets[key], "value", None)
                    values_now[key] = raw
                try:
                    _save_chat_params(values_now)
                except Exception as e:  # noqa: BLE001
                    logger.exception("save chat params failed")
                    status_label.set_text(f"❌ 保存失败:{e}")
                    status_label.classes("text-negative", remove="text-grey-6 text-positive")
                    return
                status_label.set_text("✅ 已保存")
                status_label.classes("text-positive", remove="text-grey-6 text-negative")
                ui.notify("对话参数已保存到 model_settings.yaml", type="positive")

            ui.button("💾 保存对话参数", on_click=_on_save).props(
                "unelevated dense color=primary"
            )


__all__ = ["render_defaults_subpage", "_load_chat_params", "_save_chat_params"]
