"""HTTP 通用请求工具（http_request）。

把 LLM 和「任意 REST API」打通的万能胶水：LLM 输出 URL + method + payload，
工具就按 yaml 里的**域名白名单**去访问，返回 JSON 或文本给 LLM。

为什么默认要走白名单：
- LLM 幻觉可能生成任意 URL（含内网 /metadata / 企业后台），开放模式会把
  SSRF（Server-Side Request Forgery）风险带到生产；
- 白名单让运维把「可被 LLM 访问的后端」管理在 yaml 里，配合 AuthMiddleware
  的 RBAC 控制谁可以用这个工具，是企业落地的最小安全基线。

yaml 示例（``chayuan_data/tool_settings.yaml``）：

    http_request:
      use: true
      allowlist:
        - https://api.github.com
        - https://jsonplaceholder.typicode.com
        - https://my-internal.example.com/api
      timeout: 15
      max_bytes: 200000    # 响应截断上限，避免把 50MB JSON 全塞给 LLM
      default_headers:     # 统一注入的请求头（如鉴权）
        User-Agent: chayuan-agent/1.0
"""
from __future__ import annotations

import json as _json
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlparse

import httpx

from chayuan.server.pydantic_v1 import Field
from chayuan.server.utils import get_tool_config

from .tools_registry import regist_tool

from langchain_chayuan.agent_toolkits.all_tools.tool import (
    BaseToolOutput,
)


def _allowed(url: str, allowlist: list[str]) -> bool:
    """仅当 URL 的 scheme://host[:port] 命中白名单前缀时才允许访问。"""
    if not allowlist:
        return False
    try:
        u = urlparse(url)
    except Exception:  # noqa: BLE001
        return False
    if u.scheme not in ("http", "https") or not u.netloc:
        return False
    base = f"{u.scheme}://{u.netloc}".rstrip("/")
    for allowed in allowlist:
        a = str(allowed).rstrip("/")
        if base == a or url.startswith(a + "/") or url == a:
            return True
    return False


@regist_tool(title="HTTP 通用请求")
def http_request(
    url: str = Field(description="目标 URL，必须命中 yaml allowlist 白名单"),
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = Field(
        "GET", description="HTTP 方法，默认 GET"
    ),
    params: Optional[Dict[str, Any]] = Field(
        None, description="query string 参数，字典形式"
    ),
    json_body: Optional[Dict[str, Any]] = Field(
        None, description="POST/PUT/PATCH 的 JSON body"
    ),
    headers: Optional[Dict[str, str]] = Field(
        None, description="附加请求头（会与 yaml default_headers 合并）"
    ),
):
    """通用 HTTP 请求工具 — **只允许命中 yaml allowlist 白名单**的 URL。
调用时机:用户给出白名单内 API URL,要「调一下接口」「测一下返回」或做轻量集成时。
输入:url(必须命中 allowlist,否则报错);method(GET/POST/PUT/PATCH/DELETE);headers / params / body。
输出:状态码 + 响应文本(过长会截断)。
不要用于:白名单外 URL(SSRF 风险)、用户没指定 URL 的问题、内网探测;若已配置 OpenAPI spec 优先用 openapi_call(更结构化)。"""
    cfg = get_tool_config("http_request") or {}
    raw_allow = cfg.get("allowlist") or []
    # 兼容两种来源：yaml list 原生、或面板 textarea 的多行字符串
    if isinstance(raw_allow, str):
        allowlist = [ln.strip() for ln in raw_allow.splitlines() if ln.strip()]
    else:
        allowlist = [str(x).strip() for x in raw_allow if str(x).strip()]
    timeout = float(cfg.get("timeout", 15) or 15)
    max_bytes = int(cfg.get("max_bytes", 200000) or 200000)
    default_headers = dict(cfg.get("default_headers") or {})

    if not _allowed(url, allowlist):
        return BaseToolOutput({
            "code": 403,
            "msg": f"URL 未命中 allowlist，已拒绝：{url}",
            "allowlist": allowlist,
        }, format="json")

    hdrs = {**default_headers, **(headers or {})}

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as cli:
            resp = cli.request(
                method.upper(), url,
                params=params, json=json_body, headers=hdrs,
            )
    except httpx.HTTPError as e:
        return BaseToolOutput({
            "code": 599,
            "msg": f"{type(e).__name__}: {e}",
            "url": url,
        }, format="json")

    ct = resp.headers.get("content-type", "")
    raw = resp.content[:max_bytes]
    truncated = len(resp.content) > max_bytes

    body: Any
    if "json" in ct.lower():
        try:
            body = _json.loads(raw.decode("utf-8", "replace") or "null")
        except Exception:  # noqa: BLE001
            body = raw.decode("utf-8", "replace")
    else:
        body = raw.decode("utf-8", "replace")

    return BaseToolOutput({
        "status": resp.status_code,
        "content_type": ct,
        "truncated": truncated,
        "body": body,
    }, format="json")
