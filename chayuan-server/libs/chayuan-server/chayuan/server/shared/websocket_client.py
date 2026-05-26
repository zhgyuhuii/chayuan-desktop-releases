"""通用 WebSocket 探测 / 发收封装。

对标 ``shared.http_test.run_request`` 的定位，被「WebSocket 工具测试按钮」、
「WS 自定义工具运行时」、以及未来想发 WS ping 的任何地方复用。

核心入口：``probe_websocket`` —— 同步调用，内部自己跑 asyncio event loop。
所有超时 / 连接错误 / 协议错误都被收敛成 ``WSProbeResult(ok=False, error=...)``，
不抛出。

固定返回结构 ``WSProbeResult``：

- ``ok``        —— 连接成功且没有抛错（不代表业务成功）
- ``url``       —— 拼好 query 串后的最终 URL
- ``connect_ms``—— TCP + TLS + 协议握手总耗时
- ``duration_ms`` — 连接持续时间（close 时减去 start）
- ``messages``  —— 按时序排列的 send / receive 记录，每条是 WSMessage
- ``error``     —— 出错时的一行 summary

每条消息统一用 ``WSMessage`` 表示：kind 区分 ``json / text / bytes``；收到的消息
若配置了 ``response_path``，会同时把抽取结果挂在 ``parsed`` 字段。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit


AuthType = Literal["none", "header", "query", "bearer"]


@dataclass
class AuthSpec:
    type: AuthType = "none"
    key: str = ""          # header name / query param name
    value: str = ""        # token / secret


@dataclass
class WSMessage:
    ts: float
    direction: Literal["sent", "received"]
    kind: Literal["json", "text", "bytes", "empty"]
    body: Any
    parsed: Any = None


@dataclass
class WSProbeResult:
    ok: bool
    url: str
    connect_ms: int = 0
    duration_ms: int = 0
    error: str = ""
    messages: List[WSMessage] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # dataclass → dict，枚举已经是 str，time 已经是 float
        return d


def _apply_auth(url: str, auth: Optional[AuthSpec]) -> tuple[str, Dict[str, str]]:
    """把 auth 应用到 URL / Header 上，返回 (修改后的 url, 附加 headers)。"""
    headers: Dict[str, str] = {}
    if not auth or auth.type == "none":
        return url, headers

    if auth.type == "query" and auth.key:
        parts = urlsplit(url)
        q = parts.query
        sep = "&" if q else ""
        q = f"{q}{sep}{urlencode({auth.key: auth.value})}"
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, q, parts.fragment))
    elif auth.type == "header" and auth.key:
        headers[auth.key] = auth.value
    elif auth.type == "bearer":
        headers["Authorization"] = f"Bearer {auth.value}"
    return url, headers


def _frame_body(raw: Any) -> tuple[str, Any]:
    """识别 WS frame：JSON / 文本 / 字节。"""
    if raw is None:
        return "empty", None
    if isinstance(raw, (bytes, bytearray)):
        try:
            decoded = raw.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return "bytes", raw.hex()
        return _frame_body(decoded)
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return "json", json.loads(stripped)
            except Exception:  # noqa: BLE001
                return "text", raw
        return "text", raw
    # 已经是 dict / list 等 Python 对象（例如调用方直接传过来的）
    return "json", raw


def _extract_path(obj: Any, dotted: str) -> Any:
    """从 JSON body 按 dotted path 提取字段；失败返回 None。"""
    if not dotted:
        return None
    node = obj
    for seg in dotted.split("."):
        if not seg:
            continue
        if isinstance(node, dict):
            if seg in node:
                node = node[seg]
            else:
                return None
        elif isinstance(node, list):
            try:
                idx = int(seg)
            except ValueError:
                return None
            if 0 <= idx < len(node):
                node = node[idx]
            else:
                return None
        else:
            return None
    return node


async def _probe_async(
    *,
    url: str,
    auth: Optional[AuthSpec],
    on_connect_messages: List[str],
    request_message: Optional[str],
    max_messages: int,
    receive_timeout: float,
    close_after_first: bool,
    response_path: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> WSProbeResult:
    try:
        import websockets
        from websockets.client import connect as ws_connect
    except ImportError as e:
        return WSProbeResult(
            ok=False, url=url,
            error=f"websockets 未安装：{e!s}",
        )

    url2, auth_headers = _apply_auth(url, auth)
    headers = {**auth_headers, **(extra_headers or {})}

    messages: List[WSMessage] = []
    t_start = time.monotonic()
    t_connect_done = t_start

    try:
        async with ws_connect(
            url2,
            additional_headers=list(headers.items()) if headers else None,
            open_timeout=max(3.0, min(receive_timeout, 30.0)),
            max_size=2 * 1024 * 1024,  # 2MB 单帧上限，防被意外大包打爆内存
        ) as ws:
            t_connect_done = time.monotonic()

            # on_connect 初始帧
            for m in on_connect_messages or []:
                await ws.send(m)
                kind, body = _frame_body(m)
                messages.append(WSMessage(
                    ts=time.monotonic() - t_start,
                    direction="sent", kind=kind, body=body,
                ))

            # 主请求
            if request_message is not None:
                await ws.send(request_message)
                kind, body = _frame_body(request_message)
                messages.append(WSMessage(
                    ts=time.monotonic() - t_start,
                    direction="sent", kind=kind, body=body,
                ))

            # 收 max_messages 条或 timeout
            deadline = time.monotonic() + receive_timeout
            received = 0
            while received < max_messages and time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                except websockets.exceptions.ConnectionClosed:
                    break

                kind, body = _frame_body(raw)
                msg = WSMessage(
                    ts=time.monotonic() - t_start,
                    direction="received", kind=kind, body=body,
                    parsed=_extract_path(body, response_path) if response_path else None,
                )
                messages.append(msg)
                received += 1
                if close_after_first:
                    break
    except asyncio.TimeoutError:
        return WSProbeResult(
            ok=False, url=url2, error="connect/receive timeout",
            connect_ms=int((time.monotonic() - t_start) * 1000),
            duration_ms=int((time.monotonic() - t_start) * 1000),
            messages=messages,
        )
    except Exception as e:  # noqa: BLE001
        return WSProbeResult(
            ok=False, url=url2, error=f"{type(e).__name__}: {e}",
            connect_ms=int((t_connect_done - t_start) * 1000),
            duration_ms=int((time.monotonic() - t_start) * 1000),
            messages=messages,
        )

    return WSProbeResult(
        ok=True, url=url2,
        connect_ms=int((t_connect_done - t_start) * 1000),
        duration_ms=int((time.monotonic() - t_start) * 1000),
        messages=messages,
    )


def probe_websocket(
    url: str,
    *,
    auth: Optional[AuthSpec] = None,
    on_connect_messages: Optional[List[str]] = None,
    request_message: Optional[str] = None,
    max_messages: int = 5,
    receive_timeout: float = 10.0,
    close_after_first: bool = False,
    response_path: str = "",
    extra_headers: Optional[Dict[str, str]] = None,
) -> WSProbeResult:
    """同步入口：自动兼容「当前有 / 无 running event loop」两种场景。"""
    coro = _probe_async(
        url=url, auth=auth,
        on_connect_messages=on_connect_messages or [],
        request_message=request_message,
        max_messages=max_messages,
        receive_timeout=receive_timeout,
        close_after_first=close_after_first,
        response_path=response_path,
        extra_headers=extra_headers,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        return asyncio.run(coro)

    # 已经在 event loop 中（例如 NiceGUI 事件回调）：开线程跑新 loop，避免嵌套
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(asyncio.run, coro)
        return fut.result()
