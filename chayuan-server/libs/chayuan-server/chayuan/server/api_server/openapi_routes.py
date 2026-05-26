"""开放平台路由（inbound：外部应用 → Chayuan）。

与面向终端用户的普通 ``/`` 路由分家，挂在 ``/openapi/v1/*``。
每个请求都必须带 ``X-App-Id / X-Timestamp / X-Sign`` 三件套，由
``AppAuthMiddleware`` 校验；成功后 ``request.state.app`` 带上 ``AppSpec``。

本文件先提供最小闭环的几个端点：

- ``GET /openapi/v1/ping``
  健康探测；所有正确签名请求都能拿到 ``{"ok": true, "app_id": ..., "name": ...}``；
  用来验证 app_id/secret 是否有效、回调时钟同步是否 OK。
- ``POST /openapi/v1/echo``
  回声接口；返回整个 body，用来调试客户端签名实现。

后续可以在这个 router 上挂 chat / kb / tools 的封装端点，共用同一套鉴权。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chayuan.server.shared.app_signing import verify
from chayuan.server.config_panel.apps_store import AppSpec
from chayuan.server.api_server.openapi_deps import require_scopes


logger = logging.getLogger("chayuan.openapi")

openapi_router = APIRouter(
    prefix="/openapi/v1",
    tags=["OpenAPI · 对外接口（需签名）"],
    responses={
        401: {"description": "签名无效 / App 禁用 / 鉴权失败"},
        403: {"description": "App scope 不足"},
        429: {"description": "达到限流阈值"},
        503: {"description": "DB 未就绪或后端降级"},
    },
)


# ---------------------------------------------------------------------------
# Response Models —— 让 Swagger 正确展示返回结构
# ---------------------------------------------------------------------------

class PingResponse(BaseModel):
    ok: bool = True
    app_id: str
    name: str
    scopes: List[str]
    server_time: int = Field(..., description="服务器当前 Unix 秒")


class EchoResponse(BaseModel):
    ok: bool = True
    echo: Any
    app_id: str


class ScopesResponse(BaseModel):
    """当前 App 的 scope + 全部可申请 scope 说明。"""
    current: List[str]
    all: List[str]
    groups: dict


class EventScopeMapResponse(BaseModel):
    """事件 → 订阅所需 scope 的映射，供对接方自查。"""
    events: dict


class WhoAmIResponse(BaseModel):
    app_id: str
    name: str
    scopes: List[str]
    callback_url: str
    callback_events: List[str]
    enabled: bool
    created_at: str


class ServerTimeResponse(BaseModel):
    server_time: int
    iso: str


class AppAuthMiddleware:
    """ASGI 中间件：只在 ``/openapi/v1/`` 前缀上生效。

    用原生 ASGI 接口而不是 ``BaseHTTPMiddleware``，原因：
    需要读 body 计算签名，还要把同一份 body 完整传给下游；``BaseHTTPMiddleware``
    会把响应包一层 stream，读过 body 后下游 FastAPI 看不到。ASGI 层直接拦
    ``receive`` callable，先读完 body、校验完签名、再重放给下游，最稳。

    失败响应：
    - 400：缺少 header；
    - 401：app_id 不存在 / 禁用 / 签名不匹配 / 时间戳漂移过大。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "") or ""
        if not path.startswith("/openapi/v1/"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin1").lower(): v.decode("latin1")
                   for k, v in (scope.get("headers") or [])}
        app_id = headers.get("x-app-id", "")
        timestamp = headers.get("x-timestamp", "")
        given_sign = headers.get("x-sign", "")

        if not app_id or not timestamp or not given_sign:
            await _send_error(send, 400, "missing X-App-Id / X-Timestamp / X-Sign")
            return

        try:
            from chayuan.server.config_panel.apps_store import get_app
        except Exception as e:  # noqa: BLE001
            logger.exception("apps_store import failed")
            await _send_error(send, 500, f"apps store error: {e}")
            return

        app_spec = get_app(app_id)
        if not app_spec:
            await _send_error(send, 401, "unknown app_id")
            return
        if not app_spec.enabled:
            await _send_error(send, 401, "app disabled")
            return

        # 读完整 body，计算签名；随后把 body 重放给下游
        body_chunks: list[bytes] = []
        more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.request":
                body_chunks.append(msg.get("body", b"") or b"")
                more = bool(msg.get("more_body", False))
            elif msg["type"] == "http.disconnect":
                return
            else:
                more = False
        body_bytes = b"".join(body_chunks)

        ok, reason = verify(app_spec.app_secret, timestamp, body_bytes, given_sign)
        if not ok:
            logger.warning("openapi auth failed: app_id=%s reason=%s", app_id, reason)
            await _send_error(send, 401, f"invalid signature: {reason}")
            return

        # 把 AppSpec 暴露给下游 endpoint 通过 request.state 拿到
        scope.setdefault("state", {})["app"] = app_spec

        # 重放 body
        sent = {"done": False}

        async def _replay_receive() -> Message:
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            return await receive()

        await self.app(scope, _replay_receive, send)


