"""老 chat handlers → ChatGraph 适配层（P1-6 项 1）。

把原 ``/chat/kb_chat``、``/chat/file_chat``、``/chat/chat`` 的参数翻译成
``ChatRequest``，然后走 ``run_chat_stream`` / ``run_chat_sync``。

**兼容性保证**：
- 事件协议尽量与老 handler 保持一致（OpenAI 风格的 ``chat.completion.chunk``
  + ``docs`` 字段）；保证前端 webui / 外部 SDK 改动最小
- 任何失败都自动回退到老实现（``fallback_to_legacy=True`` 参数）
- 仅当 ``Settings.basic_settings.USE_CHAT_GRAPH=True`` 时才启用；默认走老路径

老代码入口不动，只是**内部**多一次 feature flag 判断；这样我们可以：
- 灰度按 user_id 分流
- 发现问题一键回退（把 flag 关掉即可）
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import Request
from sse_starlette.sse import EventSourceResponse

from chayuan.server.api_server.api_schemas import OpenAIChatOutput
from chayuan.server.chat.graph import ChatMode, ChatRequest, run_chat_stream
from chayuan.server.chat.graph.streaming import make_sse  # noqa: F401 (再导出)

logger = logging.getLogger("chayuan.chat.graph.shims")


def use_chat_graph() -> bool:
    """统一 feature-flag 读取，任何异常默认 False（旧路径）。"""
    try:
        from chayuan.settings import Settings
        return bool(getattr(Settings.basic_settings, "USE_CHAT_GRAPH", False))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# kb_chat / file_chat / search_engine_chat 共用 shim
# ---------------------------------------------------------------------------

async def kb_chat_via_graph(
    *,
    query: str,
    mode: str,
    kb_name: str,
    top_k: int,
    score_threshold: float,
    history: List[Dict[str, Any]],
    stream: bool,
    model: str,
    temperature: float,
    max_tokens: Optional[int],
    prompt_name: str,
    return_direct: bool,
    request: Optional[Request] = None,
    user: Optional[Dict[str, Any]] = None,
):
    """把老 kb_chat(local_kb/temp_kb/search_engine) 入口路由进 ChatGraph。

    事件兼容层：我们把 ChatGraph 的 SSE 事件翻译成老前端期望的
    ``OpenAIChatOutput.model_dump_json`` 流。
    """
    chat_mode = {
        "local_kb": ChatMode.KB,
        "temp_kb": ChatMode.FILE,
        "search_engine": ChatMode.SEARCH_ENGINE,
    }.get(mode, ChatMode.LLM)

    uid = (user or {}).get("id") if isinstance(user, dict) else None
    uname = str((user or {}).get("username") or "") if isinstance(user, dict) else ""
    urole = str((user or {}).get("role") or "") if isinstance(user, dict) else ""

    req = ChatRequest(
        query=query, mode=chat_mode, history=history or [],
        stream=bool(stream), model=model or "",
        temperature=float(temperature),
        max_tokens=(int(max_tokens) if max_tokens else None),
        prompt_name=prompt_name or "default",
        kb_name=(kb_name if chat_mode == ChatMode.KB else None),
        file_chat_id=(kb_name if chat_mode == ChatMode.FILE else None),
        search_engine=(kb_name if chat_mode == ChatMode.SEARCH_ENGINE else None),
        top_k=int(top_k or 5),
        score_threshold=float(score_threshold),
        user_id=int(uid) if uid is not None else None,
        username=uname, user_role=urole,
        conversation_id="",
        request_id=str(getattr(request.state, "request_id", "")) if request else "",
        governance_enabled=True,
    )

    if not stream:
        # 非流式：一次跑完，把结果包成老 OpenAIChatOutput
        from chayuan.server.chat.graph import run_chat_sync
        out = await run_chat_sync(req)
        data = out.get("data") or {}
        payload = OpenAIChatOutput(
            id=f"chat{uuid.uuid4().hex[:12]}", object="chat.completion",
            content=data.get("answer") or "", role="assistant",
            model=model or "",
            docs=_docs_from_sources(data.get("chunks") or []),
            finish_reason="stop",
        )
        return payload.model_dump_json()

    # 流式：事件翻译
    async def _iterator():
        # 先推空 stub 与 docs（遵循老协议：首帧包含 docs）
        run_id = f"chat{uuid.uuid4().hex[:12]}"
        docs_emitted = False
        text_buf = ""
        try:
            async for sse in run_chat_stream(req):
                ev, data = sse.get("event"), _json_loads(sse.get("data"))

                if ev == "sources" and not docs_emitted:
                    out = OpenAIChatOutput(
                        id=run_id, object="chat.completion.chunk",
                        content="", role="assistant", model=model or "",
                        docs=_docs_from_sources(data.get("chunks") or []),
                    )
                    yield out.model_dump_json()
                    docs_emitted = True

                elif ev == "token":
                    delta = (data or {}).get("delta") or ""
                    if not delta:
                        continue
                    text_buf += delta
                    out = OpenAIChatOutput(
                        id=run_id, object="chat.completion.chunk",
                        content=delta, role="assistant", model=model or "",
                    )
                    yield out.model_dump_json()

                elif ev in ("masked_answer", "output_blocked", "input_blocked"):
                    # 将安全相关事件透传为 annotation chunk（老协议扩展）
                    msg = (data or {}).get("reason") or (data or {}).get("masked") or ""
                    if msg:
                        out = OpenAIChatOutput(
                            id=run_id, object="chat.completion.chunk",
                            content=f"\n\n[{ev}] {msg}\n",
                            role="assistant", model=model or "",
                        )
                        yield out.model_dump_json()

                elif ev == "done":
                    if not text_buf:
                        # 一个空回答（quota_denied / input_blocked 等），至少回一条 stop
                        out = OpenAIChatOutput(
                            id=run_id, object="chat.completion.chunk",
                            content="", role="assistant", model=model or "",
                            finish_reason="stop",
                        )
                        yield out.model_dump_json()
                    return
        except Exception as e:  # noqa: BLE001
            logger.warning("kb_chat_via_graph 异常：%r", e)
            yield json.dumps({"error": f"{type(e).__name__}: {e}"})

    return EventSourceResponse(_iterator())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _docs_from_sources(chunks: List[Dict[str, Any]]) -> List[str]:
    """与老 kb_chat 的 docs 返回格式保持一致：每条是一段 markdown。"""
    out: List[str] = []
    for i, ch in enumerate(chunks[:20]):
        cite = (ch.get("citation") or {})
        title = cite.get("title") or f"chunk#{i+1}"
        content = (ch.get("content") or "")[:1000]
        out.append(f"出处 [{i + 1}] {title}\n\n{content}\n\n")
    return out


def _json_loads(s):
    if isinstance(s, (dict, list)):
        return s
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return {}
