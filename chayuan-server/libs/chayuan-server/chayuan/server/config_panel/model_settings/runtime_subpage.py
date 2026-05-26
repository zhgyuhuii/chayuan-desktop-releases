"""① 运行时与服务 — 显示 framework runner 卡片(thin shell)。

数据来源(用户原话:"全部是从两条线探测的"):

1. 本地是否运行这些服务  → ``probe_all_frameworks`` 探活
2. 扫描数据目录下对应的 ``docker-compose.yaml``  → ``runtime_framework_panel``
   内置的 dynamic compose specs(``_get_dynamic_compose_specs``)

本 subpage 是 thin shell,直接调用 ``render_runtime_framework_row``;
所有逻辑(健康探测、动态 yaml 扫描、安装弹窗、启停容器)仍在原模块内。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(
    "chayuan.config_panel.model_settings.runtime_subpage"
)


def render_runtime_subpage(ui: Any) -> None:
    """渲染①运行时与服务 tab 内容。"""
    from chayuan.server.config_panel.model_settings import state_cache
    from chayuan.server.config_panel.runtime_framework_panel import (
        render_runtime_framework_row,
    )

    healths = state_cache.get_healths()
    try:
        render_runtime_framework_row(ui, _prefetched_healths=healths)
    except Exception as e:  # noqa: BLE001
        logger.exception("render_runtime_framework_row failed: %s", e)
        ui.label(
            f"运行时与服务渲染失败:{type(e).__name__}: {e}"
        ).classes("text-negative text-sm")


__all__ = ["render_runtime_subpage"]