async def _send_error(send: Send, code: int, msg: str) -> None:
    body = json.dumps({"code": code, "msg": msg}, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start", "status": code,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body, "more_body": False})


# ---------------------------------------------------------------------------
# 端点：每个都显式声明 scope + response_model + summary，便于 Swagger 自动出文档
# ---------------------------------------------------------------------------

@openapi_router.get(
    "/ping",
    response_model=PingResponse,
    summary="健康探测（无 scope 要求）",
    description="只要签名合法且 App 启用就返回 200；对接方用来验证自己签名实现是否正确。",
)
async def ping(app: AppSpec = Depends(require_scopes())) -> PingResponse:
    return PingResponse(
        ok=True, app_id=app.app_id, name=app.name,
        scopes=list(app.scopes or []),
        server_time=int(time.time()),
    )


@openapi_router.post(
    "/echo",
    response_model=EchoResponse,
    summary="回声（无 scope 要求）",
    description="把请求 body 原样回显。参与签名的 raw body 必须 byte-level 一致。",
)
async def echo(
    request: Request,
    app: AppSpec = Depends(require_scopes()),
) -> EchoResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = (await request.body()).decode("utf-8", "replace")
    return EchoResponse(ok=True, echo=body, app_id=app.app_id)


@openapi_router.get(
    "/server-time",
    response_model=ServerTimeResponse,
    summary="返回服务器时间",
    description="对接方校准时钟；签名的 timestamp 对齐 ±5 分钟内才合法。",
)
async def server_time(
    _app: AppSpec = Depends(require_scopes()),
) -> ServerTimeResponse:
    now = int(time.time())
    from datetime import datetime, timezone
    return ServerTimeResponse(
        server_time=now,
        iso=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
    )


@openapi_router.get(
    "/whoami",
    response_model=WhoAmIResponse,
    summary="返回当前 App 的基本信息（需 admin:read）",
    description="用于运营后台查看对接方信息；普通接入方应避免授予此 scope。",
)
async def whoami(
    app: AppSpec = Depends(require_scopes("admin:read")),
) -> WhoAmIResponse:
    return WhoAmIResponse(
        app_id=app.app_id, name=app.name,
        scopes=list(app.scopes or []),
        callback_url=app.callback_url or "",
        callback_events=list(app.callback_events or []),
        enabled=app.enabled,
        created_at=app.created_at or "",
    )


@openapi_router.get(
    "/scopes",
    response_model=ScopesResponse,
    summary="列出当前 App scope + 全平台可申请 scope",
    description="对接方用来自检缺哪些 scope；面板发起「申请 scope」流程时也能用。",
)
async def list_scopes(
    app: AppSpec = Depends(require_scopes()),
) -> ScopesResponse:
    from chayuan.server.shared.scopes import ALL_SCOPES, SCOPE_GROUPS
    return ScopesResponse(
        current=list(app.scopes or []),
        all=list(ALL_SCOPES),
        groups={k: list(v) for k, v in SCOPE_GROUPS.items()},
    )


@openapi_router.get(
    "/events",
    response_model=EventScopeMapResponse,
    summary="列出事件 → 订阅 scope 映射",
    description="告诉对接方订阅某事件需要哪个 scope；WS `/openapi/v1/ws` 同样按此过滤。",
)
async def list_events(
    _app: AppSpec = Depends(require_scopes()),
) -> EventScopeMapResponse:
    from chayuan.server.shared.scopes import EVENT_SCOPE_MAP
    return EventScopeMapResponse(events=dict(EVENT_SCOPE_MAP))


# ---- chat 示例（需要 chat 相关 scope） -----------------------------------

class ChatHistoryItem(BaseModel):
    role: str
    content: str
    ts: int


class ChatHistoryResponse(BaseModel):
    conversation_id: str
    messages: List[ChatHistoryItem]


@openapi_router.get(
    "/chat/history",
    response_model=ChatHistoryResponse,
    summary="拉取对话历史（需 chat:read）",
    description="开放平台最常见的只读能力。示例实现返回固定的空列表；"
                "真实接入时应注入鉴权后的用户上下文再查库。",
)
async def chat_history(
    conversation_id: str,
    _app: AppSpec = Depends(require_scopes("chat:read")),
) -> ChatHistoryResponse:
    return ChatHistoryResponse(conversation_id=conversation_id, messages=[])


class ChatCompleteRequest(BaseModel):
    conversation_id: Optional[str] = Field(None, description="可选的会话 ID")
    prompt: str = Field(..., description="用户输入的 prompt")
    model: Optional[str] = Field(None, description="指定的 LLM 模型名")


class ChatCompleteResponse(BaseModel):
    conversation_id: str
    answer: str


@openapi_router.post(
    "/chat/complete",
    response_model=ChatCompleteResponse,
    summary="同步发起一次对话（需 chat:write）",
    description="示例端点，真实实现应转发到 /chat 或 chat.completion 内部服务。",
)
async def chat_complete(
    req: ChatCompleteRequest,
    _app: AppSpec = Depends(require_scopes("chat:write")),
) -> ChatCompleteResponse:
    cid = req.conversation_id or "demo-" + str(int(time.time()))
    return ChatCompleteResponse(
        conversation_id=cid,
        answer=f"[demo] echo your prompt: {req.prompt[:80]}",
    )


# ---- kb：开放平台 KB 接入（plan v1.3 §4.3 完整实现，替代原 placeholder） ----

def _spec_to_user(app: AppSpec) -> dict:
    """把 AppSpec 合成为 access.py 能识别的 user dict（id="app:<app_id>"）。"""
    return {
        "id":        f"app:{app.app_id}",
        "username":  f"app:{app.name or app.app_id}",
        "role":      "app",
        "app_id":    app.app_id,
        "scopes":    list(app.scopes or []),
    }


class _KBListItem(BaseModel):
    kb_name: str
    visibility: str = "private"
    role: str = Field(..., description="reader / editor / owner（应用账号无 owner）")
    file_count: int = 0


class _KBListResponse(BaseModel):
    data: List[_KBListItem]


@openapi_router.get(
    "/kb/list_knowledge_bases",
    response_model=_KBListResponse,
    summary="列出当前 App 有权访问的知识库（需 kb:read）",
    description="只返回 grant + (public 且 App 有 kb:public-read) 的 KB，过滤已过期 grant。",
)
async def list_kbs_for_app(
    app: AppSpec = Depends(require_scopes("kb:read")),
) -> _KBListResponse:
    from chayuan.server.auth.app_acl import (
        list_app_accessible_kbs, app_kb_role,
    )
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    from chayuan.server.db.session import session_scope

    names = list_app_accessible_kbs(app)
    items: List[_KBListItem] = []
    if names:
        with session_scope() as s:
            rows = s.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.kb_name.in_(names)).all()
            for kb in rows:
                items.append(_KBListItem(
                    kb_name=kb.kb_name,
                    visibility=kb.visibility or "private",
                    role=app_kb_role(app, kb.kb_name) or "reader",
                    file_count=int(getattr(kb, "file_count", 0) or 0),
                ))
    for ku_id in getattr(app, "ku_ids", None) or []:
        items.append(_KBListItem(
            kb_name=str(ku_id),
            visibility="private",
            role="reader",
            file_count=0,
        ))
    return _KBListResponse(data=items)


