"""② 模型厂商配置 — 本地模型 strip + 云厂商分类卡片。

布局(用户原话:c 项):

    ┌─ 顶部:本地模型(横向 8 + 更多) ─────────────────┐
    │ Ollama / vLLM / Xinference / LM-Studio /         │ ← 第 1 行 4 张
    │ llama.cpp / LocalAI / TGI / GPUStack             │ ← 第 2 行 4 张
    │                                       [更多 →]  │
    ├─ 顶栏:搜索 + 标签 chips + 添加自定义 ────────────┤
    ├─ 云厂商分组(全部展示) ──────────────────────────┤
    │  推荐 (5 家)         [展开]                       │
    │  国内 (15 家)        [展开]                       │
    │  国外 (20 家)        [展开]                       │
    │  聚合中转 (15 家)    [展开]                       │
    └────────────────────────────────────────────────────┘

实现:复用 ``model_config.render_model_settings_page(_mode='providers_only')``,
内部跳过 runtime/capability/marketplace 三行,只渲染 hero strip(本地)+
``_render_cloud_providers_grouped``(云分组网格)+ dialog。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(
    "chayuan.config_panel.model_settings.providers_subpage"
)


def render_providers_subpage(
    ui: Any,
    *,
    mark_restart_needed: Optional[Callable[[], None]] = None,
) -> None:
    """渲染②模型厂商配置 tab 内容。

    复用 ``model_config.render_model_settings_page(_mode='providers_only')``,
    它会渲染:
        * 本地模型 hero strip(kind="local",8 + 更多)
        * 云厂商分组网格(c 项需求)
        * 共享的配置 dialog(点击卡片打开)
        * "添加自定义"对话框
        * "全部保存"按钮(底部)

    保存联动:``_save_all`` 内部已注册 ``trigger_hero_strip_refresh`` +
    ``trigger_capability_defaults_refresh`` 钩子;切换 tab 后 state_cache
    会拿到最新数据。
    """
    from chayuan.server.config_panel.model_config import (
        render_model_settings_page,
    )
    from chayuan.server.config_panel.model_settings import state_cache

    # 复用 state_cache 的 prefetch 结果(避免重复 yaml.load)
    states = state_cache.get_states()

    try:
        render_model_settings_page(
            ui,
            mark_restart_needed=_wrap_invalidate_after_save(mark_restart_needed),
            _prefetched_states=states,
            _mode="providers_only",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("render providers subpage failed: %s", e)
        ui.label(f"② 模型厂商渲染失败:{type(e).__name__}: {e}").classes(
            "text-negative text-sm"
        )


def _wrap_invalidate_after_save(
    user_cb: Optional[Callable[[], None]],
) -> Optional[Callable[[], None]]:
    """包一层:保存厂商配置后自动:

    1. ``state_cache.invalidate("states","grouped","defaults")`` —
       清掉跨 tab 共享的缓存,下次读时会重新走 yaml/local index。
    2. ``mark_tab_dirty("defaults") / ("marketplace")`` — 通知 page.py
       把 ④ 默认模型 tab 和 ③ 模型广场 tab 标脏,用户切换到这些 tab 时
       会重新渲染,拿到最新 yaml 数据(显示新配的厂商及其模型)。

    66 题修复:之前只 invalidate "states",不动 grouped/defaults,所以 ④
    tab 的下拉看到的是首次预取的旧 grouped 数据,即使保存了新厂商也不刷新。
    """
    def _full_invalidate() -> None:
        from chayuan.server.config_panel.model_settings import state_cache
        from chayuan.server.config_panel.model_settings.page import (
            TAB_DEFAULTS, mark_tab_dirty,
        )
        # 1) 清缓存(下次 get_xxx() 时会重读 yaml)
        state_cache.invalidate("states", "grouped", "defaults")
        # 2) 标脏 ② 默认模型(用户切到时重渲染)
        mark_tab_dirty(TAB_DEFAULTS)

    if user_cb is None:
        return _full_invalidate

    def _wrapped() -> None:
        try:
            user_cb()
        finally:
            _full_invalidate()
    return _wrapped


__all__ = ["render_providers_subpage"]
