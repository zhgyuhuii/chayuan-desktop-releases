"""模态分发入口。

调用方:``api_server/openai_routes.py`` 的 ``/v1/chat/completions`` 在
``is_enabled() == True`` 且 ``capability != chat`` 时把请求转过来。
本模块产出 ``AsyncIterator[dict]``(Vercel v5 事件流);调用方负责
``sse_encode`` + 通过 EventSourceResponse 推给前端。

行为:
  - 按 ``(req.capability, req.platform_type)`` 查 Connector;未注册返友好错
  - Connector 抛异常 → 翻译为 v5 ``error`` 事件 + ``finish``
  - 所有流以 ``start`` 起、``finish`` 终
  - 不 catch ``asyncio.CancelledError`` —— 让上层 SSE 中间件正确收尾

不做的:
  - 鉴权 / 配额(放在 HTTP 层中间件)
  - SSE 编码(``sse_encode`` 调用方做)
  - 日志/审计/计费(后续在 dispatch 周围加 hook,不动这里)

PR-9 后:``dispatch_via_manager`` 是新主路径 —— 落库 task,跑后台 asyncio.Task,
SSE 订阅 event_bus。客户端断开不会取消任务,刷新页面能续看。``dispatch`` 保留为
兼容入口(走 manager 但 await 完成)— 但 manager-aware 客户端建议直接用
``dispatch_via_manager`` 拿 task_id。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import AsyncIterator, Dict, Tuple

from chayuan.server.modality.router.connectors.base import pick_connector
from chayuan.server.modality.router.protocol import Capability, GenerateReq
from chayuan.server.modality.router.sse_v5 import (
    data_part,
    error_event,
    finish_event,
    finish_step_event,
    start_event,
)

logger = logging.getLogger("chayuan.modality.router")


# ──────────────────────────────────────────────────────────────
# Feature flag
# ──────────────────────────────────────────────────────────────


def is_enabled() -> bool:
    """新路径总开关。

    PR-5 起默认 **ON** — PR-1~PR-4 已经端到端走通,旁路逻辑稳定,
    继续 OFF 会让用户选了 qwen-image-max 直接撞 503,体验割裂。

    只有显式 ``CHAYUAN_MODALITY_NEW_PATH=0`` / ``=false`` / ``=off`` /
    ``=no`` 才关 — 留个紧急回滚旋钮。
    """
    val = os.environ.get("CHAYUAN_MODALITY_NEW_PATH", "1").strip().lower()
    return val not in ("0", "false", "off", "no", "")


# ──────────────────────────────────────────────────────────────
# Dispatch
# ──────────────────────────────────────────────────────────────


async def dispatch(req: GenerateReq) -> AsyncIterator[Dict]:
    """按 capability/platform 路由到 Connector,统一吐 v5 事件。

    总以 ``start`` 起、``finish`` 终(包括失败路径),便于前端状态机
    收尾(关 spinner、写消息状态)。
    """
    message_id = req.message_id or f"msg-{uuid.uuid4().hex[:12]}"
    yield start_event(message_id=message_id)
    # 给前端一条 modality 元信息,UI 可据此切渲染模式
    yield data_part(
        "modality-meta",
        {
            "capability": req.capability.value,
            "model": req.model,
            "platform_name": req.platform_name,
            "platform_type": req.platform_type,
        },
    )

    cls = pick_connector(req.capability, req.model, req.platform_type)
    if cls is None:
        logger.warning(
            "[modality.router] 未注册的 connector: cap=%s platform_type=%s model=%s",
            req.capability.value, req.platform_type, req.model,
        )
        yield error_event(
            f"模态路由器还没接入 {req.capability.value}/{req.platform_type} 连接器。\n"
            f"  • 模型「{req.model}」需要专属上游协议(非 OpenAI 兼容)\n"
            f"  • 后续版本会逐步补齐;现在请到「模型广场」改用同 capability 的其它平台,"
            f"或换走对话模型 + 工具(画图工具)流",
            code="connector_not_registered",
        )
        yield finish_step_event()
        yield finish_event()
        return

    connector = cls()
    try:
        async for ev in connector.generate(req):
            yield ev
    except asyncio.CancelledError:
        # SSE 断开 — 不要吞,让 finally 跑完后 re-raise
        logger.info("[modality.router] dispatch cancelled: model=%s", req.model)
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "[modality.router] connector failed: cap=%s platform=%s model=%s err=%r",
            req.capability.value, req.platform_type, req.model, e,
        )
        yield error_event(f"{type(e).__name__}: {e}", code="connector_error")
    finally:
        yield finish_step_event()
        yield finish_event()


# ──────────────────────────────────────────────────────────────
# PR-9: TaskManager 路径
# ──────────────────────────────────────────────────────────────


async def dispatch_via_manager(req: GenerateReq) -> Tuple[str, AsyncIterator[Dict]]:
    """新主路径 — 把请求落 task 表 → 后台 asyncio.Task 跑 → 返 (task_id, 事件流)。

    与 ``dispatch`` 的本质区别:
      - 后台 Task 不绑订阅者生命周期;SSE 断开 → 任务继续跑
      - 同一个 task_id 可以被多次订阅(replay 历史 buffer + 续 live)

    调用方:openai_routes.create_modality_completion;直接把返回流交给
    EventSourceResponse。需要给前端透 task_id 用于"刷新页面续看"时,
    取第一个返回值。
    """
    from chayuan.server.modality.router.tasks import manager as task_mgr

    task_id = await task_mgr.create_task(req)
    # 后台 Task 启动 — ensure_running 保 _running 字典持有强引用防 GC
    task_mgr.ensure_running(task_id, req)
    # subscribe 拿 AsyncIterator(replay buffer + live)
    return task_id, task_mgr.subscribe(task_id)
