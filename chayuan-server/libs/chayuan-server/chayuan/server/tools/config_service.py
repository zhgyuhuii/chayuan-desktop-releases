"""工具配置服务层。

把「读 catalog → 合并当前 yaml 值 → 写回 yaml」收口在这里，REST API 与
NiceGUI 面板都从这里出发，避免两边各写一份。

数据流：

    tools_catalog.json     ─┐                ┌─→ ToolConfigDoc(key, fields, values, ...)
    tools_catalog.override.json ─┼─ catalog ─┤
                              │              │
    tool_settings.yaml        ─┴──────────────┘
                                              ▲
                                              │ update_config(values)
                                              │ (yaml_store.save_updates)
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from chayuan.server.config_panel import yaml_store
from chayuan.server.tools.catalog import ToolField, ToolSpec, load_catalog


logger = logging.getLogger("chayuan.tools.config_service")


_TOOL_SETTINGS_YAML = "tool_settings.yaml"
# tools_page.py 中的描述覆盖键(以下划线开头不会污染工具运行时配置)
_DESC_OVERRIDE_KEY = "_desc"
# 启用开关在 yaml 里的子键(全局约定:每个工具节点下 ``use: bool``)
_USE_KEY = "use"


# ---------------------------------------------------------------------------
# Pydantic 输出 schema
# ---------------------------------------------------------------------------


class ToolFieldDoc(BaseModel):
    """前端渲染用的字段 schema(catalog 字段 + 当前值的合并视图)。"""

    path: str
    label: str
    widget: str
    help: str = ""
    placeholder: str = ""
    choices: Optional[List[str]] = None
    model_type: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    password_toggle: bool = True
    default: Any = None
    value: Any = None


class ToolCardDoc(BaseModel):
    """单个工具的"卡片视图":目录元数据 + 当前 yaml 值。"""

    key: str
    title: str
    icon: str = "build"
    category: str = "misc"
    summary: str = ""
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    docs_url: str = ""
    fields: List[ToolFieldDoc] = Field(default_factory=list)
    defaults: Dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False
    description_override: Optional[str] = None
    """用户在 ``tool_settings.yaml/<key>._desc`` 写过的覆盖描述,优先于 catalog.description 展示给 LLM"""
    builtin: bool = True
    """True = 来自 catalog.json 内置 / override；False = 来自 custom_tools.yaml(P3 接入)"""


class ToolUpdate(BaseModel):
    """``PUT /tools/{key}/config`` 的请求体。

    只接受"用户改过的"字段,没动的字段不会出现在 ``values`` 里——避免误把默认值
    刷回 yaml 把用户的旧自定义值覆盖掉。``enabled`` / ``description_override``
    分开传,语义比塞在 values 里更清晰。
    """

    enabled: Optional[bool] = None
    description_override: Optional[str] = None
    values: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _yaml_doc() -> Dict[str, Any]:
    """读 ``tool_settings.yaml`` 全量 doc(没有则空 dict)。"""
    res = yaml_store.load_yaml(_TOOL_SETTINGS_YAML)
    return dict(res.doc or {}) if res.exists else {}


def _get_by_path(doc: Any, dotted: str, default: Any = None) -> Any:
    """``a.b.c`` 形 dotted-path 取值,走 yaml_store 的实现以保证 ruamel 兼容。"""
    return yaml_store.get_by_path(doc, dotted, default=default)


def _field_to_doc(f: ToolField, current: Any) -> ToolFieldDoc:
    return ToolFieldDoc(
        path=f.path,
        label=f.label,
        widget=f.widget,
        help=f.help or "",
        placeholder=f.placeholder or "",
        choices=list(f.choices) if f.choices else None,
        model_type=f.model_type,
        min=f.min,
        max=f.max,
        step=f.step,
        password_toggle=bool(f.password_toggle),
        default=f.default,
        value=current if current is not None else f.default,
    )


def _spec_to_card(spec: ToolSpec, yaml_doc: Dict[str, Any]) -> ToolCardDoc:
    node = yaml_doc.get(spec.key) or {}
    if not isinstance(node, dict):
        node = {}

    fields = [_field_to_doc(f, _get_by_path(node, f.path, default=None)) for f in spec.fields]
    desc_override = node.get(_DESC_OVERRIDE_KEY)
    return ToolCardDoc(
        key=spec.key,
        title=spec.title,
        icon=spec.icon,
        category=spec.category,
        summary=spec.summary,
        description=spec.description,
        tags=list(spec.tags),
        docs_url=spec.docs_url,
        fields=fields,
        defaults=dict(spec.defaults),
        enabled=bool(node.get(_USE_KEY, False)),
        description_override=str(desc_override) if isinstance(desc_override, str) and desc_override.strip() else None,
        builtin=True,
    )


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def list_cards() -> List[ToolCardDoc]:
    """所有内置 + override 工具的卡片视图(按 catalog 排序)。

    自定义工具(``custom_tools.yaml``)由 P3 单独接口暴露,不混在这里——
    UI 上想合并展示是前端拼接的事。
    """
    specs, _ = load_catalog()
    doc = _yaml_doc()
    return [_spec_to_card(s, doc) for s in specs]


def get_card(key: str) -> Optional[ToolCardDoc]:
    specs, _ = load_catalog()
    spec = next((s for s in specs if s.key == key), None)
    if spec is None:
        return None
    return _spec_to_card(spec, _yaml_doc())


def list_categories() -> Dict[str, str]:
    _, cats = load_catalog()
    return dict(cats)


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def _coerce_value(field_spec: ToolField, raw: Any) -> Any:
    """把 UI 来的原始值标准化为写回 yaml 时合适的 python 值。"""
    return yaml_store.coerce_for_widget(field_spec.widget, raw)


def update_config(key: str, update: ToolUpdate) -> ToolCardDoc:
    """更新单个工具的配置,写 ``tool_settings.yaml``,返回新卡片视图。

    - ``enabled``     → ``<key>.use``
    - ``description_override`` → ``<key>._desc``
    - ``values[path]``→ ``<key>.<path>``(按 widget 类型 coerce)

    校验:
    - key 必须存在于 catalog
    - values 中的 path 必须是 catalog 字段已定义的 path,否则报错(防 typo
      把垃圾键写进 yaml)
    """
    specs, _ = load_catalog()
    spec = next((s for s in specs if s.key == key), None)
    if spec is None:
        raise KeyError(f"工具 key 不存在于目录:{key!r}")

    field_by_path = {f.path: f for f in spec.fields}
    updates: Dict[str, Any] = {}

    if update.enabled is not None:
        updates[f"{key}.{_USE_KEY}"] = bool(update.enabled)

    if update.description_override is not None:
        # 空串 → 删除覆盖(走 yaml_store 没有 delete_path,用 None / 空串近似)
        cleaned = update.description_override.strip()
        updates[f"{key}.{_DESC_OVERRIDE_KEY}"] = cleaned if cleaned else None

    for path, raw in update.values.items():
        if path not in field_by_path:
            raise ValueError(f"未知字段:{path!r}(目录中工具 {key} 未声明此 path)")
        try:
            coerced = _coerce_value(field_by_path[path], raw)
        except ValueError as e:
            raise ValueError(f"字段 {path} 值非法:{e}")
        updates[f"{key}.{path}"] = coerced

    if updates:
        yaml_store.save_updates(_TOOL_SETTINGS_YAML, updates)
        logger.info("已更新工具配置 key=%s 改动 path 数=%d", key, len(updates))

    # 重新读卡片视图(yaml 已落盘,这里直接 list_cards 一次保证返回最新)
    card = get_card(key)
    if card is None:  # pragma: no cover —— 上面已校验
        raise KeyError(f"工具不存在:{key}")
    return card
