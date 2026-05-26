"""「自定义工具」持久化：``CHAYUAN_ROOT/custom_tools.yaml``。

结构：

    custom_tools:
      - name: my_weather               # 必须是合法 Python 标识符；唯一
        title: 自定义天气                 # 面板展示
        description: 查询指定城市的实时天气  # 同时作为 LLM prompt
        method: GET
        url: https://api.example.com/weather
        params:
          - name: city
            type: string
            description: 城市名称（拼音或英文更稳）
            required: true
          - name: unit
            type: string
            enum: [c, f]
            default: c
        headers:
          Authorization: Bearer xxx
        body_template: null            # POST/PUT/PATCH 时用：允许 {placeholder}
        timeout: 15
        enabled: true

所有读写统一经由这里，UI / 动态注册器 / CLI 都走同一份 parser，避免两份不同意。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from chayuan.pydantic_settings_file import import_yaml


_FILENAME = "custom_tools.yaml"
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ParamSpec:
    name: str
    type: str = "string"     # string / integer / number / boolean
    description: str = ""
    required: bool = False
    default: Any = None
    enum: List[Any] = field(default_factory=list)


@dataclass
class CustomToolSpec:
    name: str
    title: str = ""
    description: str = ""
    method: str = "GET"
    url: str = ""
    params: List[ParamSpec] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    body_template: Optional[str] = None
    timeout: float = 15.0
    enabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "method": self.method.upper(),
            "url": self.url,
            "params": [
                {
                    "name": p.name, "type": p.type,
                    "description": p.description,
                    "required": bool(p.required),
                    "default": p.default,
                    "enum": list(p.enum) if p.enum else [],
                }
                for p in self.params
            ],
            "headers": dict(self.headers or {}),
            "body_template": self.body_template,
            "timeout": float(self.timeout or 15.0),
            "enabled": bool(self.enabled),
        }


def _yaml_path() -> Path:
    # 懒查询 CHAYUAN_ROOT；也支持 ``CHAYUAN_CUSTOM_TOOLS_YAML`` 覆盖，便于单测
    # 仅隔离这一个 yaml，而不用切整个数据目录（避免连带 KB/DB 初始化）。
    import os
    override = os.environ.get("CHAYUAN_CUSTOM_TOOLS_YAML")
    if override:
        return Path(override).expanduser()
    from chayuan.settings import CHAYUAN_ROOT as _ROOT
    return Path(_ROOT) / _FILENAME


_NAMESPACE = "custom_tools"
_ROOT_KEY = "custom_tools"


def _config_center_disabled() -> bool:
    import os
    return os.environ.get("CHAYUAN_CONFIG_CENTER_DISABLED", "").strip() in (
        "1", "true", "yes", "on",
    )


def _load_raw() -> Dict[str, Any]:
    if _config_center_disabled():
        path = _yaml_path()
        if not path.is_file():
            return {"custom_tools": []}
        with open(path, "r", encoding="utf-8") as f:
            return import_yaml().load(f) or {"custom_tools": []}
    try:
        from chayuan.server.config_center import get_store
        val = get_store().get(
            _NAMESPACE, _ROOT_KEY, default=None,
            yaml_fallback_path=_yaml_path(),
        )
        if val is None:
            return {"custom_tools": []}
        if isinstance(val, list):
            return {"custom_tools": val}
        if isinstance(val, dict):
            return val
        return {"custom_tools": []}
    except Exception:  # noqa: BLE001
        path = _yaml_path()
        if not path.is_file():
            return {"custom_tools": []}
        with open(path, "r", encoding="utf-8") as f:
            doc = import_yaml().load(f)
        return doc or {"custom_tools": []}


def _dump_raw(doc: Dict[str, Any]) -> None:
    tools_list = doc.get("custom_tools", []) or []
    if not _config_center_disabled():
        try:
            from chayuan.server.config_center import get_store
            get_store().set(
                _NAMESPACE, _ROOT_KEY, tools_list,
                updated_by="custom_tools_store", comment="panel save",
            )
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("chayuan.custom_tools_store").warning(
                "config_center 写入失败，降级只写 yaml：%r", e,
            )
    path = _yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        import_yaml().dump(doc, f)
    import os
    os.replace(tmp, path)


def _parse_tool(raw: Dict[str, Any]) -> CustomToolSpec:
    params: List[ParamSpec] = []
    for p in raw.get("params") or []:
        params.append(ParamSpec(
            name=str(p.get("name", "")).strip(),
            type=str(p.get("type", "string")).lower().strip() or "string",
            description=str(p.get("description", "")),
            required=bool(p.get("required", False)),
            default=p.get("default"),
            enum=list(p.get("enum") or []),
        ))
    return CustomToolSpec(
        name=str(raw.get("name", "")).strip(),
        title=str(raw.get("title", "")),
        description=str(raw.get("description", "")),
        method=str(raw.get("method", "GET")).upper(),
        url=str(raw.get("url", "")),
        params=params,
        headers=dict(raw.get("headers") or {}),
        body_template=raw.get("body_template"),
        timeout=float(raw.get("timeout", 15.0) or 15.0),
        enabled=bool(raw.get("enabled", False)),
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_tools() -> List[CustomToolSpec]:
    doc = _load_raw()
    out: List[CustomToolSpec] = []
    for raw in doc.get("custom_tools") or []:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(_parse_tool(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


def get_tool(name: str) -> Optional[CustomToolSpec]:
    for t in list_tools():
        if t.name == name:
            return t
    return None


def validate_spec(spec: CustomToolSpec) -> List[str]:
    """对单条 spec 做静态校验，返回错误消息列表。"""
    errs: List[str] = []
    if not spec.name or not _ID_RE.match(spec.name):
        errs.append(
            f"name 非法：{spec.name!r}（必须是合法 Python 标识符，字母/数字/下划线开头）"
        )
    if spec.method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        errs.append(f"method 非法：{spec.method}")
    if not spec.url or not spec.url.startswith(("http://", "https://")):
        errs.append(f"url 必须以 http(s):// 开头，当前：{spec.url!r}")
    if not spec.description:
        errs.append("description 不能为空（LLM 会读这段作为工具说明）")
    seen = set()
    for p in spec.params:
        if not p.name or not _ID_RE.match(p.name):
            errs.append(f"参数 name 非法：{p.name!r}")
        if p.name in seen:
            errs.append(f"参数 name 重复：{p.name!r}")
        seen.add(p.name)
        if p.type not in ("string", "integer", "number", "boolean"):
            errs.append(f"参数 {p.name} 的 type={p.type} 不支持")
    return errs


def save_tool(spec: CustomToolSpec) -> None:
    errs = validate_spec(spec)
    if errs:
        raise ValueError("；".join(errs))
    doc = _load_raw()
    lst = list(doc.get("custom_tools") or [])
    idx = next((i for i, t in enumerate(lst)
                if isinstance(t, dict) and t.get("name") == spec.name), None)
    if idx is None:
        lst.append(spec.to_dict())
    else:
        lst[idx] = spec.to_dict()
    doc["custom_tools"] = lst
    _dump_raw(doc)


def delete_tool(name: str) -> bool:
    doc = _load_raw()
    lst = list(doc.get("custom_tools") or [])
    new = [t for t in lst if not (isinstance(t, dict) and t.get("name") == name)]
    if len(new) == len(lst):
        return False
    doc["custom_tools"] = new
    _dump_raw(doc)
    return True