def _app_can_read_search_target(app: AppSpec, target: str) -> bool:
    from chayuan.server.auth.app_acl import app_can_read_kb

    name = str(target or "").strip()
    if not name:
        return False
    if name.startswith("doc:"):
        name = name.split(":", 1)[1]
    if name.startswith("src:"):
        return name in set(getattr(app, "ku_ids", None) or [])
    return app_can_read_kb(app, name)


def _tool_allowed_for_app(app: AppSpec, tool_name: str) -> bool:
    allowed = set(str(x) for x in (getattr(app, "tool_ids", None) or []))
    return "*" in allowed or str(tool_name or "") in allowed


@openapi_router.post(
    "/kb/search_batch",
    summary="批量子查询并行检索（需 kb:read）",
    description=(
        "与 /knowledge_base/search_batch 完全等价但走 HMAC 通道。\n"
        "ACL：每个 KB 都过 app_acl.app_can_read_kb；无权 KB 直接 403。\n"
        "返回 chunk 含 download_token，aud 绑定到当前 app:<app_id>，跨 App 复用即 401。"
    ),
)
async def kb_search_batch_for_app(
    request: Request,
    app: AppSpec = Depends(require_scopes("kb:read")),
):
    from pydantic import ValidationError as _PydErr
    from fastapi import HTTPException as _HTTP
    from chayuan.server.file_rag import batch_search as _bs
    from chayuan.server.auth import kb_audit
    from chayuan.server.observability.audit import AuditStopwatch
    from chayuan.server.api_server.kb_routes import _run_mixed_search_batch

    rid = request.headers.get("x-request-id", "")
    user_dict = _spec_to_user(app)
    # App "first call in a session" 视为一次 login(轻量;每次请求都记一次也没关系)
    kb_audit.app_login(user_dict, request_id=rid, endpoint="/openapi/v1/kb/search_batch")

    raw = await request.json()
    try:
        body = _bs.SearchBatchIn(**(raw or {}))
    except _PydErr as e:
        raise _HTTP(status_code=400, detail={"code": 4001, "msg": "invalid params",
                                             "errors": e.errors()})
    except (ValueError, TypeError) as e:
        raise _HTTP(status_code=400, detail={"code": 4001, "msg": str(e)})

    from chayuan.server.observability import metrics as _metrics
    for kb in body.knowledge_base_names:
        if not _app_can_read_search_target(app, kb):
            kb_audit.denied(user_dict, kb=kb, action="read", code=4032,
                            reason=f"app no kb_grant", request_id=rid)
            _metrics.record_kb_denied("app", 4032, "read")
            raise _HTTP(status_code=403,
                        detail={"code": 4032,
                                "msg": f"app {app.app_id!r} no read grant on kb {kb!r}"})

    with AuditStopwatch() as w:
        try:
            out = await _run_mixed_search_batch(body, user=user_dict, subject_for_token=user_dict)
        except Exception:
            _metrics.record_kb_search("app", body.fusion or "rrf", "error",
                                      duration_s=w.elapsed_ms / 1000.0 if hasattr(w, "elapsed_ms") else 0.0,
                                      sub_queries=len(body.queries), chunks_returned=0)
            raise
    kb_audit.search_ok(user_dict,
                       kbs=body.knowledge_base_names,
                       queries=len(body.queries),
                       chunks=len(out.merged),
                       elapsed_ms=w.elapsed_ms,
                       request_id=rid)
    _metrics.record_kb_search("app", body.fusion or "rrf", "ok",
                              duration_s=w.elapsed_ms / 1000.0,
                              sub_queries=len(body.queries), chunks_returned=len(out.merged))
    return out


