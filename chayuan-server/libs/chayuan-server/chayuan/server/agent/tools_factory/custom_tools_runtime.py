"""把 ``custom_tools.yaml`` 里的条目动态合成 LangChain StructuredTool 并注册。

启动链路：
1. ``chayuan.server.agent.tools_factory.__init__`` 导入本模块；
2. ``load_and_register()`` 读 yaml → 为每条 ``CustomToolSpec`` 合成一个
   ``StructuredTool`` ；
3. 注册到 ``_TOOLS_REGISTRY``，从此与手写 ``@regist_tool`` 工具同级；
4. 下游（``get_tool``、``/tools``、对话界面下拉）完全无感知。

运行期：LLM 带具体参数调用某个自定义工具 → 工具用 ``shared.http_test.run_request``
做实际 HTTP 调用，返回 ``BaseToolOutput(json)`` 交回 LLM。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Type

from pydantic import BaseModel, Field, create_model

from langchain_core.tools import StructuredTool

from chayuan.server.shared.http_test import run_request

from .tools_registry import _TOOLS_REGISTRY

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


logger = logging.getLogger("chayuan.custom_tools")


_TYPE_MAP: Dict[str, Type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _build_args_model(spec) -> Type[BaseModel]:
    """基于 ParamSpec 列表动态创建 Pydantic 参数模型。"""
    fields: Dict[str, Any] = {}
    for p in spec.params:
        py_type = _TYPE_MAP.get(p.type, str)
        desc = p.description or ""
        if p.enum:
            desc = (desc + f"（候选：{', '.join(map(str, p.enum))}）").strip()
        if p.required:
            fields[p.name] = (py_type, Field(..., description=desc))
        else:
            default = p.default if p.default is not None else None
            fields[p.name] = (py_type | None, Field(default, description=desc))

    model_name = f"CustomToolArgs_{spec.name}"
    return create_model(model_name, **fields)  # type: ignore[arg-type]


def _render_template(text: str, kwargs: Dict[str, Any]) -> str:
    """极简 {name} 占位模板；未命中时原样保留（用于 URL / body_template）。"""
    try:
        return text.format(**kwargs)
    except Exception:  # noqa: BLE001
        return text


def _make_tool(spec) -> StructuredTool:
    args_model = _build_args_model(spec)

    def _invoke(**kwargs: Any):
        method = spec.method.upper()
        url = _render_template(spec.url, kwargs)
        headers = {k: _render_template(str(v), kwargs) for k, v in (spec.headers or {}).items()}

        # 带占位的 URL 参数已经在 url 里消耗了；剩下的作为 query(GET) 或 body
        remaining = {k: v for k, v in kwargs.items()
                     if v is not None and "{" + k + "}" not in spec.url}

        if method == "GET":
            res = run_request(
                method, url,
                params=remaining, headers=headers, timeout=spec.timeout,
            )
        else:
            if spec.body_template:
                body_str = _render_template(spec.body_template, kwargs)
                try:
                    import json as _json
                    body = _json.loads(body_str)
                except Exception:  # noqa: BLE001
                    body = body_str
            else:
                body = remaining
            res = run_request(
                method, url,
                json_body=body, headers=headers, timeout=spec.timeout,
            )

        return BaseToolOutput(res.to_dict(), format="json")

    # tool description = user-written 说明 + 参数信息
    description = spec.description or f"Call custom endpoint {spec.method} {spec.url}"

    tool = StructuredTool.from_function(
        func=_invoke,
        name=spec.name,
        description=description,
        args_schema=args_model,
    )
    # 让 /tools 接口里的 `title` 字段有值
    try:
        setattr(tool, "title", spec.title or spec.name)
    except Exception:  # noqa: BLE001
        pass
    return tool


# 记录本次注册进 _TOOLS_REGISTRY 的自定义工具名，reload 时清理
_REGISTERED_CUSTOM_NAMES: set[str] = set()


def load_and_register() -> None:
    """读 yaml → 合成 → 注册 / 覆盖。"""
    try:
        from chayuan.server.config_panel.custom_tools_store import list_tools
    except Exception as e:  # noqa: BLE001
        logger.warning("load custom_tools.yaml failed: %r", e)
        return

    # 先把上一轮注册的自定义工具清出 _TOOLS_REGISTRY，避免重名互相污染
    for n in list(_REGISTERED_CUSTOM_NAMES):
        _TOOLS_REGISTRY.pop(n, None)
    _REGISTERED_CUSTOM_NAMES.clear()

    for spec in list_tools():
        if not spec.enabled:
            continue
        try:
            tool = _make_tool(spec)
        except Exception as e:  # noqa: BLE001
            logger.warning("build custom tool %s failed: %r", spec.name, e)
            continue
        _TOOLS_REGISTRY[spec.name] = tool
        _REGISTERED_CUSTOM_NAMES.add(spec.name)
        logger.info("registered custom tool: %s (%s %s)",
                    spec.name, spec.method, spec.url)


# 模块被 import 时立即做一次注册；``get_tool`` 的 reload 链路也会间接再触发。
load_and_register()
