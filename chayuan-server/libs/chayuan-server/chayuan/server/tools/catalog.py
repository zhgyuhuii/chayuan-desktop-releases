"""工具目录加载（共享层）。

数据来源：
- 内置：``chayuan/data/tools_catalog.json``（必有）
- 覆盖：``$CHAYUAN_ROOT/tools_catalog.override.json``（可选；按 key 整体替换）

之前这两份解析逻辑只在 ``config_panel/tools_page.py`` 私有持有；REST API 现在
也要读同一份目录，再复制一遍既容易出错又难维护，所以提到这里做唯一真源。

dataclass 形态与 NiceGUI 面板原版完全一致，只是搬家——上层调用方无须改动。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.tools.catalog")


# ---------------------------------------------------------------------------
# dataclass
# ---------------------------------------------------------------------------

@dataclass
class ToolField:
    """单个可编辑字段。

    ``widget`` 决定前端渲染方式：text / textarea / switch / number / password /
    select / model_select。``model_select`` 由前端解析 ``model_type`` 到模型选择器。
    """

    path: str
    label: str
    widget: str = "text"
    help: str = ""
    placeholder: str = ""
    choices: Optional[List[str]] = None
    model_type: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    password_toggle: bool = True
    default: Any = None


@dataclass
class ToolSpec:
    """目录里的单个工具卡片定义。"""

    key: str
    title: str
    icon: str = "build"
    category: str = "misc"
    summary: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    docs_url: str = ""
    fields: List[ToolField] = field(default_factory=list)
    defaults: Dict[str, Any] = field(default_factory=dict)
    register_as: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def bundled_catalog_path() -> Path:
    """``chayuan/data/tools_catalog.json`` 的绝对路径（随包发布）。"""
    # 本文件位于 chayuan/server/tools/，data/ 在 chayuan 包根
    return Path(__file__).resolve().parent.parent.parent / "data" / "tools_catalog.json"


def override_catalog_path() -> Path:
    """``$CHAYUAN_ROOT/tools_catalog.override.json``（用户覆盖，可选）。"""
    from chayuan.settings import CHAYUAN_ROOT
    return Path(CHAYUAN_ROOT) / "tools_catalog.override.json"


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.is_file():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        logger.exception("读取工具目录 JSON 失败：%s", path)
        return None


def _parse_field(raw: Dict[str, Any]) -> ToolField:
    return ToolField(
        path=str(raw.get("path", "")),
        label=str(raw.get("label", raw.get("path", ""))),
        widget=str(raw.get("widget", "text")),
        help=str(raw.get("help", "")),
        placeholder=str(raw.get("placeholder", "")),
        choices=list(raw["choices"]) if isinstance(raw.get("choices"), list) else None,
        model_type=raw.get("model_type"),
        min=raw.get("min"),
        max=raw.get("max"),
        step=raw.get("step"),
        password_toggle=bool(raw.get("password_toggle", True)),
        default=raw.get("default"),
    )


def _parse_tool(raw: Dict[str, Any]) -> ToolSpec:
    fields = [_parse_field(f) for f in (raw.get("fields") or [])]
    return ToolSpec(
        key=str(raw["key"]),
        title=str(raw.get("title", raw["key"])),
        icon=str(raw.get("icon", "build")),
        category=str(raw.get("category", "misc")),
        summary=str(raw.get("summary", "")),
        description=str(raw.get("description", "")),
        tags=[str(t) for t in (raw.get("tags") or [])],
        docs_url=str(raw.get("docs_url", "")),
        fields=fields,
        defaults=dict(raw.get("defaults") or {}),
        register_as=[str(c) for c in (raw.get("register_as") or [])],
    )


def load_catalog() -> Tuple[List[ToolSpec], Dict[str, str]]:
    """读取内置 + 覆盖目录，返回 (tools, categories)。

    规则：
    - bundled 必须存在；override 可选
    - tools 按 key 合并：override 整体替换 bundled 的同 key 项
    - categories：dict 浅合并，override 优先
    - 排序：先按 categories 的字典顺序，再按 title 字典序
    """
    bundled = _read_json(bundled_catalog_path()) or {}
    override = _read_json(override_catalog_path()) or {}

    tools_by_key: Dict[str, Dict[str, Any]] = {}
    categories: Dict[str, str] = {}

    for src in (bundled, override):
        cats = src.get("categories") or {}
        if isinstance(cats, dict):
            categories.update({str(k): str(v) for k, v in cats.items()})
        for t in src.get("tools") or []:
            key = t.get("key")
            if not key:
                continue
            tools_by_key[str(key)] = t

    specs: List[ToolSpec] = []
    for key, raw in tools_by_key.items():
        try:
            specs.append(_parse_tool(raw))
        except Exception:  # noqa: BLE001
            logger.exception("跳过无法解析的工具目录项：%s", key)

    cat_order = list(categories.keys())
    specs.sort(key=lambda s: (
        cat_order.index(s.category) if s.category in cat_order else 999,
        s.title,
    ))
    return specs, categories
