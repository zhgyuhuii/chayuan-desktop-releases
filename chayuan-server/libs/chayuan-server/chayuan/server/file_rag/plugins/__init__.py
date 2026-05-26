"""Retriever 插件注册入口。

在服务启动时（api_server.server_app.create_app）按需注册；
import 失败不影响主服务启动。

启用由 ``kb_settings`` 的 ``USE_COLBERT`` / ``USE_COLPALI`` 控制；
关闭时插件仍然注册，但 ``enabled=False``，方便前端管理面板一键切换。
"""
from __future__ import annotations

import logging

from chayuan.server.file_rag.plugins.colbert_plugin import ColBertPlugin  # noqa: F401
from chayuan.server.file_rag.plugins.colpali_plugin import ColPaliPlugin  # noqa: F401

logger = logging.getLogger("chayuan.file_rag.plugins")


def register_builtin_plugins() -> None:
    """把内置插件注册到共用 registry。可多次调用（幂等）。"""
    try:
        from chayuan.server.shared.retriever_plugins import register_plugin
        from chayuan.settings import Settings
        kb = Settings.kb_settings

        colbert_model = getattr(kb, "COLBERT_MODEL", "colbert-ir/colbertv2.0") or "colbert-ir/colbertv2.0"
        register_plugin(
            "colbert",
            ColBertPlugin(model=colbert_model),
            default_weight=float(getattr(kb, "COLBERT_WEIGHT", 0.3) or 0.3),
            enabled=bool(getattr(kb, "USE_COLBERT", False)),
        )
        register_plugin(
            "colpali",
            ColPaliPlugin(),
            default_weight=float(getattr(kb, "COLPALI_WEIGHT", 0.2) or 0.2),
            enabled=bool(getattr(kb, "USE_COLPALI", False)),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("register_builtin_plugins 失败（忽略）：%r", e)
