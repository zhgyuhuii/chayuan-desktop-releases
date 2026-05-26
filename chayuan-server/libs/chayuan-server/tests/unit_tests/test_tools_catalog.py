"""针对 ``chayuan/data/tools_catalog.json`` 和 ``tools_factory`` 的不变量测试。

目标：用一组静态断言把下面几类最常见的「加工具时忘改一处」的 bug 拦在 CI：

1. 目录 JSON 能解析，每条 tool 都有必填字段；
2. 每条 tool 的 ``category`` 都在 ``categories`` 字典里有定义；
3. 每条 tool 的 ``key`` 都能在 ``_TOOLS_REGISTRY`` 里找到对应 ``@regist_tool``
   注册；反过来 ``_TOOLS_REGISTRY`` 里不应该有「目录不知道的」孤儿工具
   （除了以 ``_`` 开头的内部工具或明确的白名单）；
4. 每条 tool 的 ``fields[*].path`` 相对 ``defaults`` 的点号路径是存在的
   （允许 fields 是 defaults 的子集，避免 yaml 里出现不被字段覆盖的冗余键）；
5. 每条 tool 至少有一个图标（icon）且是非空字符串；对图标合法性只做
   基本校验（不联网、不验 Material Icons 全集）。

这些检查是纯静态的，不需要真跑任何工具；``@regist_tool`` 装饰器在
``tools_factory`` 被 import 时会把工具塞进 ``_TOOLS_REGISTRY``，所以
只要能顺利 ``import tools_factory`` 就自动覆盖了「语法 / import 错误」。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _catalog_path() -> Path:
    import chayuan
    return Path(chayuan.__file__).resolve().parent / "data" / "tools_catalog.json"


def _load_catalog() -> dict:
    with open(_catalog_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def _get_by_dotted(doc, path: str):
    node = doc
    for seg in path.split("."):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return ...  # sentinel
    return node


# ---------------------------------------------------------------------------
# 1. 目录 JSON 能解析 + 必填字段
# ---------------------------------------------------------------------------

def test_catalog_json_parses():
    cat = _load_catalog()
    assert isinstance(cat, dict)
    assert "tools" in cat and isinstance(cat["tools"], list) and cat["tools"]
    assert "categories" in cat and isinstance(cat["categories"], dict)


@pytest.mark.parametrize("field", ["key", "title", "icon", "category", "summary", "description"])
def test_every_tool_has_required_fields(field):
    cat = _load_catalog()
    missing = [t.get("key") or "<unknown>" for t in cat["tools"] if not t.get(field)]
    assert not missing, f"下列工具缺少 `{field}` 字段：{missing}"


def test_tool_keys_are_unique():
    cat = _load_catalog()
    keys = [t["key"] for t in cat["tools"]]
    dupes = {k for k in keys if keys.count(k) > 1}
    assert not dupes, f"目录里有重复的 key：{sorted(dupes)}"


def test_tool_icons_are_non_empty_strings():
    cat = _load_catalog()
    for t in cat["tools"]:
        icon = t.get("icon", "")
        assert isinstance(icon, str) and icon.strip(), f"{t['key']} 缺 icon"


# ---------------------------------------------------------------------------
# 2. 每条 tool 的 category 都在 categories 字典里
# ---------------------------------------------------------------------------

def test_tool_categories_are_declared():
    cat = _load_catalog()
    declared = set(cat["categories"].keys())
    bad = [(t["key"], t["category"]) for t in cat["tools"] if t["category"] not in declared]
    assert not bad, f"下列工具引用了未声明的分类：{bad}；已声明：{sorted(declared)}"


# ---------------------------------------------------------------------------
# 3. 目录 <-> 注册表 双向一致
# ---------------------------------------------------------------------------

def _registry_keys() -> set:
    # 导入会触发所有 @regist_tool 注册
    from chayuan.server.agent.tools_factory import tools_registry  # noqa: F401
    from chayuan.server.agent import tools_factory  # noqa: F401

    return set(tools_registry._TOOLS_REGISTRY.keys())


def _catalog_keys(cat: dict) -> set:
    """目录里所有「可被视为已覆盖」的 runtime 名字。

    - 直接 ``key``；
    - ``register_as`` 里声明的伞型子工具。
    """
    out = set()
    for t in cat["tools"]:
        out.add(t["key"])
        for child in (t.get("register_as") or []):
            out.add(str(child))
    return out


def test_every_catalog_key_is_registered():
    cat = _load_catalog()
    reg = _registry_keys()
    missing = []
    for t in cat["tools"]:
        # 伞型 key 自身不要求是运行时工具（如 amap），只要 register_as 里的子工具
        # 都已注册即可；普通 key 必须直接命中注册表
        children = t.get("register_as") or []
        if children:
            for c in children:
                if c not in reg:
                    missing.append(f"{t['key']} -> {c}")
        else:
            if t["key"] not in reg:
                missing.append(t["key"])
    assert not missing, (
        f"下列 key 在 tools_catalog.json 里存在，但没有对应的 @regist_tool 注册："
        f"{missing}"
    )


def test_no_orphan_registered_tools():
    cat = _load_catalog()
    covered = _catalog_keys(cat)

    # 自定义工具 / WebSocket 端点 都是运行时动态注册的，不出现在内置目录里，
    # 属于合法「不在 catalog」的情况，需要排除。
    dynamic_names: set[str] = set()
    try:
        from chayuan.server.config_panel.custom_tools_store import list_tools as _list_custom
        dynamic_names.update(t.name for t in _list_custom())
    except Exception:
        pass
    try:
        from chayuan.server.config_panel.websocket_endpoints_store import list_endpoints as _list_ws
        dynamic_names.update(t.name for t in _list_ws())
    except Exception:
        pass

    reg = _registry_keys()
    orphans = [k for k in reg if k not in covered and k not in dynamic_names]
    assert not orphans, (
        f"下列工具在 @regist_tool 注册，但没有在 tools_catalog.json 里被描述："
        f"{orphans}；请为它们添加目录条目，或作为现有卡片的 register_as 子工具。"
    )


# ---------------------------------------------------------------------------
# 4. fields[*].path 指向的键应在 defaults 里存在
# ---------------------------------------------------------------------------

def test_field_paths_exist_in_defaults():
    cat = _load_catalog()
    issues: list[str] = []
    for t in cat["tools"]:
        fields = t.get("fields") or []
        defaults = t.get("defaults") or {}
        for f in fields:
            path = f.get("path", "")
            if not path:
                continue
            got = _get_by_dotted(defaults, path)
            if got is Ellipsis:
                issues.append(f"{t['key']}.{path}: defaults 里没有对应键")
    assert not issues, "fields/defaults 不一致：\n  - " + "\n  - ".join(issues)


# ---------------------------------------------------------------------------
# 5. import 链路通畅（smoke test）
# ---------------------------------------------------------------------------

def test_tools_factory_imports_clean():
    """能 import 就说明所有 tool 文件语法/顶层 import 都 OK。"""
    import importlib

    mod = importlib.import_module("chayuan.server.agent.tools_factory")
    # 再 reload 一次，模拟真实启动路径（get_tool() 里用了 reload）
    importlib.reload(mod)

    # 验证 __init__ 里暴露的 T1/T2 新工具函数都在
    expected_exports = [
        # T1
        "pubmed_search", "http_request",
        # T2
        "openweather", "yahoo_finance_news", "semantic_scholar",
        "stackexchange", "news_api", "python_repl",
        "github_tool", "gitlab_tool",
        "lark_message", "wechat_work_message", "dingtalk_message",
        "openapi_call", "notion_search", "confluence_search",
    ]
    missing = [name for name in expected_exports if not hasattr(mod, name)]
    assert not missing, f"tools_factory 缺少导出：{missing}"
