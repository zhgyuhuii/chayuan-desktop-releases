"""按 OpenAPI Spec 动态调用任意后端接口。

相比官方 openapi toolkit 会在 agent 初始化时「展开」成 N 个独立工具（每 operation
一个），这里做了简化：只暴露一个通用 ``openapi_call`` 工具，由 LLM 在运行时选择
`operationId` + 参数。好处：

- 不用给 agent 喂成百上千个同类工具描述，省 token；
- 后台改 spec 不需要重启 Agent（只要 ``list_operations`` 能列出来）；
- 结合 ``http_request`` 的 allowlist 理念，仅允许 spec 指定的 ``servers.url`` 访问。
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

import httpx

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


_SPEC_CACHE: Dict[str, Dict[str, Any]] = {}


def _load_spec(spec_url: str, timeout: float = 15.0) -> Dict[str, Any]:
    """缓存化读取 OpenAPI JSON 文档；YAML spec 需要调用方先转成 JSON 供我们读。"""
    if spec_url in _SPEC_CACHE:
        return _SPEC_CACHE[spec_url]
    if spec_url.startswith(("http://", "https://")):
        with httpx.Client(timeout=timeout) as cli:
            resp = cli.get(spec_url)
            resp.raise_for_status()
            data = resp.json()
    else:
        import json as _json
        with open(spec_url, "r", encoding="utf-8") as f:
            data = _json.load(f)
    _SPEC_CACHE[spec_url] = data
    return data


def _find_operation(spec: Dict[str, Any], operation_id: str) -> Optional[Dict[str, Any]]:
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            if op.get("operationId") == operation_id:
                return {"path": path, "method": method, "op": op}
    return None


@regist_tool(title="OpenAPI 动态调用")
def openapi_call(
    action: Literal["list_operations", "call"] = Field(
        description="list_operations 仅列 operationId + 摘要；call 才真正请求",
    ),
    operation_id: str = Field("", description="要调用的 operationId（call 必填）"),
    path_params: Optional[Dict[str, Any]] = Field(
        None, description="路径参数（{id} 占位），字典形式",
    ),
    query_params: Optional[Dict[str, Any]] = Field(
        None, description="query string 参数",
    ),
    json_body: Optional[Dict[str, Any]] = Field(
        None, description="请求体（POST/PUT/PATCH）",
    ),
):
    """按 operationId 调用 OpenAPI / Swagger spec 中定义的接口。
调用时机:yaml 已配置 spec_url(后端 openapi.json 地址)且用户问「调用 X 接口」「用 API 做 Y」「查询 Z」时。
**两步走**:先 ``action=list_operations`` 看有哪些 operationId,再 ``action=call`` + ``operation_id=...`` + ``query_params/json_body=...`` 实际调用。
输入:action(``list_operations`` 或 ``call``);operation_id(call 必填);path_params / query_params / json_body。
输出:接口 JSON 响应。
不要用于:未配置 spec_url、用户没说调用什么接口的问题、需要白名单管理的随意 URL(用 http_request)。"""
    cfg = get_tool_config("openapi_call") or {}
    spec_url = (cfg.get("spec_url") or "").strip()
    if not spec_url:
        return BaseToolOutput({
            "error": "未配置 openapi_call.spec_url（可以是 http(s) URL 或本地 JSON 路径）",
        }, format="json")

    try:
        spec = _load_spec(spec_url)
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({
            "error": f"加载 OpenAPI spec 失败：{type(e).__name__}: {e}",
        }, format="json")

    servers = spec.get("servers") or [{"url": cfg.get("base_url", "")}]
    base_url = str((servers[0] or {}).get("url") or cfg.get("base_url") or "").rstrip("/")

    if action == "list_operations":
        ops = []
        for p, methods in (spec.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            for m, op in methods.items():
                if isinstance(op, dict) and op.get("operationId"):
                    ops.append({
                        "operationId": op["operationId"],
                        "method": m.upper(), "path": p,
                        "summary": op.get("summary", ""),
                    })
        return BaseToolOutput({"base_url": base_url, "operations": ops}, format="json")

    # action == "call"
    if not operation_id:
        return BaseToolOutput({"error": "call 动作需要 operation_id"}, format="json")
    found = _find_operation(spec, operation_id)
    if not found:
        return BaseToolOutput({
            "error": f"未找到 operationId={operation_id}",
        }, format="json")

    url_path = found["path"]
    for k, v in (path_params or {}).items():
        url_path = url_path.replace("{" + k + "}", str(v))
    full_url = f"{base_url}{url_path}"

    default_headers = dict(cfg.get("default_headers") or {})
    timeout = float(cfg.get("timeout", 15) or 15)

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as cli:
            resp = cli.request(
                found["method"].upper(), full_url,
                params=query_params, json=json_body,
                headers=default_headers,
            )
    except Exception as e:  # noqa: BLE001
        return BaseToolOutput({
            "error": f"{type(e).__name__}: {e}", "url": full_url,
        }, format="json")

    body: Any
    ct = resp.headers.get("content-type", "")
    if "json" in ct.lower():
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = resp.text
    else:
        body = resp.text[: int(cfg.get("max_chars", 20000) or 20000)]

    return BaseToolOutput({
        "status": resp.status_code, "url": full_url,
        "operationId": operation_id, "body": body,
    }, format="json")