@openapi_router.post(
    "/kb/search_batch_stream",
    summary="批量子查询的 SSE 流式版（需 kb:read）",
)
async def kb_search_batch_stream_for_app(
    request: Request,
    app: AppSpec = Depends(require_scopes("kb:read")),
):
    from pydantic import ValidationError as _PydErr
    from fastapi import HTTPException as _HTTP
    from fastapi.responses import StreamingResponse
    import json as _json
    from chayuan.server.file_rag import batch_search as _bs
    from chayuan.server.auth import kb_audit
    from chayuan.server.api_server.kb_routes import _run_mixed_search_batch, _split_search_targets

    rid = request.headers.get("x-request-id", "")
    user_dict = _spec_to_user(app)
    kb_audit.app_login(user_dict, request_id=rid, endpoint="/openapi/v1/kb/search_batch_stream")

    raw = await request.json()
    try:
        body = _bs.SearchBatchIn(**(raw or {}))
    except _PydErr as e:
        raise _HTTP(status_code=400, detail={"code": 4001, "msg": "invalid params",
                                             "errors": e.errors()})
    except (ValueError, TypeError) as e:
        raise _HTTP(status_code=400, detail={"code": 4001, "msg": str(e)})

    for kb in body.knowledge_base_names:
        if not _app_can_read_search_target(app, kb):
            kb_audit.denied(user_dict, kb=kb, action="read", code=4032,
                            reason="app no kb_grant (stream)", request_id=rid)
            raise _HTTP(status_code=403,
                        detail={"code": 4032,
                                "msg": f"app {app.app_id!r} no read grant on kb {kb!r}"})

    async def _event_stream():
        chunk_count = 0
        try:
            doc_names, ku_ids = _split_search_targets(body.knowledge_base_names)
            if ku_ids:
                yield f"data: {_json.dumps({'type': 'embed_done', 'kbs': len(doc_names) + len(ku_ids), 'queries': len(body.queries)}, ensure_ascii=False)}\n\n"
                out = await _run_mixed_search_batch(body, user=user_dict, subject_for_token=user_dict)
                chunk_count = len(out.merged)
                summary = out.summary.model_dump() if hasattr(out.summary, "model_dump") else out.summary.dict()
                yield f"data: {_json.dumps({'type': 'results', 'merged': out.merged, 'summary': summary, 'errors': out.errors}, ensure_ascii=False, default=str)}\n\n"
                yield f"data: {_json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            else:
                async for frame in _bs.run_batch_stream(body, user=user_dict, subject_for_token=user_dict):
                    if frame.get("type") == "results":
                        chunk_count = len(frame.get("merged") or [])
                    yield f"data: {_json.dumps(frame, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {_json.dumps({'type': 'error', 'message': repr(e)}, ensure_ascii=False)}\n\n"
        finally:
            kb_audit.search_ok(user_dict,
                               kbs=body.knowledge_base_names,
                               queries=len(body.queries),
                               chunks=chunk_count,
                               request_id=rid)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@openapi_router.get(
    "/kb/download_doc",
    summary="下载知识库文件（需 kb:read）",
    description=(
        "与 /knowledge_base/download_doc 等价；HMAC 通道使用方便对接方直接走 OpenAPI 下载。\n"
        "也支持 ?token=<dl_token>（与 JWT 路径相同），但 token 的 aud 必须 = 当前 app:<app_id>。"
    ),
)
async def kb_download_doc_for_app(
    request: Request,
    knowledge_base_name: str,
    file_name: str,
    preview: bool = False,
    app: AppSpec = Depends(require_scopes("kb:read")),
):
    from fastapi import HTTPException as _HTTP
    from chayuan.server.auth.app_acl import app_can_read_kb
    from chayuan.server.auth import kb_audit
    from chayuan.server.knowledge_base.kb_doc_api import download_doc

    rid = request.headers.get("x-request-id", "")
    user_dict = _spec_to_user(app)

    from chayuan.server.observability import metrics as _metrics
    if not app_can_read_kb(app, knowledge_base_name):
        kb_audit.denied(user_dict, kb=knowledge_base_name, action="download",
                        code=4032, reason="app no kb_grant", request_id=rid)
        _metrics.record_kb_denied("app", 4032, "download")
        raise _HTTP(status_code=403,
                    detail={"code": 4032,
                            "msg": f"app {app.app_id!r} no read grant on kb {knowledge_base_name!r}"})

    # 可选的 dl_token（不强制；HMAC 已经鉴过，但如果带了要求 aud 匹配）
    via_dl_token = False
    dl_tok = (request.query_params.get("token") or "").strip()
    if dl_tok:
        from chayuan.server.auth.download_token import (
            is_download_token, verify_download,
        )
        from chayuan.server.auth.tokens import TokenError as _TokErr
        if is_download_token(dl_tok):
            try:
                verify_download(
                    dl_tok,
                    expected_subject=f"app:{app.app_id}",
                    expected_kb=knowledge_base_name,
                    expected_file=file_name,
                )
                via_dl_token = True
            except _TokErr as e:
                kb_audit.denied(user_dict, kb=knowledge_base_name, action="download",
                                code=4011, reason=f"dl_token invalid: {e}", request_id=rid)
                _metrics.record_kb_denied("app", 4011, "download")
                raise _HTTP(status_code=401,
                            detail={"code": 4011, "msg": f"download token invalid: {e}"})

    kb_audit.download_ok(user_dict, kb=knowledge_base_name, file_name=file_name,
                         via_dl_token=via_dl_token, request_id=rid)
    _metrics.record_kb_download("app", via_dl_token, "ok")
    return download_doc(
        knowledge_base_name=knowledge_base_name,
        file_name=file_name,
        preview=preview,
    )


