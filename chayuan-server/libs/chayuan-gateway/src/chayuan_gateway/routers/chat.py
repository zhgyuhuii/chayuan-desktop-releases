from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from chayuan_gateway.deps import get_repo
from chayuan_registry import ModelRepository
from chayuan_runtime import AdapterRequest, pick_adapter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


# 与 OpenAI 官方一致的 SSE 头：
#   - text/event-stream 强制告诉客户端这是 SSE
#   - X-Accel-Buffering: no  让 nginx / 反向代理别缓冲整段响应
#   - Cache-Control: no-cache 避免中间层缓存
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _format_sse(chunk: Any) -> bytes:
    """Render one OpenAI-compatible SSE event line.

    OpenAI 的 wire format 是裸 ``data: <json>\\n\\n``，**没有** ``event:`` 字段；
    客户端（openai-python / openai-node / langchain.sse）都按这个格式解析。
    所以这里我们只输出 ``data:`` 行；不要套 ``event_source_response`` 加 ``event:`` 头。
    """
    if isinstance(chunk, (bytes, bytearray)):
        # 适配器已经把整行 ``data: ...\n\n`` 给我们了，原样透传
        return bytes(chunk)
    if isinstance(chunk, str):
        # 字符串视为已经序列化的 JSON 内容
        body = chunk
    else:
        body = json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
    return f"data: {body}\n\n".encode("utf-8")


def _passthrough_iter(body: Iterator[Any]) -> Iterator[bytes]:
    """Pass adapter chunks straight through as OpenAI-compatible SSE bytes."""
    try:
        for chunk in body:
            if chunk is None:
                continue
            yield _format_sse(chunk)
    except Exception as e:  # pragma: no cover - protective; logged for ops
        logger.warning("[chat-stream] upstream adapter raised: %r", e)
        err_chunk = {
            "error": {
                "type": "upstream_error",
                "message": f"{type(e).__name__}: {e}",
            }
        }
        yield _format_sse(err_chunk)
    finally:
        # 终止帧：OpenAI 协议明确要求最后一行是 ``data: [DONE]``
        yield b"data: [DONE]\n\n"


@router.post("/v1/chat/completions")
async def chat_completions(payload: dict[str, Any] = Body(...),
                           repo: ModelRepository = Depends(get_repo)):
    model_id = payload.get("model")
    if not model_id:
        raise HTTPException(400, detail="missing 'model'")
    m = repo.get(model_id)
    if m is None:
        raise HTTPException(404, detail=f"unknown model: {model_id}")
    if m.category != "chat":
        raise HTTPException(409, detail=f"model '{model_id}' is not chat (category={m.category})")
    adapter = pick_adapter(m)
    if adapter is None:
        raise HTTPException(503, detail=f"no adapter for runtime={m.runtime}")
    stream = bool(payload.get("stream"))
    req = AdapterRequest(op="chat", model=m, payload=payload, stream=stream)
    resp = adapter.call(req)
    if not stream:
        return resp.body

    # 用 StreamingResponse 而不是 EventSourceResponse：
    # * EventSourceResponse 会强制加上 ``event: message`` 之类的头，OpenAI 客户端不识别
    # * StreamingResponse + media_type="text/event-stream" 就是 OpenAI / vLLM 服务端的标准做法
    # * 同步生成器会被 Starlette 自动 iterate_in_threadpool 调度，不会阻塞 event loop
    return StreamingResponse(
        _passthrough_iter(resp.body),
        media_type="text/event-stream; charset=utf-8",
        headers=_SSE_HEADERS,
    )
