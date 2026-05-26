"""94-2/94-3:知识中心混合集合 HTTP 路由。

业务背景:用户在知识中心选"混合集合",同时管理 doc-KB + image-KB。
所有接口都基于 ``kb_collection_repository``。

路由前缀:``/knowledge_universe/collections``

接口清单:
  GET    /collections                    列出当前用户的集合(轻量,不展开成员)
  POST   /collections                    新建集合
  GET    /collections/{id}               详情(展开 members)
  PATCH  /collections/{id}               更新 display_name / description
  DELETE /collections/{id}               删集合(MVP 不级联子 KB,后续 94-3+)
  POST   /collections/{id}/members       加成员(ku_id, kind);校验 owner 一致
  DELETE /collections/{id}/members/{ku}  移除成员
  POST   /collections/{id}/search        在集合内并发搜索 doc + image,
                                         单子 KB 超时跳过(94-3)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path

from chayuan.server.auth.deps import require_auth_enabled

logger = logging.getLogger("chayuan.api.kb_collection")

collection_router = APIRouter(
    prefix="/knowledge_universe/collections",
    tags=["Knowledge Collection (混合集合)"],
)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@collection_router.get(
    "",
    summary="94-2:列出当前用户的混合集合(不展开成员)",
)
def list_collections_endpoint(user=Depends(require_auth_enabled())):
    from chayuan.server.db.repository import kb_collection_repository as repo
    owner_id = _user_id(user)
    items = repo.list_collections(owner_id=owner_id)
    return {"code": 0, "data": {"items": items, "total": len(items)}}


@collection_router.post(
    "",
    summary="94-2:新建集合",
)
def create_collection_endpoint(
    payload: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import kb_collection_repository as repo
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name 必填")
    try:
        d = repo.create_collection(
            name,
            owner_id=_user_id(user),
            display_name=str(payload.get("display_name") or "").strip(),
            description=str(payload.get("description") or "").strip(),
            visibility=str(payload.get("visibility") or "private").strip(),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"code": 0, "data": d, "msg": "ok"}


@collection_router.get(
    "/{collection_id}",
    summary="94-2:集合详情(展开成员)",
)
def get_collection_endpoint(
    collection_id: int = Path(..., ge=1),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import kb_collection_repository as repo
    d = repo.get_collection(collection_id)
    if d is None:
        raise HTTPException(404, "集合不存在")
    _check_owner(user, d)
    return {"code": 0, "data": d}


@collection_router.patch(
    "/{collection_id}",
    summary="94-2:更新集合 display_name / description",
)
def update_collection_endpoint(
    collection_id: int,
    payload: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import kb_collection_repository as repo
    cur = repo.get_collection(collection_id)
    if cur is None:
        raise HTTPException(404, "集合不存在")
    _check_owner(user, cur)
    d = repo.update_collection(
        collection_id,
        display_name=payload.get("display_name"),
        description=payload.get("description"),
    )
    return {"code": 0, "data": d, "msg": "ok"}


@collection_router.delete(
    "/{collection_id}",
    summary="94-2:删集合 + 级联删子 KB(用户决策)",
)
def delete_collection_endpoint(
    collection_id: int,
    user=Depends(require_auth_enabled()),
):
    """级联删:先删子 KB(doc / image),再删集合本身。

    单个子 KB 删除失败不阻断集合删除(后续可手动清理孤儿 KB)。
    """
    from chayuan.server.db.repository import kb_collection_repository as repo

    cur = repo.get_collection(collection_id)
    if cur is None:
        raise HTTPException(404, "集合不存在")
    _check_owner(user, cur)

    # 级联删子 KB(失败收集到 errors,继续往下)
    errors: List[Dict[str, Any]] = []
    for m in cur.get("members", []):
        try:
            _delete_member_kb(m["ku_id"], m["kind"], user)
        except Exception as e:  # noqa: BLE001
            logger.warning("删子 KB %s (%s) 失败: %r", m["ku_id"], m["kind"], e)
            errors.append({
                "ku_id": m["ku_id"], "kind": m["kind"],
                "error": f"{type(e).__name__}: {e}",
            })

    repo.delete_collection(collection_id)
    return {"code": 0, "data": {"deleted_id": collection_id, "errors": errors}}


@collection_router.post(
    "/{collection_id}/members",
    summary="94-2:加成员到集合(校验 owner 一致)",
)
def add_member_endpoint(
    collection_id: int,
    payload: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import kb_collection_repository as repo

    cur = repo.get_collection(collection_id)
    if cur is None:
        raise HTTPException(404, "集合不存在")
    _check_owner(user, cur)

    ku_id = str(payload.get("ku_id") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    if not ku_id:
        raise HTTPException(400, "ku_id 必填")
    if kind not in ("document", "image"):
        raise HTTPException(400, "kind 必须是 document / image")

    # 决策 2:同 owner 跟随顶级走 — 校验子 KB owner == 集合 owner
    sub_owner = _resolve_ku_owner(ku_id, kind)
    if sub_owner is not None and sub_owner != cur["owner_id"]:
        raise HTTPException(
            400,
            f"子 KB owner={sub_owner} 与集合 owner={cur['owner_id']} 不一致;"
            "只能加同 owner 的 KB 进集合",
        )

    try:
        d = repo.add_member(
            collection_id, ku_id, kind,
            sort_order=int(payload.get("sort_order") or 0),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"code": 0, "data": d, "msg": "ok"}


@collection_router.delete(
    "/{collection_id}/members/{ku_id}",
    summary="94-2:从集合移除成员(不删子 KB)",
)
def remove_member_endpoint(
    collection_id: int, ku_id: str,
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository import kb_collection_repository as repo
    cur = repo.get_collection(collection_id)
    if cur is None:
        raise HTTPException(404, "集合不存在")
    _check_owner(user, cur)
    removed = repo.remove_member(collection_id, ku_id)
    return {"code": 0, "data": {"removed": removed}}


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _user_id(user: Any) -> int:
    """从认证返的 dict 拿 user.id;guest 也认。"""
    if isinstance(user, dict):
        return int(user.get("id") or 0)
    return int(getattr(user, "id", 0) or 0)


def _check_owner(user: Any, collection: Dict[str, Any]) -> None:
    """admin 或 owner 才能改;guest 视情况(AUTH_REQUIRED=false 时 guest 是 -1)。"""
    if not isinstance(user, dict):
        return
    if user.get("is_guest"):
        return  # 单机 / 内网模式直接放行
    if user.get("role") == "admin":
        return
    if int(user.get("id") or 0) != int(collection.get("owner_id") or -1):
        raise HTTPException(403, "你不是这个集合的 owner")


def _resolve_ku_owner(ku_id: str, kind: str) -> Optional[int]:
    """反查 ku_id 的 owner_id。失败返 None(让上层不做强校验)。"""
    try:
        if kind == "document":
            from chayuan.server.db.repository.knowledge_base_repository import (
                get_kb_detail,
            )
            kb = get_kb_detail(ku_id)
            if isinstance(kb, dict):
                return kb.get("owner_id")
        elif kind == "image":
            from chayuan.server.db.repository.knowledge_source_repository import (
                get_source,
            )
            src = get_source_by_ku_id(ku_id)
            if src is not None:
                return src.get("owner_id")
    except Exception as e:  # noqa: BLE001
        logger.debug("[_resolve_ku_owner] %s/%s failed: %r", kind, ku_id, e)
    return None


def get_source_by_ku_id(ku_id: str):
    """image source 反查;ku_id 通常是 ``"src:<id>"`` 格式。"""
    try:
        from chayuan.server.db.repository.knowledge_source_repository import (
            get_source,
        )
        if isinstance(ku_id, str) and ku_id.startswith("src:"):
            return get_source(int(ku_id[4:]))
    except Exception:  # noqa: BLE001
        return None
    return None


@collection_router.post(
    "/{collection_id}/search",
    summary="94-3:在集合内并发搜索 doc + image 子 KB(单子超时跳过)",
)
def search_collection_endpoint(
    collection_id: int,
    payload: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
):
    """合并搜索:并发查每个子 KB,按 score 全局重排序;单子超时(默认 5s)跳过。

    请求体::

        {
          "query": "<必填>文本查询",
          "top_k": 10,                  # 每个子 KB 召回数,默认 10
          "score_threshold": 0.0,       # 默认 0
          "per_member_timeout_s": 5.0,  # 单子超时,默认 5s
          "max_results": 30             # 合并后最多返多少条,默认 30
        }
    """
    from chayuan.server.db.repository import kb_collection_repository as repo

    cur = repo.get_collection(collection_id)
    if cur is None:
        raise HTTPException(404, "集合不存在")
    _check_owner(user, cur)

    query = str(payload.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query 必填")

    top_k = int(payload.get("top_k") or 10)
    timeout_s = float(payload.get("per_member_timeout_s") or 5.0)
    max_results = int(payload.get("max_results") or 30)

    members = cur.get("members") or []
    if not members:
        return {"code": 0, "data": {"items": [], "total": 0, "diagnostics": []}}

    import concurrent.futures as _cf

    def _query_member(member: Dict[str, Any]) -> Dict[str, Any]:
        """单子 KB 搜索,catch 一切异常返 ServiceStatus-like dict。"""
        ku_id = member["ku_id"]
        kind = member["kind"]
        try:
            hits = _search_single_member(ku_id, kind, query, top_k, user)
            return {
                "ku_id": ku_id, "kind": kind, "ok": True,
                "hits": hits, "error": "",
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("search %s (%s) failed: %r", ku_id, kind, e)
            return {
                "ku_id": ku_id, "kind": kind, "ok": False,
                "hits": [], "error": f"{type(e).__name__}: {e}",
            }

    workers = min(8, max(2, len(members)))
    ex = _cf.ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="kbcoll-",
    )
    diagnostics: List[Dict[str, Any]] = []
    all_hits: List[Dict[str, Any]] = []
    try:
        future_map = [(ex.submit(_query_member, m), m) for m in members]
        import time as _time
        deadline = _time.time() + timeout_s
        for fut, m in future_map:
            ku_id = m["ku_id"]; kind = m["kind"]
            remaining = max(0.05, deadline - _time.time())
            try:
                ret = fut.result(timeout=remaining)
                diagnostics.append({
                    "ku_id": ku_id, "kind": kind,
                    "ok": ret["ok"], "hit_count": len(ret["hits"]),
                    "error": ret["error"],
                })
                if ret["ok"]:
                    for h in ret["hits"]:
                        h.setdefault("source_kind", kind)
                        h.setdefault("ku_id", ku_id)
                        all_hits.append(h)
            except _cf.TimeoutError:
                diagnostics.append({
                    "ku_id": ku_id, "kind": kind,
                    "ok": False, "hit_count": 0,
                    "error": f"超时(>{timeout_s:.1f}s)",
                })
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)

    # 按 score 倒序合并
    all_hits.sort(
        key=lambda h: float(h.get("score") or 0.0), reverse=True,
    )
    return {
        "code": 0,
        "data": {
            "items": all_hits[:max_results],
            "total": len(all_hits),
            "diagnostics": diagnostics,
        },
    }


def _search_single_member(
    ku_id: str, kind: str, query: str, top_k: int, user: Any,
) -> List[Dict[str, Any]]:
    """对单个子 KB 调原有搜索接口(透明委托),返 list of hit dict。"""
    if kind == "document":
        # 走 kb_service 文档检索;返回 List[DocumentWithVSId]
        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
        kb = KBServiceFactory.get_service_by_name(ku_id)
        if kb is None:
            return []
        docs = kb.search_docs(query, top_k=top_k, score_threshold=0.0)
        out: List[Dict[str, Any]] = []
        for d in docs:
            out.append({
                "id": getattr(d, "id", ""),
                "content": getattr(d, "page_content", "") or "",
                "score": float(getattr(d, "score", 0.0) or 0.0),
                "metadata": dict(getattr(d, "metadata", {}) or {}),
                "kind": "document",
            })
        return out
    elif kind == "image":
        # 走 image_source.connector;ku_id 形如 "src:<id>"
        if not (isinstance(ku_id, str) and ku_id.startswith("src:")):
            return []
        from chayuan.server.db.repository.knowledge_source_repository import (
            connection_spec_for_source,
        )
        from chayuan.server.image_source.connector import ImageConnector
        from chayuan.server.knowledge_universe.types import NLQuery

        src_id = int(ku_id[4:])
        resolved = connection_spec_for_source(src_id)
        if resolved is None:
            return []
        _, spec = resolved
        conn = ImageConnector(spec=spec, source_id=src_id)
        chunks = _run_search_sync(conn, query)
        out = []
        for c in chunks[:top_k]:
            out.append({
                "id": getattr(c, "chunk_id", ""),
                "content": getattr(c, "text", "") or "",
                "score": float(getattr(c, "score", 0.0) or 0.0),
                "metadata": dict(getattr(c, "metadata", {}) or {}),
                "kind": "image",
            })
        return out
    return []


def _run_search_sync(conn, query: str) -> List[Any]:
    """ImageConnector.search 是 async,这里同步包一层。"""
    import asyncio
    from chayuan.server.knowledge_universe.types import NLQuery

    nl = NLQuery(text=query, intent="search")

    async def _wrap():
        return await conn.search(nl)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在 running loop 内 — 不该发生(我们在 ThreadPoolExecutor worker 线程里),
            # 但万一发生用 to_thread 兜底
            return []
    except RuntimeError:
        pass
    return asyncio.run(_wrap())


def _delete_member_kb(ku_id: str, kind: str, user: Any) -> None:
    """级联删子 KB。失败抛异常给上层 errors 记录。"""
    if kind == "document":
        # 完整删除:向量数据 + 磁盘文件 + FileStorage + file metadata + KB DB 行。
        # 之前只删 DB 行 → 磁盘文件 / 向量按 kb_name 残留,删掉再建同名 KB 会把
        # 旧文件 / 旧向量"复活"。改走 KBService.clear_vs + drop_kb 整套清干净。
        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
        kb = KBServiceFactory.get_service_by_name(ku_id)
        if kb is not None:
            kb.clear_vs()   # do_clear_vs(清向量内容)+ delete_files_from_db(file metadata 行)
            kb.drop_kb()    # do_drop_kb(删 collection)+ rmtree(kb_path)+ FileStorage + delete_kb_from_db
        else:
            # KBService 取不到(配置残破等)→ 至少兜底删 KB DB 行
            from chayuan.server.db.repository.knowledge_base_repository import (
                delete_kb_from_db,
            )
            delete_kb_from_db(ku_id)
    elif kind == "image":
        if isinstance(ku_id, str) and ku_id.startswith("src:"):
            from chayuan.server.db.repository.knowledge_source_repository import (
                delete_source,
            )
            delete_source(int(ku_id[4:]))
        else:
            raise ValueError(f"image ku_id 必须以 'src:' 开头, got {ku_id!r}")
    else:
        raise ValueError(f"unknown kind {kind!r}")