# ---- admin：APP → KB grants 管理（需 admin:write） ----

class _GrantIn(BaseModel):
    kb_name: str = Field(..., max_length=128)
    role: str = Field("reader", pattern="^(reader|editor)$")
    expires_at: Optional[str] = Field(None, description="ISO-8601；None=永久")


class _GrantOut(BaseModel):
    id: int
    app_id: str
    kb_id: int
    kb_name: str
    role: str
    granted_by: Optional[int]
    granted_at: str
    expires_at: Optional[str]


@openapi_router.get(
    "/admin/apps/{app_id}/kb_grants",
    response_model=List[_GrantOut],
    summary="查看某 App 的 KB 授权列表（需 admin:read）",
)
async def admin_list_grants(
    app_id: str,
    _app: AppSpec = Depends(require_scopes("admin:read")),
) -> List[_GrantOut]:
    from chayuan.server.auth.app_acl import list_grants_for_app
    return [_GrantOut(**g) for g in list_grants_for_app(app_id)]


@openapi_router.post(
    "/admin/apps/{app_id}/kb_grants",
    response_model=List[_GrantOut],
    summary="批量授权某 App 访问 KB（需 admin:write）",
    description="幂等：(app_id, kb_name) 已存在则更新 role/expires_at。",
)
async def admin_add_grants(
    request: Request,
    app_id: str,
    grants: List[_GrantIn],
    admin_app: AppSpec = Depends(require_scopes("admin:write")),
) -> List[_GrantOut]:
    from datetime import datetime
    from chayuan.server.auth.app_acl import add_grant
    from chayuan.server.config_panel.apps_store import get_app
    from chayuan.server.auth import kb_audit
    from fastapi import HTTPException as _HTTP

    rid = request.headers.get("x-request-id", "")
    admin_user = _spec_to_user(admin_app)

    if not get_app(app_id):
        raise _HTTP(status_code=404, detail={"code": 4041, "msg": f"app {app_id!r} not found"})
    if not grants:
        raise _HTTP(status_code=400, detail={"code": 4001, "msg": "grants cannot be empty"})

    out: List[_GrantOut] = []
    for g in grants:
        exp = None
        if g.expires_at:
            try:
                exp = datetime.fromisoformat(g.expires_at.replace("Z", "+00:00"))
            except Exception as e:
                raise _HTTP(status_code=400,
                            detail={"code": 4001, "msg": f"bad expires_at: {e}"})
        try:
            row = add_grant(app_id, g.kb_name, role=g.role, expires_at=exp)
        except LookupError as e:
            raise _HTTP(status_code=404, detail={"code": 4041, "msg": str(e)})
        except ValueError as e:
            raise _HTTP(status_code=400, detail={"code": 4001, "msg": str(e)})
        kb_audit.grant_added(admin_user, app_id=app_id, kb_name=g.kb_name,
                             role=g.role, expires_at=g.expires_at, request_id=rid)
        out.append(_GrantOut(**row))
    return out


