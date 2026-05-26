"""察元「工具中心」服务层。

把 ``tools_catalog.json`` + ``tool_settings.yaml`` + ``custom_tools.yaml`` 三份
持久化数据收口到这里，让 NiceGUI 配置面板（``config_panel/tools_page.py``）
和新的 REST API（``api_server/tool_routes.py``）共用同一份 dataclass + loader：

- ``catalog``      — 内置目录数据类（ToolSpec / ToolField）+ JSON 加载
- ``config_service`` — 工具配置 CRUD（写 ``tool_settings.yaml``）
- ``openapi_importer`` — 把 OpenAPI 2.0 / 3.x spec 解析为 ``CustomToolSpec`` 草稿
- ``desc_synthesizer`` — 自动生成中文描述 / prompt(spec → LLM → method+path 三层兜底)
"""
from __future__ import annotations

from .catalog import (
    ToolField,
    ToolSpec,
    load_catalog,
    bundled_catalog_path,
    override_catalog_path,
)

__all__ = [
    "ToolField",
    "ToolSpec",
    "load_catalog",
    "bundled_catalog_path",
    "override_catalog_path",
]
