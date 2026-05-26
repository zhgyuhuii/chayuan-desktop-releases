"""把 ``websocket_endpoints.yaml`` 动态合成为 LangChain StructuredTool。

与 HTTP 版的 ``custom_tools_runtime`` 对称：同样通过 ``StructuredTool.from_function``
合成，同样注册进 ``_TOOLS_REGISTRY``，对下游（/tools、对话界面下拉）完全透明。

单次 LLM 调用的语义：
1. 按 ``spec.request_template`` 用 LLM 给的参数做 ``.format(**kwargs)`` 插值；
2. 打开 WS，先发 ``on_connect`` 里的初始帧，再发主请求；
3. 最多等 ``receive_timeout`` 秒、或收到 ``max_messages`` 条后关闭；
4. 返回固定 envelope（``WSProbeResult.to_dict()`` 的结果）给 LLM。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Type

from pydantic import BaseModel, Field, create_model

from langchain_core.tools import StructuredTool

from chayuan.server.shared.websocket_client import (
    AuthSpec, probe_websocket,
)

from .tools_registry import _TOOLS_REGISTRY

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


logger = logging.getLogger("chayuan.websocket_tools")


_TYPE_MAP: Dict[str, Type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _build_args_model(spec) -> Type[BaseModel]:
    fields: Dict[str, Any] = {}
    for p in spec.params:
        py_type = _TYPE_MAP.get(p.type, str)
        if p.required:
            fields[p.name] = (py_type, Field(..., description=p.description or ""))
        else:
            default = p.default if p.default is not None else None
            fields[p.name] = (py_type | None, Field(default, description=p.description or ""))
    model_name = f"WSArgs_{spec.name}"
    return create_model(model_name, **fields)  # type: ignore[arg-type]


def _make_tool(spec) -> StructuredTool:
    args_model = _build_args_model(spec)

    def _invoke(**kwargs: Any):
        try:
            req = spec.request_template.format(**kwargs) if spec.request_template else None
        except Exception:  # noqa: BLE001
            req = spec.request_template or None

        auth = AuthSpec(
            type=spec.auth.type or "none",
            key=spec.auth.key or "",
            value=spec.auth.value or "",
        )
        res = probe_websocket(
            url=spec.url,
            auth=auth,
            on_connect_messages=list(spec.on_connect or []),
            request_message=req,
            max_messages=int(spec.max_messages or 5),
            receive_timeout=float(spec.receive_timeout or 10.0),
            close_after_first=bool(spec.close_after_first),
            response_path=spec.response_path or "",
        )
        return BaseToolOutput(res.to_dict(), format="json")

    tool = StructuredTool.from_function(
        func=_invoke,
        name=spec.name,
        description=spec.description or f"Open WebSocket {spec.url}",
        args_schema=args_model,
    )
    try:
        setattr(tool, "title", spec.title or spec.name)
    except Exception:  # noqa: BLE001
        pass
    return tool


_REGISTERED_WS_NAMES: set[str] = set()


def load_and_register() -> None:
    try:
        from chayuan.server.config_panel.websocket_endpoints_store import list_endpoints
    except Exception as e:  # noqa: BLE001
        logger.warning("load websocket_endpoints.yaml failed: %r", e)
        return

    for n in list(_REGISTERED_WS_NAMES):
        _TOOLS_REGISTRY.pop(n, None)
    _REGISTERED_WS_NAMES.clear()

    for spec in list_endpoints():
        if not spec.enabled:
            continue
        try:
            tool = _make_tool(spec)
        except Exception as e:  # noqa: BLE001
            logger.warning("build ws tool %s failed: %r", spec.name, e)
            continue
        _TOOLS_REGISTRY[spec.name] = tool
        _REGISTERED_WS_NAMES.add(spec.name)
        logger.info("registered ws tool: %s (%s)", spec.name, spec.url)


load_and_register()