@openapi_router.delete(
    "/admin/apps/{app_id}/kb_grants/{kb_name}",
    summary="撤销某 App 对某 KB 的授权（需 admin:write）",
)
async def admin_remove_grant(
    request: Request,
    app_id: str,
    kb_name: str,
    admin_app: AppSpec = Depends(require_scopes("admin:write")),
) -> dict:
    from chayuan.server.auth.app_acl import remove_grant
    from chayuan.server.auth import kb_audit
    rid = request.headers.get("x-request-id", "")
    admin_user = _spec_to_user(admin_app)
    ok = remove_grant(app_id, kb_name)
    if ok:
        kb_audit.grant_revoked(admin_user, app_id=app_id, kb_name=kb_name, request_id=rid)
    return {"ok": ok}


# ---- tools 示例 ----

@openapi_router.get(
    "/tools",
    response_model=dict,
    summary="列出当前可调用的工具（需 tools:read）",
    description="返回 ``/tools?enabled=true`` 的摘要；对接方用来在自家 UI 展示工具下拉。",
)
async def list_tools_openapi(
    app: AppSpec = Depends(require_scopes("tools:read")),
) -> dict:
    from chayuan.server.utils import get_tool, get_tool_config
    tools = get_tool()
    data: dict = {}
    for t in tools.values():
        cfg = get_tool_config(t.name) or {}
        if not cfg.get("use", False):
            continue
        if not _tool_allowed_for_app(app, t.name):
            continue
        data[t.name] = {
            "title": getattr(t, "title", t.name),
            "description": t.description,
        }
    return {"data": data}


