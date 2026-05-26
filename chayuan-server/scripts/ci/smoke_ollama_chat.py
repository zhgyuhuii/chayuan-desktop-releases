#!/usr/bin/env python3
"""End-to-end smoke：真启 ollama → 真过 chayuan-gateway → 验证 OpenAI SSE 透传。

为什么不能只跑单元测试？
========================

* 单元测试用 ``httpx.MockTransport`` 验证字段拼接，但生产上有大量"socket 早关
  / 缓冲区行为 / 反向代理头" 类的问题，只有真后端跑一遍才能暴露。
* 流式 chat 改成裸 SSE 后，``StreamingResponse`` + 同步 generator 的组合在 Starlette
  threadpool 里行为正确，要在真 HTTP 通道上验证。

执行步骤
--------

1. 假定调用方已经启动了 ollama（``$OLLAMA_HOST`` 默认 ``http://127.0.0.1:11434``）；
2. 加载 ``chayuan-gateway`` 的 ASGI app；
3. 注入一个 ``OllamaAdapter(mock=False, base_url=$OLLAMA_HOST)``；
4. 把待测 model 写进内存 ``ModelRepository``；
5. 用 ``httpx.AsyncClient(transport=ASGITransport(app))`` 同进程发起一次
   ``POST /v1/chat/completions stream=true``；
6. 断言：
   * 响应头 ``content-type`` 以 ``text/event-stream`` 开头
   * 数据行只出现 ``data: ...`` 不出现 ``event: ...``
   * 最后一行是 ``data: [DONE]``
   * 至少能解析出一个 OpenAI 风格的 ``chat.completion.chunk``

退出码
------

* ``0`` —— 通过
* ``1`` —— assertion 失败
* ``2`` —— ollama 不可达 / 模型未下载
* ``3`` —— 内部异常

CI 上会先跑 ``ollama pull qwen2:0.5b``（约 350MB，最小 chat 模型），所以
本脚本默认 ``--model qwen2:0.5b``。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

DEFAULT_MODEL = "qwen2:0.5b"


async def _run(model: str, ollama_url: str, timeout: float) -> int:
    try:
        import httpx
        from httpx import ASGITransport
    except ImportError as e:
        print(f"[smoke] httpx required: {e}", file=sys.stderr)
        return 3

    # 1) 提前探活，避免后面跑半天才发现 ollama 没起来
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{ollama_url}/api/tags")
            r.raise_for_status()
            tags = r.json().get("models", [])
            names = {t.get("name") for t in tags}
            if model not in names and not any(n.startswith(model.split(":")[0]) for n in names):
                print(f"[smoke] model {model!r} not pulled. tags={names}", file=sys.stderr)
                return 2
    except Exception as e:
        print(f"[smoke] ollama at {ollama_url} unreachable: {e!r}", file=sys.stderr)
        return 2

    # 2) 装配 gateway 上下文
    try:
        from chayuan_gateway.app import create_app
        from chayuan_gateway.deps import get_repo
        from chayuan_registry import ModelRepository, session_scope
        from chayuan_registry.db import reset_for_tests
        from chayuan_runtime.adapters.ollama_adapter import OllamaAdapter
        from chayuan_runtime.registry import get_registry
    except ImportError as e:
        print(f"[smoke] chayuan packages not installed: {e}", file=sys.stderr)
        return 3

    # 注入真 ollama adapter（覆盖默认 mock）
    real = OllamaAdapter(base_url=ollama_url, mock=False)
    get_registry(mock=False).register(real)

    reset_for_tests("sqlite:///:memory:")
    with session_scope() as s:
        ModelRepository(s).upsert({
            "repo": model, "category": "chat", "runtime": "ollama",
            "format": "gguf", "path": "/dev/null",
        })

    app = create_app()

    # 3) 同进程 POST 流式 chat
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=timeout,
    ) as ac:
        async with ac.stream("POST", "/v1/chat/completions", json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
            "stream": True,
            "options": {"temperature": 0, "num_predict": 12},
        }) as resp:
            ctype = resp.headers.get("content-type", "")
            if not ctype.startswith("text/event-stream"):
                print(f"[smoke] FAIL content-type={ctype!r}", file=sys.stderr)
                return 1
            text = ""
            async for chunk in resp.aiter_text():
                text += chunk

    # 4) 断言 SSE wire 格式
    raw_lines = text.split("\n")
    nonempty = [ln for ln in raw_lines if ln.strip()]
    if any(ln.startswith("event:") for ln in nonempty):
        print(f"[smoke] FAIL: unexpected event: lines: {nonempty[:5]}", file=sys.stderr)
        return 1
    data_lines = [ln for ln in nonempty if ln.startswith("data: ")]
    if not data_lines:
        print(f"[smoke] FAIL: no data: lines. text={text[:500]!r}", file=sys.stderr)
        return 1
    if data_lines[-1].strip() != "data: [DONE]":
        print(f"[smoke] FAIL: last line is not [DONE]: {data_lines[-1]!r}", file=sys.stderr)
        return 1

    parsed: list[dict[str, Any]] = []
    for ln in data_lines[:-1]:
        body = ln[len("data: "):]
        try:
            parsed.append(json.loads(body))
        except Exception as e:
            print(f"[smoke] FAIL: data is not valid JSON: {body!r} ({e})", file=sys.stderr)
            return 1

    if not parsed:
        print("[smoke] FAIL: zero parsed chunks", file=sys.stderr)
        return 1

    # 5) 至少有一帧是 OpenAI ``chat.completion.chunk`` 形态
    sample = parsed[0]
    if "choices" not in sample:
        print(f"[smoke] FAIL: chunk missing 'choices': {sample}", file=sys.stderr)
        return 1

    print(f"[smoke] OK chunks={len(parsed)} model={model}")
    print(f"[smoke] sample={json.dumps(sample, ensure_ascii=False)[:200]}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=os.environ.get("SMOKE_MODEL", DEFAULT_MODEL))
    p.add_argument("--ollama", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    p.add_argument("--timeout", type=float, default=float(os.environ.get("SMOKE_TIMEOUT", "60")))
    args = p.parse_args()
    try:
        return asyncio.run(_run(args.model, args.ollama, args.timeout))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[smoke] internal error: {e!r}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
