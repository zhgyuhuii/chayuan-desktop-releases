"""通用 HTTP 验证器（供「自定义工具测试」「http_request 工具」「App 回调测试」复用）。

核心：用 ``httpx`` 按 GET/POST/PUT/PATCH/DELETE 发包，返回一个结构化的 ``ProbeResult``，
方便上层：
- 面板上展示「连通 + HTTP 状态 + 响应预览 + 延迟」；
- 回调系统在「创建 App」时给用户跑一次 ping，提前发现配置错误；
- 自动化测试里断言请求是否成功。

设计要点：
- 小而通用：不绑任何具体工具；参数就是 HTTP 三件套 + 响应处理选项；
- 默认 15s 超时、跟随重定向；`max_bytes` 截断以便 UI 友好展示；
- 响应体优先按 JSON 解析，失败回落到原始字符串（前 `max_bytes` 字节）。
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Mapping, Optional


Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


@dataclass
class ProbeResult:
    ok: bool
    status_code: Optional[int]
    latency_ms: int
    content_type: str
    body_kind: str         # json / text / bytes / empty
    body: Any              # 解析后的 JSON dict/list 或截断后的 str
    truncated: bool
    url: str
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_request(
    method: str,
    url: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    json_body: Optional[Any] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: float = 15.0,
    follow_redirects: bool = True,
    max_bytes: int = 200_000,
    verify: bool = True,
) -> ProbeResult:
    """单次 HTTP 请求；所有异常都被收敛为 ``ProbeResult(ok=False, ...)``。"""
    import httpx

    t0 = time.monotonic()
    m = (method or "GET").upper()
    if m not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        return ProbeResult(
            ok=False, status_code=None, latency_ms=0,
            content_type="", body_kind="empty", body=None, truncated=False,
            url=url, error=f"unsupported method: {method}",
        )

    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=follow_redirects, verify=verify,
        ) as cli:
            resp = cli.request(
                m, url, params=params, json=json_body, headers=dict(headers or {}),
            )
    except httpx.HTTPError as e:
        return ProbeResult(
            ok=False, status_code=None,
            latency_ms=int((time.monotonic() - t0) * 1000),
            content_type="", body_kind="empty", body=None, truncated=False,
            url=url, error=f"{type(e).__name__}: {e}",
        )

    latency_ms = int((time.monotonic() - t0) * 1000)
    ct = resp.headers.get("content-type", "")
    raw = resp.content[:max_bytes]
    truncated = len(resp.content) > max_bytes

    body: Any
    body_kind: str
    if not raw:
        body, body_kind = None, "empty"
    elif "json" in ct.lower():
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
            body_kind = "json"
        except Exception:  # noqa: BLE001
            body = raw.decode("utf-8", "replace")
            body_kind = "text"
    else:
        try:
            body = raw.decode("utf-8", "replace")
            body_kind = "text"
        except Exception:  # noqa: BLE001
            body = raw.hex()
            body_kind = "bytes"

    return ProbeResult(
        ok=200 <= resp.status_code < 400,
        status_code=resp.status_code,
        latency_ms=latency_ms,
        content_type=ct,
        body_kind=body_kind,
        body=body,
        truncated=truncated,
        url=str(resp.url),
    )
