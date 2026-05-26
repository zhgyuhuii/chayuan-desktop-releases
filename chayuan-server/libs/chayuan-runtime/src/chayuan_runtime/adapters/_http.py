"""共享 httpx 工具：复用 Client、把流式响应规整成"产 dict 的生成器"。

为什么要单独抽出来？
====================

* lazy-import：让桌面 lite 构建在不装 ``httpx`` 时仍能 import 适配器壳；
* 三个 chat adapter（ollama / vllm / llamacpp）解析的是不同的 wire format
  （NDJSON vs OpenAI SSE），把行解析逻辑公共化避免到处复制；
* 关键：流式生成器必须 **拥有** 底层 ``client`` + ``response`` 的生命周期，
  否则 ``with get_client() as c:`` 会在 ``call()`` 返回时关闭连接 ——
  router 拿到的迭代器再迭代时就拿到 ``StreamConsumed``。

公开 API：
* :func:`get_client` —— 生产一个一次性 ``httpx.Client``；
* :func:`stream_openai_sse` —— 解析 OpenAI 风格 SSE 流（vllm / llama.cpp）；
* :func:`stream_ndjson`     —— 解析 ``\\n``-分隔的 JSON 流（ollama）；
* :func:`post_streaming`    —— 一站式：开 client、发 POST、按 ``parser`` 产 dict、
  最后清理资源。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any, Callable, Dict


def get_client(timeout: float = 60.0):
    """构造一个超时 60s、跟随重定向的同步 httpx 客户端。"""
    import httpx
    return httpx.Client(timeout=httpx.Timeout(timeout, read=timeout), follow_redirects=True)


# ---- 流式行解析 -----------------------------------------------------------


def stream_openai_sse(lines_iter: Iterator[str]) -> Iterator[Dict[str, Any]]:
    """解析 OpenAI / vLLM / llama.cpp 风格的 SSE 流。

    输入：逐行字符串迭代器（``response.iter_lines()``）。
    输出：每个 ``data: { ... }`` 块产一个解码好的 dict；遇到 ``data: [DONE]``
    时停止；解析失败的行静默跳过（不阻塞流）。
    """
    for raw in lines_iter:
        if raw is None:
            continue
        line = raw.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[len("data:"):].lstrip()
        if line == "[DONE]":
            return
        if not line or line[0] not in "{[":
            # 心跳 / 注释 / 非 JSON 行
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def stream_ndjson(lines_iter: Iterator[str]) -> Iterator[Dict[str, Any]]:
    """解析 ``\\n`` 分隔的 JSON 流（ollama ``/api/chat``、jaegerlike）。"""
    for raw in lines_iter:
        if raw is None:
            continue
        line = raw.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


# ---- 一站式 streaming POST ------------------------------------------------


def post_streaming(
    url: str,
    *,
    json_body: Dict[str, Any],
    parser: Callable[[Iterator[str]], Iterator[Dict[str, Any]]] = stream_openai_sse,
    timeout: float = 600.0,
    headers: Dict[str, str] | None = None,
) -> Iterator[Dict[str, Any]]:
    """开 client → 发 streaming POST → 用 ``parser`` 产 dict → 最后清理。

    用 :class:`contextlib.ExitStack` 把 ``client`` + ``response`` 的两层
    上下文吃进同一个 finally 中；调用方拿到的"看似裸生成器"实际上托管了
    底层 socket 资源直到迭代完成。

    适用 chat adapter 的 ``call(stream=True)`` 路径，不适合二进制 / 长消息体。
    """
    import httpx

    stack = ExitStack()
    try:
        client = stack.enter_context(httpx.Client(timeout=httpx.Timeout(timeout, read=timeout)))
        resp = stack.enter_context(client.stream("POST", url, json=json_body, headers=headers or {}))
        resp.raise_for_status()

        def _gen() -> Iterator[Dict[str, Any]]:
            try:
                yield from parser(resp.iter_lines())
            finally:
                stack.close()

        return _gen()
    except BaseException:
        stack.close()
        raise


__all__ = [
    "get_client",
    "post_streaming",
    "stream_ndjson",
    "stream_openai_sse",
]
