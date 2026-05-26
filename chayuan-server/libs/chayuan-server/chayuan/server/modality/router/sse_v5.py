"""Vercel AI SDK v5 UI Message Stream Protocol — Python 端 helpers。

规范:
  - 传输:SSE,每条事件 ``data: <json>\\n\\n``
  - 事件 JSON 含 ``type`` 字段标识种类,其它字段按 type 分别约定
  - 完整事件列表见 https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol#ui-message-stream-protocol

我们用到的类型(后续 Connector 按需 yield):
  start          流首
  text-start     文本块起始(id)
  text-delta     文本 token 增量(id, delta)
  text-end       文本块结束(id)
  file           文件/图/音/视产物(mediaType, url, [metadata])
  source-url     URL 引用
  source-document KB 文档引用
  data-<name>    自定义 typed data(我们用:data-task-progress / data-usage / data-modality-meta)
  tool-input-start / -delta / -available / tool-output-available  工具调用(老 agent 链路用)
  error          错误(errorText)
  finish-step    步骤结束
  finish         流终止

工厂函数都返回纯 dict,方便测试 + JSON 序列化。SSE 编码在 ``sse_encode``。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


# ──────────────────────────────────────────────────────────────
# 事件工厂(返回纯 dict)
# ──────────────────────────────────────────────────────────────


def start_event(message_id: Optional[str] = None) -> Dict[str, Any]:
    ev: Dict[str, Any] = {"type": "start"}
    if message_id:
        ev["messageId"] = message_id
    return ev


def text_start(id: str) -> Dict[str, Any]:
    return {"type": "text-start", "id": id}


def text_delta(id: str, delta: str) -> Dict[str, Any]:
    return {"type": "text-delta", "id": id, "delta": delta}


def text_end(id: str) -> Dict[str, Any]:
    return {"type": "text-end", "id": id}


def file_part(
    media_type: str,
    url: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """v5 file part — 用于图/音/视/PDF 等产物。

    metadata 是我们的扩展(v5 协议在 file part 上接收任意附加字段;
    放进 ``metadata`` 子键便于 frontend 解构 + 老 v5 client 也能直读 url/mediaType)。
    """
    ev: Dict[str, Any] = {"type": "file", "mediaType": media_type, "url": url}
    if metadata:
        ev["metadata"] = metadata
    return ev


def source_url(source_id: str, url: str, title: Optional[str] = None) -> Dict[str, Any]:
    ev: Dict[str, Any] = {"type": "source-url", "sourceId": source_id, "url": url}
    if title:
        ev["title"] = title
    return ev


def source_document(
    source_id: str, media_type: str, title: str,
) -> Dict[str, Any]:
    return {
        "type": "source-document",
        "sourceId": source_id,
        "mediaType": media_type,
        "title": title,
    }


def data_part(name: str, data: Any, id: Optional[str] = None) -> Dict[str, Any]:
    """v5 typed data part — type 写作 ``data-<name>``。

    约定的几个 name:
      task-progress   {percent: 0-100, eta_s?: int, message?: str}
      usage           {tokens_in?, tokens_out?, image_count?, seconds?, cost_cny?}
      modality-meta   {capability: str, model: str, platform: str}
    """
    ev: Dict[str, Any] = {"type": f"data-{name}", "data": data}
    if id is not None:
        ev["id"] = id
    return ev


def error_event(text: str, code: Optional[str] = None) -> Dict[str, Any]:
    ev: Dict[str, Any] = {"type": "error", "errorText": text}
    if code:
        # code 不是 v5 标准字段,但 v5 允许 error 上附加;前端可读
        ev["code"] = code
    return ev


def finish_step_event() -> Dict[str, Any]:
    return {"type": "finish-step"}


def finish_event() -> Dict[str, Any]:
    return {"type": "finish"}


# ──────────────────────────────────────────────────────────────
# SSE 编码
# ──────────────────────────────────────────────────────────────


def sse_encode(event: Dict[str, Any]) -> str:
    """把事件 dict 编码成单帧 SSE。

    v5 协议要求 ``data: <json>\\n\\n``;不需要 event 行,type 在 JSON 里。
    """
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# v5 终止哨兵(老 OpenAI 兼容客户端也认 ``[DONE]``;v5 自己不强制要求,
# 但加上不亏 — 让 SDK 的 finish reason 处理更稳)
SSE_DONE = "data: [DONE]\n\n"