class ToolCallRequest(BaseModel):
    name: str
    tool_input: dict


@openapi_router.post(
    "/tools/call",
    response_model=dict,
    summary="调用工具（需 tools:call）",
)
async def call_tool_openapi(
    req: ToolCallRequest,
    app: AppSpec = Depends(require_scopes("tools:call")),
) -> dict:
    from chayuan.server.utils import get_tool
    from chayuan.server.observability.metrics import record_tool_call

    t0 = time.perf_counter()
    status = "error"
    try:
        if not _tool_allowed_for_app(app, req.name):
            status = "forbidden"
            return {"code": 403, "msg": f"tool {req.name!r} is not granted to this app"}
        tool = get_tool(req.name)
        if tool is None:
            status = "not_found"
            return {"code": 404, "msg": f"no tool named {req.name!r}"}
        try:
            result = await tool.ainvoke(req.tool_input)
            status = "success"
            return {"data": result}
        except Exception as e:  # noqa: BLE001
            return {"code": 500, "msg": f"{type(e).__name__}: {e}"}
    finally:
        record_tool_call(req.name, status, time.perf_counter() - t0)


# ---- admin 示例 ----

class AppSummary(BaseModel):
    app_id: str
    name: str
    enabled: bool
    scopes: List[str]
    callback_url: str
    callback_events: List[str]


@openapi_router.get(
    "/admin/apps",
    response_model=List[AppSummary],
    summary="列出所有 App（需 admin:read）",
    description="对接方用自己的 admin 账号看全平台对接情况。secret 一律不出现在返回中。",
)
async def list_apps_openapi(
    _app: AppSpec = Depends(require_scopes("admin:read")),
) -> List[AppSummary]:
    from chayuan.server.config_panel.apps_store import list_apps
    return [
        AppSummary(
            app_id=a.app_id, name=a.name, enabled=a.enabled,
            scopes=list(a.scopes or []),
            callback_url=a.callback_url or "",
            callback_events=list(a.callback_events or []),
        )
        for a in list_apps()
    ]
