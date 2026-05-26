"""「WebSocket 端点」持久化：``CHAYUAN_ROOT/websocket_endpoints.yaml``。

与 HTTP 自定义工具（custom_tools.yaml）并列。每条条目定义一个 WS 接口：
连接地址、鉴权、连接后要发的初始帧、主请求模板、收消息数 / 超时 / 是否首条即关。

单测可通过 ``CHAYUAN_WS_ENDPOINTS_YAML`` 环境变量覆盖 yaml 路径。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from chayuan.pydantic_settings_file import import_yaml


_FILENAME = "websocket_endpoints.yaml"
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class WSParamSpec:
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: Any = None


@dataclass
class WSAuth:
    type: str = "none"   # none / header / query / bearer
    key: str = ""
    value: str = ""


@dataclass
class WSEndpointSpec:
    name: str
    title: str = ""
    description: str = ""
    url: str = ""
    auth: WSAuth = field(default_factory=WSAuth)
    on_connect: List[str] = field(default_factory=list)
    request_template: str = ""
    message_format: str = "json"   # json / text
    response_path: str = ""
    max_messages: int = 5
    receive_timeout: float = 10.0
    close_after_first: bool = False
    params: List[WSParamSpec] = field(default_factory=list)
    enabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "auth": {
                "type": self.auth.type or "none",
                "key": self.auth.key or "",
                "value": self.auth.value or "",
            },
            "on_connect": list(self.on_connect or []),
            "request_template": self.request_template or "",
            "message_format": self.message_format or "json",
            "response_path": self.response_path or "",
            "max_messages": int(self.max_messages or 5),
            "receive_timeout": float(self.receive_timeout or 10.0),
            "close_after_first": bool(self.close_after_first),
            "params": [
                {
                    "name": p.name, "type": p.type,
                    "description": p.description,
                    "required": bool(p.required),
                    "default": p.default,
                }
                for p in self.params
            ],
            "enabled": bool(self.enabled),
        }


def _yaml_path() -> Path:
    import os
    override = os.environ.get("CHAYUAN_WS_ENDPOINTS_YAML")
    if override:
        return Path(override).expanduser()
    from chayuan.settings import CHAYUAN_ROOT as _ROOT
    return Path(_ROOT) / _FILENAME


_NAMESPACE = "ws_endpoints"
_ROOT_KEY = "websocket_endpoints"


def _config_center_disabled() -> bool:
    import os
    return os.environ.get("CHAYUAN_CONFIG_CENTER_DISABLED", "").strip() in (
        "1", "true", "yes", "on",
    )


def _load_raw() -> Dict[str, Any]:
    if _config_center_disabled():
        p = _yaml_path()
        if not p.is_file():
            return {"websocket_endpoints": []}
        with open(p, "r", encoding="utf-8") as f:
            return import_yaml().load(f) or {"websocket_endpoints": []}
    try:
        from chayuan.server.config_center import get_store
        val = get_store().get(
            _NAMESPACE, _ROOT_KEY, default=None,
            yaml_fallback_path=_yaml_path(),
        )
        if val is None:
            return {"websocket_endpoints": []}
        if isinstance(val, list):
            return {"websocket_endpoints": val}
        if isinstance(val, dict):
            return val
        return {"websocket_endpoints": []}
    except Exception:  # noqa: BLE001
        p = _yaml_path()
        if not p.is_file():
            return {"websocket_endpoints": []}
        with open(p, "r", encoding="utf-8") as f:
            return import_yaml().load(f) or {"websocket_endpoints": []}


def _dump_raw(doc: Dict[str, Any]) -> None:
    endpoints = doc.get("websocket_endpoints", []) or []
    if not _config_center_disabled():
        try:
            from chayuan.server.config_center import get_store
            get_store().set(
                _NAMESPACE, _ROOT_KEY, endpoints,
                updated_by="websocket_endpoints_store", comment="panel save",
            )
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("chayuan.ws_endpoints_store").warning(
                "config_center 写入失败，降级只写 yaml：%r", e,
            )
    p = _yaml_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        import_yaml().dump(doc, f)
    import os
    os.replace(tmp, p)


def _parse(raw: Dict[str, Any]) -> WSEndpointSpec:
    auth_raw = raw.get("auth") or {}
    auth = WSAuth(
        type=str(auth_raw.get("type", "none")).lower() or "none",
        key=str(auth_raw.get("key", "")),
        value=str(auth_raw.get("value", "")),
    )
    params: List[WSParamSpec] = []
    for p in raw.get("params") or []:
        params.append(WSParamSpec(
            name=str(p.get("name", "")).strip(),
            type=str(p.get("type", "string")).lower() or "string",
            description=str(p.get("description", "")),
            required=bool(p.get("required", False)),
            default=p.get("default"),
        ))
    return WSEndpointSpec(
        name=str(raw.get("name", "")).strip(),
        title=str(raw.get("title", "")),
        description=str(raw.get("description", "")),
        url=str(raw.get("url", "")),
        auth=auth,
        on_connect=[str(x) for x in (raw.get("on_connect") or [])],
        request_template=str(raw.get("request_template", "")),
        message_format=str(raw.get("message_format", "json")).lower() or "json",
        response_path=str(raw.get("response_path", "")),
        max_messages=int(raw.get("max_messages", 5) or 5),
        receive_timeout=float(raw.get("receive_timeout", 10.0) or 10.0),
        close_after_first=bool(raw.get("close_after_first", False)),
        params=params,
        enabled=bool(raw.get("enabled", False)),
    )


def list_endpoints() -> List[WSEndpointSpec]:
    doc = _load_raw()
    out: List[WSEndpointSpec] = []
    for raw in doc.get("websocket_endpoints") or []:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(_parse(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


def get_endpoint(name: str) -> Optional[WSEndpointSpec]:
    for t in list_endpoints():
        if t.name == name:
            return t
    return None


def validate_spec(spec: WSEndpointSpec) -> List[str]:
    errs: List[str] = []
    if not spec.name or not _ID_RE.match(spec.name):
        errs.append(f"name 非法：{spec.name!r}（要求合法 Python 标识符）")
    if not spec.url.startswith(("ws://", "wss://")):
        errs.append(f"url 必须以 ws:// 或 wss:// 开头：{spec.url!r}")
    if not spec.description:
        errs.append("description 不能为空（LLM 提示词）")
    if spec.auth.type not in ("none", "header", "query", "bearer"):
        errs.append(f"auth.type 非法：{spec.auth.type}")
    if spec.auth.type in ("header", "query") and not spec.auth.key:
        errs.append(f"auth.type={spec.auth.type} 时必须填 auth.key")
    if spec.message_format not in ("json", "text"):
        errs.append(f"message_format 非法：{spec.message_format}")
    seen = set()
    for p in spec.params:
        if not p.name or not _ID_RE.match(p.name):
            errs.append(f"参数 name 非法：{p.name!r}")
        if p.name in seen:
            errs.append(f"参数 name 重复：{p.name!r}")
        seen.add(p.name)
        if p.type not in ("string", "integer", "number", "boolean"):
            errs.append(f"参数 {p.name} type={p.type} 不支持")
    return errs


def save_endpoint(spec: WSEndpointSpec) -> None:
    errs = validate_spec(spec)
    if errs:
        raise ValueError("；".join(errs))
    doc = _load_raw()
    lst = list(doc.get("websocket_endpoints") or [])
    idx = next((i for i, t in enumerate(lst)
                if isinstance(t, dict) and t.get("name") == spec.name), None)
    if idx is None:
        lst.append(spec.to_dict())
    else:
        lst[idx] = spec.to_dict()
    doc["websocket_endpoints"] = lst
    _dump_raw(doc)


def delete_endpoint(name: str) -> bool:
    doc = _load_raw()
    lst = list(doc.get("websocket_endpoints") or [])
    new = [t for t in lst if not (isinstance(t, dict) and t.get("name") == name)]
    if len(new) == len(lst):
        return False
    doc["websocket_endpoints"] = new
    _dump_raw(doc)
    return True
