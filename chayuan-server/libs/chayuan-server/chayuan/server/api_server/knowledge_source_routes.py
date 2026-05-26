"""知识源（Knowledge Source）FastAPI 路由。

路由前缀：/knowledge_source
覆盖：
- 连接测试（不落库）
- 数据源 CRUD
- introspect 刷新 + schema 查看
- 批量授权 / 单项授权 / 撤销
- 多源并行检索（SSE）
- 结果下载（SQL CSV / ES JSON）
- 多源 RAG 对话（SSE，复用 kb_chat 风格）
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.auth.source_access import (
    can_admin,
    can_read,
    can_write,
    filter_accessible,
)
from chayuan.server.db.repository.knowledge_source_repository import (
    connection_spec_for_source,
    create_connection,
    create_source,
    delete_source,
    get_connection,
    get_source,
    grant_source_access,
    grant_source_access_batch,
    list_accessible_source_ids,
    list_source_grants,
    list_sources,
    mark_connection_check,
    replace_schema_cache,
    revoke_source_access,
    row_to_connection_spec,
    update_connection,
    update_source,
)
from chayuan.server.knowledge_source.base import ConnectionSpec, ConnectorError
from chayuan.server.knowledge_source.orchestrator import (
    multi_search_stream,
    multi_search_sync,
)
from chayuan.server.knowledge_source.registry import (
    all_supported_dialects,
    build_connector,
    normalize_dialect,
)
from chayuan.server.observability.audit import AuditStopwatch, audit
from chayuan.server.utils import BaseResponse


def _req_id(request: Optional[Request]) -> str:
    try:
        return str(getattr(request.state, "request_id", "") or "") if request else ""
    except Exception:
        return ""

logger = logging.getLogger("chayuan.api.knowledge_source")

ks_router = APIRouter(prefix="/knowledge_source", tags=["Knowledge Source"])


# ---------------------------------------------------------------------------
# 元数据
# ---------------------------------------------------------------------------

@ks_router.get("/dialects", summary="列出支持的数据源方言")
def list_dialects():
    return {"code": 0, "data": all_supported_dialects()}


# ---------------------------------------------------------------------------
# 连通性测试（不落库）
# ---------------------------------------------------------------------------

@ks_router.post("/test_connection", summary="测试数据源连通性（不落库）")
def test_connection(
    dialect: str = Body(..., examples=["mysql"]),
    host: str = Body(""),
    port: int = Body(0),
    database: str = Body(""),
    username: str = Body(""),
    password: str = Body(""),
    options: Dict[str, Any] = Body({}),
    kind: str = Body(
        "",
        description="可选；消歧同名方言：kind='vs' 时按外部向量库路由 "
        "（例：es + kind='vs' 走 ExternalVsConnector 而非文本 ES 连接器）",
    ),
    connection_id: Optional[int] = Body(
        None,
        description="若提供，未填的字段将从已有连接补全，password 为空则用旧密码",
    ),
    _user=Depends(require_auth_enabled()),
):
    # 提供 connection_id 时做 patch 式填充（避免重新测连接需要用户重填整串）
    if connection_id:
        row = get_connection(int(connection_id))
        if row is not None:
            base_spec = row_to_connection_spec(row)
            if not host:
                host = base_spec.host
            if not port:
                port = base_spec.port
            if not database:
                database = base_spec.database
            if not username:
                username = base_spec.username
            if not password:
                password = base_spec.password
            if not options:
                options = base_spec.options
    try:
        spec = ConnectionSpec(
            dialect=normalize_dialect(dialect),
            host=host, port=port, database=database,
            username=username, password=password,
            options=options or {},
            connect_timeout=5.0,
        )
        conn = build_connector(spec=spec, source_id=0, kind=kind or "")
        ok, msg = conn.test_connection()
    except ConnectorError as e:
        return {"code": 0, "data": {"ok": False, "msg": str(e), "error_code": e.code}}
    except Exception as e:  # noqa: BLE001
        return {"code": 0, "data": {"ok": False, "msg": f"{type(e).__name__}: {e}"}}
    return {"code": 0, "data": {"ok": bool(ok), "msg": msg}}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@ks_router.post("/", summary="新建数据源（SQL / Mongo / ES）")
def create_source_endpoint(
    name: str = Body(..., description="唯一标识"),
    kind: str = Body(..., description="sql / mongo / es / vector"),
    display_name: str = Body(""),
    description: str = Body(""),
    dialect: str = Body(""),
    host: str = Body(""),
    port: int = Body(0),
    database: str = Body(""),
    username: str = Body(""),
    password: str = Body(""),
    options: Dict[str, Any] = Body({}),
    allowed: Dict[str, List[str]] = Body({}, description="白名单：tables/collections/indices"),
    visibility: str = Body("private"),
    request: Request = None,
    user=Depends(require_auth_enabled()),
):
    req_id = _req_id(request)
    watch = AuditStopwatch().__enter__()
    kind = (kind or "").lower()
    # image：本地 store，无远程连接，走单独分支；其它非 vector 走通用「先测联通再落库」。
    # vs：外部向量库（BYO Milvus/pg/ES/Chroma），用 ExternalVsConnector probe 后落库。
    if kind not in ("sql", "mongo", "es", "vector", "image", "vs"):
        raise HTTPException(400, "kind 必须为 sql / mongo / es / vector / image / vs")

    if kind == "image":
        # 仅登记 options（embedder_model 等）；database 通常就是 source_name
        spec_opts = dict(options or {})
        spec_opts.setdefault("source_name", name)
        conn_id = create_connection(
            dialect="image", host="", port=0,
            database=database or name, username="", password="",
            options=spec_opts, allowed={},
            owner_id=(user or {}).get("id") if isinstance(user, dict) else None,
        )
        sid = create_source(
            name=name, kind="image",
            display_name=display_name or name,
            description=description,
            connection_id=conn_id,
            owner_id=(user or {}).get("id") if isinstance(user, dict) else None,
            visibility=visibility,
        )
        watch.__exit__(None, None, None)
        audit(
            "source.create", user=user, request_id=req_id,
            target_type="source", target_id=sid,
            payload={"name": name, "kind": "image",
                     "embedder_model": spec_opts.get("embedder_model", "")},
            result={"connection_id": conn_id, "visibility": visibility},
            elapsed_ms=watch.elapsed_ms,
        )
        return {"code": 0, "data": {"id": sid, "connection_id": conn_id}}

    if kind != "vector":
        # 先建连接，再建源
        if not dialect:
            raise HTTPException(400, "非向量源必须指定 dialect")
        # 创建前先做一次连接探测，失败则直接拒绝（避免垃圾记录）
        spec = ConnectionSpec(
            dialect=normalize_dialect(dialect),
            host=host, port=port, database=database,
            username=username, password=password, options=options or {},
            allowed_tables=list((allowed or {}).get("tables") or []),
            allowed_collections=list((allowed or {}).get("collections") or []),
            allowed_indices=list((allowed or {}).get("indices") or []),
        )
        # kind 透传让 registry 在 es/pg 同名方言时正确分发到 ExternalVsConnector
        conn = build_connector(spec=spec, source_id=0, kind=kind)
        ok, msg = conn.test_connection()
        if not ok:
            raise HTTPException(400, f"连通性测试失败：{msg}")
        conn_id = create_connection(
            dialect=spec.dialect, host=host, port=port, database=database,
            username=username, password=password,
            options=options or {}, allowed=allowed or {},
            owner_id=(user or {}).get("id") if isinstance(user, dict) else None,
        )
        mark_connection_check(conn_id, ok=True, error="")
        sid = create_source(
            name=name, kind=kind, display_name=display_name, description=description,
            connection_id=conn_id,
            owner_id=(user or {}).get("id") if isinstance(user, dict) else None,
            visibility=visibility,
        )
        watch.__exit__(None, None, None)
        audit(
            "source.create", user=user, request_id=req_id,
            target_type="source", target_id=sid,
            payload={"name": name, "kind": kind, "dialect": dialect,
                     "host": host, "port": port, "database": database,
                     "username": username},
            result={"connection_id": conn_id, "visibility": visibility},
            elapsed_ms=watch.elapsed_ms,
        )
        return {"code": 0, "data": {"id": sid, "connection_id": conn_id}}
    else:
        # vector 源复用现有 KB；这里只允许登记已存在的 KB
        from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
        from chayuan.server.db.session import session_scope
        with session_scope() as s:
            kb = s.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.kb_name == name
            ).one_or_none()
            if kb is None:
                raise HTTPException(404, f"向量知识库 {name!r} 不存在，请先创建")
            kb_id = int(kb.id)
        sid = create_source(
            name=name, kind="vector", display_name=display_name or name,
            description=description, vs_kb_id=kb_id,
            owner_id=(user or {}).get("id") if isinstance(user, dict) else None,
            visibility=visibility,
        )
        return {"code": 0, "data": {"id": sid}}


@ks_router.get("/", summary="列出数据源（按用户权限过滤）")
def list_sources_endpoint(
    kind: Optional[str] = Query(None),
    user=Depends(require_auth_enabled()),
):
    rows = list_sources(kind=kind)
    if user is None or (isinstance(user, dict) and user.get("role") == "admin"):
        return {"code": 0, "data": rows}
    uid = user.get("id") if isinstance(user, dict) else None
    accessible = set(list_accessible_source_ids(uid))
    return {
        "code": 0,
        "data": [r for r in rows if int(r["id"]) in accessible
                 or r.get("visibility") == "public"],
    }


@ks_router.get("/{source_id}", summary="数据源详情")
def get_source_endpoint(
    source_id: int,
    user=Depends(require_auth_enabled()),
):
    src = get_source(int(source_id))
    if src is None:
        raise HTTPException(404, "source not found")
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    # 附带 connection 元信息（不返回密码）
    if src.get("connection_id"):
        conn = get_connection(int(src["connection_id"]))
        if conn is not None:
            src["connection"] = {
                "dialect": conn.dialect,
                "host": conn.host, "port": conn.port, "database": conn.database,
                "username": conn.username,
                "options": json.loads(conn.options_json or "{}" or "{}"),
                "allowed": json.loads(conn.allowed_json or "{}" or "{}"),
                "last_check_ok": bool(conn.last_check_ok),
                "last_check_time": conn.last_check_time,
                "last_check_error": conn.last_check_error,
            }
    return {"code": 0, "data": src}


@ks_router.patch("/{source_id}", summary="更新数据源")
def update_source_endpoint(
    source_id: int,
    display_name: Optional[str] = Body(None),
    description: Optional[str] = Body(None),
    visibility: Optional[str] = Body(None),
    connection: Optional[Dict[str, Any]] = Body(None),
    user=Depends(require_auth_enabled()),
):
    if not can_write(user, int(source_id)):
        raise HTTPException(403, "no write permission")
    ok = update_source(
        int(source_id),
        display_name=display_name, description=description, visibility=visibility,
    )
    if not ok:
        raise HTTPException(404, "source not found")
    if connection:
        src = get_source(int(source_id))
        if src and src.get("connection_id"):
            update_connection(
                int(src["connection_id"]),
                host=connection.get("host"),
                port=connection.get("port"),
                database=connection.get("database"),
                username=connection.get("username"),
                password=connection.get("password"),
                options=connection.get("options"),
                allowed=connection.get("allowed"),
            )
            # 任何连接级改动（host/port/allowed/options）都要把三层缓存一起清掉，
            # 防止 Text2SQL 拿旧 schema / 旧模板 / 旧结果。
            if any(k in connection for k in ("allowed", "options", "database", "host", "port")):
                try:
                    from chayuan.server.db.repository.knowledge_source_repository import (
                        delete_schema_cache,
                    )
                    delete_schema_cache(int(source_id))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from chayuan.server.knowledge_source.cache import (
                        invalidate_result_cache_by_source,
                        schema_cache_invalidate,
                        template_cache_invalidate,
                    )
                    schema_cache_invalidate(int(source_id))
                    template_cache_invalidate(int(source_id))
                    invalidate_result_cache_by_source(int(source_id))
                except Exception:  # noqa: BLE001
                    pass
                _catalog_cache_invalidate_for_source(int(source_id))
    return {"code": 0, "msg": "ok"}


@ks_router.delete("/{source_id}", summary="删除数据源")
def delete_source_endpoint(
    source_id: int,
    user=Depends(require_auth_enabled()),
):
    if not can_admin(user, int(source_id)):
        raise HTTPException(403, "owner only")
    # 删 DB 行前先记下:这是不是图像源、它的图像 store 名是什么 —— 删完
    # DB 行(source + connection)就再也解析不出来了。图像知识源的索引
    # store / 原图文件 / 进程内缓存全在 delete_source 之外,必须单独清,
    # 否则同名重建时 get_store 命中旧数据,旧图全部复活。
    _img_store_name = None
    try:
        _src = get_source(int(source_id))
        if _src is not None and (_src.get("kind") or "") == "image":
            from chayuan.server.api_server.image_routes import _resolve_store_name
            _img_store_name = _resolve_store_name(int(source_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("delete_source: 解析图像 store 名失败 source=%s: %r",
                       source_id, e)
    if not delete_source(int(source_id)):
        raise HTTPException(404, "source not found")
    if _img_store_name is not None:
        try:
            from chayuan.server.api_server.image_routes import (
                purge_image_source_storage,
            )
            purge_image_source_storage(int(source_id), _img_store_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("delete_source: 清图像存储失败 source=%s: %r",
                           source_id, e)
    return {"code": 0, "msg": "ok"}


# ---------------------------------------------------------------------------
# Schema 刷新 / 查看
# ---------------------------------------------------------------------------

@ks_router.post("/{source_id}/introspect", summary="刷新数据源 schema 缓存")
def introspect_endpoint(
    source_id: int,
    sample_rows: int = Body(3),
    user=Depends(require_auth_enabled()),
):
    if not can_write(user, int(source_id)):
        raise HTTPException(403, "no write permission")
    src = get_source(int(source_id))
    if src is None:
        raise HTTPException(404, "source not found")
    if src["kind"] == "vector":
        return {"code": 0, "data": {"tables": 0, "msg": "向量源无需 schema"}}
    resolved = connection_spec_for_source(int(source_id))
    if resolved is None:
        raise HTTPException(400, "source has no connection")
    _, spec = resolved
    try:
        conn = build_connector(spec=spec, source_id=int(source_id))
        snapshot = conn.introspect(sample_rows=int(sample_rows or 3))
    except ConnectorError as e:
        mark_connection_check(int(src["connection_id"]), ok=False, error=str(e))
        raise HTTPException(400, f"introspect 失败：{e}") from None
    replace_schema_cache(int(source_id), snapshot)
    mark_connection_check(int(src["connection_id"]), ok=True, error="")
    return {
        "code": 0,
        "data": {
            "tables": len(snapshot.tables),
            "names": [t.name for t in snapshot.tables],
        },
    }


# ---------------------------------------------------------------------------
# 范围选择：catalog（轻量列表 + 5min 进程内缓存）
# ---------------------------------------------------------------------------

# 进程内 TTL 缓存；key = (connection_id, dialect, 简短参数指纹)。刻意不进 Redis：
# 这个接口只给 UI 下拉用，用户主动点"刷新"时可强制 bypass。
_CATALOG_CACHE: Dict[str, Any] = {}
_CATALOG_TTL = 5 * 60  # 5 min


def _catalog_cache_get(key: str):
    import time as _t
    rec = _CATALOG_CACHE.get(key)
    if not rec:
        return None
    if rec.get("expires_at", 0) < _t.time():
        _CATALOG_CACHE.pop(key, None)
        return None
    return rec.get("value")


def _catalog_cache_put(key: str, value: Any) -> None:
    import time as _t
    _CATALOG_CACHE[key] = {"expires_at": _t.time() + _CATALOG_TTL, "value": value}


def _catalog_cache_invalidate_for_source(source_id: int) -> None:
    """改了 allowed / schema 后清掉该源相关的 catalog 缓存。"""
    prefix = f"src:{int(source_id)}:"
    for k in list(_CATALOG_CACHE.keys()):
        if k.startswith(prefix):
            _CATALOG_CACHE.pop(k, None)


def _build_probe_spec_from_body(
    *, dialect: str, host: str, port: int, database: str,
    username: str, password: str, options: Dict[str, Any],
    connection_id: Optional[int],
) -> ConnectionSpec:
    """测试连接 / catalog 探测复用同一份 spec 构造（对齐 test_connection 的 patch 逻辑）。"""
    if connection_id:
        row = get_connection(int(connection_id))
        if row is not None:
            base_spec = row_to_connection_spec(row)
            if not host:
                host = base_spec.host
            if not port:
                port = base_spec.port
            if not database:
                database = base_spec.database
            if not username:
                username = base_spec.username
            if not password:
                password = base_spec.password
            if not options:
                options = base_spec.options
    return ConnectionSpec(
        dialect=normalize_dialect(dialect),
        host=host, port=port, database=database,
        username=username, password=password, options=options or {},
    )


@ks_router.post(
    "/catalog",
    summary="轻量 catalog —— 只返 table / collection / index 名字，用于创建/编辑 UI 的范围多选",
)
def catalog_probe(
    dialect: str = Body(..., examples=["mysql", "milvus"]),
    host: str = Body(""),
    port: int = Body(0),
    database: str = Body(""),
    username: str = Body(""),
    password: str = Body(""),
    options: Dict[str, Any] = Body({}),
    kind: str = Body(""),
    connection_id: Optional[int] = Body(None),
    refresh: bool = Body(False, description="true 则绕过 TTL 缓存强制现采"),
    _user=Depends(require_auth_enabled()),
):
    """比 introspect 更轻：
    - **不**取 sample_rows / column type（省一轮 SELECT × N 张表）；
    - **不**落 schema_cache；
    - 进程内 TTL 5min；``refresh=true`` 可绕过。

    返回 ``{"code": 0, "data": {"kind": "sql|vs|mongo|es", "names": [...]}}``。
    适合建/改数据源时 UI 在"测试连接"后立即拉一把，让用户勾选范围。
    """
    import time as _t
    spec = _build_probe_spec_from_body(
        dialect=dialect, host=host, port=port, database=database,
        username=username, password=password, options=options,
        connection_id=connection_id,
    )
    cache_key = f"probe:{connection_id or 0}:{spec.dialect}:{spec.host}:{spec.port}:{spec.database}"
    if not refresh:
        hit = _catalog_cache_get(cache_key)
        if hit is not None:
            return {"code": 0, "data": hit}

    t0 = _t.time()
    try:
        conn = build_connector(spec=spec, source_id=0, kind=kind)
        # 复用 introspect：所有 Connector 都实现了。对 VS 只需 name；对 SQL/Mongo/ES
        # 这里故意让 sample_rows=0，减少 per-table 采样往返。
        try:
            snap = conn.introspect(sample_rows=0)
        except TypeError:
            # 老签名不支持 sample_rows=0：忽略掉 kw 走默认
            snap = conn.introspect()
    except ConnectorError as e:
        raise HTTPException(400, f"catalog 探测失败：{e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"catalog 探测异常：{type(e).__name__}: {e}")

    names = [t.name for t in (snap.tables or []) if t.name]
    data = {
        "dialect": spec.dialect,
        "kind": snap.source_kind,
        "names": names,
        "count": len(names),
        "elapsed_ms": int((_t.time() - t0) * 1000),
        "cached": False,
    }
    _catalog_cache_put(cache_key, {**data, "cached": True})
    return {"code": 0, "data": data}


@ks_router.get(
    "/{source_id}/catalog",
    summary="已存在数据源的轻量 catalog（和 POST /catalog 同义，但按 source_id 查）",
)
def catalog_for_source(
    source_id: int,
    refresh: bool = False,
    user=Depends(require_auth_enabled()),
):
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    src = get_source(int(source_id))
    if src is None:
        raise HTTPException(404, "source not found")
    if src["kind"] == "vector":
        return {"code": 0, "data": {"kind": "vector", "names": [], "count": 0}}
    resolved = connection_spec_for_source(int(source_id))
    if resolved is None:
        raise HTTPException(400, "source has no connection")
    _, spec = resolved

    key = f"src:{int(source_id)}:{spec.dialect}:{spec.host}:{spec.port}"
    if not refresh:
        hit = _catalog_cache_get(key)
        if hit is not None:
            return {"code": 0, "data": hit}
    try:
        conn = build_connector(spec=spec, source_id=int(source_id), kind=src.get("kind") or "")
        try:
            snap = conn.introspect(sample_rows=0)
        except TypeError:
            snap = conn.introspect()
    except ConnectorError as e:
        raise HTTPException(400, f"catalog 探测失败：{e}")
    names = [t.name for t in (snap.tables or []) if t.name]
    data = {
        "dialect": spec.dialect, "kind": snap.source_kind,
        "names": names, "count": len(names),
        # 返回当前已选（便于前端回填）
        "allowed_tables": list(spec.allowed_tables or []),
        "allowed_collections": list(spec.allowed_collections or []),
        "allowed_indices": list(spec.allowed_indices or []),
    }
    _catalog_cache_put(key, data)
    return {"code": 0, "data": data}


@ks_router.patch(
    "/{source_id}/allowed",
    summary="更新数据源的范围（白名单表 / 集合 / index）+ 级联失效缓存",
)
def patch_allowed(
    source_id: int,
    allowed: Dict[str, List[str]] = Body(..., examples=[{"tables": ["t_user", "t_order"]}]),
    user=Depends(require_auth_enabled()),
):
    """语义：allowed 非空 → 固定范围；空 → 默认全部（恢复无范围行为）。
    级联失效：schema_cache（DB+Redis）+ template_cache + result_cache + 本进程 catalog 缓存。
    """
    if not can_write(user, int(source_id)):
        raise HTTPException(403, "no write permission")
    src = get_source(int(source_id))
    if src is None:
        raise HTTPException(404, "source not found")
    if not src.get("connection_id"):
        raise HTTPException(400, "source has no connection (vector source?)")

    update_connection(int(src["connection_id"]), allowed=dict(allowed or {}))

    # 级联失效：改白名单等于改"可见表集合"，旧 schema 快照 / SQL 模板 / 结果都必须作废
    try:
        from chayuan.server.db.repository.knowledge_source_repository import (
            delete_schema_cache,
        )
        delete_schema_cache(int(source_id))
    except Exception:  # noqa: BLE001
        logger.debug("delete_schema_cache failed", exc_info=True)
    try:
        from chayuan.server.knowledge_source.cache import (
            invalidate_result_cache_by_source,
            schema_cache_invalidate,
            template_cache_invalidate,
        )
        schema_cache_invalidate(int(source_id))
        template_cache_invalidate(int(source_id))
        invalidate_result_cache_by_source(int(source_id))
    except Exception:  # noqa: BLE001
        logger.debug("invalidate caches failed", exc_info=True)
    _catalog_cache_invalidate_for_source(int(source_id))

    return {"code": 0, "data": {"allowed": allowed or {}}}


@ks_router.get("/{source_id}/schema", summary="查看数据源 schema 缓存")
def get_schema_endpoint(
    source_id: int,
    user=Depends(require_auth_enabled()),
):
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    from chayuan.server.db.repository.knowledge_source_repository import (
        load_schema_cache,
    )
    snap = load_schema_cache(int(source_id))
    if snap is None:
        return {"code": 0, "data": {"tables": []}}
    return {
        "code": 0,
        "data": {
            "dialect": snap.dialect,
            "source_kind": snap.source_kind,
            "tables": [
                {
                    "name": t.name,
                    "comment": t.comment,
                    "columns": [c.__dict__ for c in t.columns],
                    "sample_rows": t.sample_rows,
                }
                for t in snap.tables
            ],
        },
    }


# ---------------------------------------------------------------------------
# 授权
# ---------------------------------------------------------------------------

@ks_router.get("/{source_id}/grants", summary="列出授权")
def list_grants_endpoint(
    source_id: int,
    user=Depends(require_auth_enabled()),
):
    if not can_admin(user, int(source_id)):
        raise HTTPException(403, "owner only")
    return {"code": 0, "data": list_source_grants(int(source_id))}


@ks_router.post("/{source_id}/grants", summary="授予/更新单条授权")
def grant_endpoint(
    source_id: int,
    target_user_id: int = Body(...),
    role: str = Body("reader"),
    user=Depends(require_auth_enabled()),
):
    if not can_admin(user, int(source_id)):
        raise HTTPException(403, "owner only")
    grant_source_access(
        int(source_id), int(target_user_id), role=role,
        granted_by=(user or {}).get("id") if isinstance(user, dict) else None,
    )
    return {"code": 0, "msg": "ok"}


@ks_router.delete("/{source_id}/grants/{target_user_id}", summary="撤销授权")
def revoke_endpoint(
    source_id: int,
    target_user_id: int,
    user=Depends(require_auth_enabled()),
):
    if not can_admin(user, int(source_id)):
        raise HTTPException(403, "owner only")
    revoke_source_access(int(source_id), int(target_user_id))
    return {"code": 0, "msg": "ok"}


@ks_router.post("/grants/batch", summary="批量授权（多用户 × 多源）")
def grant_batch_endpoint(
    source_ids: List[int] = Body(...),
    user_ids: List[int] = Body(...),
    role: str = Body("reader"),
    user=Depends(require_auth_enabled()),
):
    # 批量场景：要求调用者对所有涉及的源都是 owner/admin
    for sid in source_ids:
        if not can_admin(user, int(sid)):
            raise HTTPException(403, f"owner only on source #{sid}")
    added = grant_source_access_batch(
        [int(x) for x in source_ids], [int(x) for x in user_ids], role=role,
        granted_by=(user or {}).get("id") if isinstance(user, dict) else None,
    )
    return {"code": 0, "data": {"added": added}}


# ---------------------------------------------------------------------------
# 多源并行检索（SSE）
# ---------------------------------------------------------------------------

def _resolve_sources_for_request(
    user, source_ids: Optional[List[int]], select_all: bool,
) -> List[Dict[str, Any]]:
    """根据入参 + 用户权限定位本次检索使用的源列表。"""
    all_rows = list_sources()
    if select_all:
        # 全选 = 用户可读的全部源
        uid = user.get("id") if isinstance(user, dict) else None
        role = user.get("role") if isinstance(user, dict) else ""
        if role == "admin" or user is None:
            return all_rows
        accessible = set(list_accessible_source_ids(uid))
        return [r for r in all_rows if int(r["id"]) in accessible
                or r.get("visibility") == "public"]
    if not source_ids:
        return []
    allowed = set(filter_accessible(user, [int(x) for x in source_ids]))
    return [r for r in all_rows if int(r["id"]) in allowed]


@ks_router.post("/multi_search", summary="多源并行检索，流式 SSE 返回进度与结果")
async def multi_search_endpoint(
    query: str = Body(...),
    source_ids: List[int] = Body(default_factory=list),
    select_all: bool = Body(False),
    top_k: int = Body(5),
    per_source_timeout: float = Body(30.0),
    llm_model: Optional[str] = Body(None),
    history: Optional[List[Dict[str, str]]] = Body(None),
    user=Depends(require_auth_enabled()),
):
    sources = _resolve_sources_for_request(user, source_ids, bool(select_all))

    async def _gen():
        async for evt in multi_search_stream(
            query=query, sources=sources, top_k=int(top_k),
            per_source_timeout=float(per_source_timeout),
            llm_model=llm_model, history=history,
        ):
            yield evt

    return EventSourceResponse(_gen())


@ks_router.post("/multi_search_sync", summary="多源并行检索（同步聚合，不走 SSE）")
async def multi_search_sync_endpoint(
    query: str = Body(...),
    source_ids: List[int] = Body(default_factory=list),
    select_all: bool = Body(False),
    top_k: int = Body(5),
    per_source_timeout: float = Body(30.0),
    llm_model: Optional[str] = Body(None),
    history: Optional[List[Dict[str, str]]] = Body(None),
    user=Depends(require_auth_enabled()),
):
    sources = _resolve_sources_for_request(user, source_ids, bool(select_all))
    aggregated, metas = await multi_search_sync(
        query=query, sources=sources, top_k=int(top_k),
        per_source_timeout=float(per_source_timeout),
        llm_model=llm_model, history=history,
    )
    return {
        "code": 0,
        "data": {
            "aggregated": [c.to_wire() for c in aggregated],
            "sources": metas,
        },
    }


# ---------------------------------------------------------------------------
# 结果下载（SQL CSV / ES JSON）
# ---------------------------------------------------------------------------

@ks_router.post("/{source_id}/download_result", summary="执行一次检索并下载结果 CSV/JSON")
async def download_result(
    source_id: int,
    query: str = Body(...),
    format: str = Body("csv", description="csv / json"),
    top_k: int = Body(200),
    llm_model: Optional[str] = Body(None),
    user=Depends(require_auth_enabled()),
):
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    src = get_source(int(source_id))
    if src is None:
        raise HTTPException(404, "source not found")
    aggregated, metas = await multi_search_sync(
        query=query, sources=[src], top_k=int(top_k),
        per_source_timeout=60.0, llm_model=llm_model,
    )
    if not aggregated:
        raise HTTPException(404, "no result")
    chunk = aggregated[0]
    meta = chunk.citation.meta or {}
    fmt = (format or "csv").lower()
    if fmt == "json":
        payload = {
            "source_id": source_id,
            "generated_query": chunk.citation.generated_query,
            "content": chunk.content,
            "meta": meta,
        }
        data = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        return Response(
            content=data,
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="source_{source_id}_result.json"'},
        )
    # csv：优先从 meta.columns + meta.rows 取；若 chunk 里只有 markdown，退化为单列
    columns = meta.get("columns") or []
    rows = meta.get("rows") or []
    buf = io.StringIO()
    w = csv.writer(buf)
    if columns and rows:
        w.writerow(columns)
        for r in rows:
            w.writerow(["" if v is None else str(v) for v in r])
    else:
        w.writerow(["content"])
        w.writerow([chunk.content])
    data = buf.getvalue().encode("utf-8-sig")  # BOM 让 Excel 直接识别 UTF-8
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="source_{source_id}_result.csv"'},
    )


# ---------------------------------------------------------------------------
# Text2SQL 训练语料 CRUD（Vanna-style）
# ---------------------------------------------------------------------------

@ks_router.get("/{source_id}/training", summary="列出 Text2SQL 训练样本")
def list_training_endpoint(
    source_id: int,
    kind: Optional[str] = Query(None, description="ddl / doc / pair"),
    approved_only: bool = Query(True),
    limit: int = Query(500),
    user=Depends(require_auth_enabled()),
):
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    from chayuan.server.db.repository.sql_training_repository import list_samples
    return {
        "code": 0,
        "data": list_samples(
            int(source_id), kind=kind, approved_only=bool(approved_only),
            limit=int(limit),
        ),
    }


@ks_router.post("/{source_id}/training", summary="添加 Text2SQL 训练样本")
def add_training_endpoint(
    source_id: int,
    kind: str = Body(..., description="ddl / doc / pair"),
    question: str = Body(""),
    sql: str = Body(""),
    content: str = Body(""),
    approved: int = Body(1),
    user=Depends(require_auth_enabled()),
):
    if not can_write(user, int(source_id)):
        raise HTTPException(403, "no write permission")
    src = get_source(int(source_id))
    if src is None:
        raise HTTPException(404, "source not found")
    if src.get("kind") != "sql":
        raise HTTPException(400, "training 仅支持 SQL 源")
    from chayuan.server.db.repository.sql_training_repository import add_sample
    sid, created = add_sample(
        source_id=int(source_id), kind=kind,
        question=question, sql=sql, content=content,
        dialect="",
        approved=int(approved),
        created_by=(user or {}).get("id") if isinstance(user, dict) else None,
    )
    return {"code": 0, "data": {"id": sid, "created": created}}


@ks_router.delete("/{source_id}/training/{sample_id}", summary="删除训练样本")
def delete_training_endpoint(
    source_id: int,
    sample_id: int,
    user=Depends(require_auth_enabled()),
):
    if not can_write(user, int(source_id)):
        raise HTTPException(403, "no write permission")
    from chayuan.server.db.repository.sql_training_repository import delete_sample
    delete_sample(int(sample_id))
    return {"code": 0, "msg": "ok"}


# ---------------------------------------------------------------------------
# 多源 RAG 对话（SSE）
# ---------------------------------------------------------------------------

@ks_router.post("/multi_chat", summary="多源 RAG 对话，流式 SSE")
async def multi_chat_endpoint(
    query: str = Body(...),
    source_ids: List[int] = Body(default_factory=list),
    select_all: bool = Body(False),
    top_k: int = Body(5),
    per_source_timeout: float = Body(30.0),
    history: List[Dict[str, str]] = Body(default_factory=list),
    stream: bool = Body(True),
    model: str = Body(""),
    temperature: float = Body(0.3),
    max_tokens: Optional[int] = Body(None),
    prompt_name: str = Body("default"),
    request: Request = None,
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.chat.multi_source_chat import multi_source_chat
    return await multi_source_chat(
        query=query, source_ids=source_ids, select_all=select_all,
        top_k=top_k, per_source_timeout=per_source_timeout,
        history=history, stream=stream, model=model,
        temperature=temperature, max_tokens=max_tokens,
        prompt_name=prompt_name, request=request, user=user,
    )
