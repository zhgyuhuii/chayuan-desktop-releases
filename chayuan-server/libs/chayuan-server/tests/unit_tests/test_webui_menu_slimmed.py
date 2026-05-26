"""webui.py 已瘦身到只做对话——AST 级断言避免未来误改回。

本测试的价值：作为**架构边界的契约**，任何人想把「知识库管理」等设置类菜单重新
加回旧对话前端，都必须先动这个测试，强制显式约定。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


_WEBUI_PATH = (
    Path(__file__).resolve().parents[2]
    / "chayuan" / "webui.py"
)


def _read_source() -> str:
    assert _WEBUI_PATH.is_file(), f"webui.py not found: {_WEBUI_PATH}"
    return _WEBUI_PATH.read_text(encoding="utf-8")


def test_webui_menu_only_contains_conversation_pages():
    """侧栏只应有两项：多功能对话 + RAG 对话。"""
    src = _read_source()
    # 管理类菜单绝不再能出现在 webui.py
    forbidden = ["知识库管理", "数据源管理", "数据治理", "MCP 管理"]
    for word in forbidden:
        assert word not in src, (
            f"webui.py 不应再出现「{word}」菜单项；它已迁至 Config Panel。"
            f"如确需保留，请同时修改本测试的约束。"
        )
    # 对话类必须仍在
    assert "多功能对话" in src
    assert "RAG 对话" in src


def test_webui_does_not_import_moved_pages():
    """严禁 import 已迁走的 webui_pages：避免死代码和隐式耦合。"""
    src = _read_source()
    tree = ast.parse(src)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    forbidden_imports = [
        "chayuan.webui_pages.knowledge_base.knowledge_base",
        "chayuan.webui_pages.mcp",
        "chayuan.webui_pages.governance.page",
        "chayuan.webui_pages.knowledge_source.settings",
    ]
    for mod in forbidden_imports:
        assert mod not in imported_modules, (
            f"webui.py 已不应 import {mod}；该页面已迁至 Config Panel。"
        )


def test_webui_mentions_config_panel_link():
    """瘦身后必须给用户一个「去配置面板」的入口，避免找不到设置项。"""
    src = _read_source()
    assert "配置面板" in src or "Config Panel" in src, (
        "侧栏底部需指引用户去配置面板进行管理操作。"
    )
    assert "CONFIG_SERVER" in src, (
        "应读 Settings.basic_settings.CONFIG_SERVER 来构造跳转 URL。"
    )
