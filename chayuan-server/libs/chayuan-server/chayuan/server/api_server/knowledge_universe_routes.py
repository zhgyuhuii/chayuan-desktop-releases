"""统一知识库宇宙 API（前端 KB UI 单一入口）。

历史沿革:
- /knowledge_base/* —— 经典本地文档 KB(FAISS/Milvus/Chroma/ES/PG/...),向量化文档
- /knowledge_source/* —— 外部数据源(SQL/Mongo/ES/外部向量/图像)

前端历来只接 knowledge_base,看不到 source 那一半。本路由把两者**统一暴露**,
不动任何旧路由(向后兼容),只多加一层"宇宙"视图:

  kind ∈ {document, image, structured, vector}
   ├── document    → /knowledge_base/* 全集
   ├── image       → /knowledge_source/* where kind='image'
   ├── structured  → /knowledge_source/* where kind in {sql,mongo,es}
   └── vector      → /knowledge_source/* where kind in {vector,vs}

ID 编码:
  doc:<kb_name>            文档 KB(name 是主键)
  src:<source_id>          外部 source(id 是 int)
前端只看到 ``ku_id`` 字符串,不需要关心两套主键。

性能:
- list 端点单次返回全集(本来就在 DB 内存里查),不分页;前端按 kind 本地过滤。
- detail 端点路由到既有路由的内部函数,不走 HTTP self-call,延迟 < 50ms 内。
- ask 端点提供两个变体:
    POST /ask          —— 同步聚合,串行处理每个 ku_id,返回完整 results(向后兼容)
    POST /ask/stream   —— SSE 流式,asyncio.to_thread 并行跑每个 ku_id,完成一个推一个
                          (events: meta / block / done / error)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from chayuan.server.auth.deps import require_auth_enabled

logger = logging.getLogger("chayuan.api.knowledge_universe")

ku_router = APIRouter(prefix="/knowledge_universe", tags=["Knowledge Universe"])


# ---------------------------------------------------------------------------
# 统一类型映射
# ---------------------------------------------------------------------------

# knowledge_source.kind → 前端宇宙 kind
_SOURCE_KIND_MAP: Dict[str, str] = {
    "image": "image",
    "sql": "structured",
    "mongo": "structured",
    "es": "structured",
    "vector": "vector",
    "vs": "vector",
}

# 反向:支持过滤
_REVERSE_KIND_MAP: Dict[str, List[str]] = {
    "image": ["image"],
    "structured": ["sql", "mongo", "es"],
    "vector": ["vector", "vs"],
}


def _encode_id(kind: str, raw_id: Any) -> str:
    """统一 KB id 表示:doc:<name> / src:<id>"""
    return f"doc:{raw_id}" if kind == "document" else f"src:{raw_id}"


def _decode_id(ku_id: str) -> Tuple[str, str]:
    """ku_id → (storage, raw)
    storage='doc' 表示 knowledge_base.kb_name=raw
    storage='src' 表示 knowledge_source.id=int(raw)"""
    if ":" not in ku_id:
        raise HTTPException(400, f"非法 ku_id: {ku_id!r};期望 doc:<name> / src:<id>")
    head, _, raw = ku_id.partition(":")
    if head not in ("doc", "src"):
        raise HTTPException(400, f"未知 ku_id 类型: {head!r}")
    return head, raw


def _is_guest_mode(user: Any) -> bool:
    """单机 / AUTH_REQUIRED=false 且未登录:user 为 None。

    此时整个进程只有一个隐含的"本地用户",对所有 KB / source 都应视为
    mine + owner。否则前端会把上传 / 删除 / 重建向量 等 owner-only UI 全藏掉,
    用户看不到入口(实际后端 _require_write 在 user=None 时也会放行,纯展示问题)。
    """
    return user is None


def _doc_kb_to_universe(row: Dict[str, Any], current_uid: Optional[int], user: Any = None) -> Dict[str, Any]:
    """文档 KB row → 统一 item"""
    guest_mode = _is_guest_mode(user)
    access_role = None
    try:
        from chayuan.server.auth.access import kb_access_for
        access_role = kb_access_for(user, row.get("kb_name") or "") if user is not None else None
    except Exception:  # noqa: BLE001
        access_role = None
    if guest_mode:
        access_role = "owner"
    return {
        "ku_id": _encode_id("document", row.get("kb_name")),
        "kind": "document",
        "name": row.get("kb_name"),
        "display_name": row.get("kb_name"),
        "description": row.get("kb_info") or "",
        "count": int(row.get("file_count") or 0),
        "owner_id": row.get("owner_id"),
        "mine": guest_mode or (current_uid is not None and row.get("owner_id") == current_uid),
        "access_role": access_role or "",
        "can_write": guest_mode or access_role in ("owner", "editor"),
        "visibility": row.get("visibility") or "private",
        "vs_type": row.get("vs_type"),
        "embed_model": row.get("embed_model"),
        "create_time": row.get("create_time"),
        # KB 卡片封面用 — 前 4 个文件名(供前端显示文件 chip 九宫格)
        "cover_files": _doc_kb_cover_files(row.get("kb_name") or ""),
    }


def _doc_kb_cover_files(kb_name: str, *, limit: int = 4) -> List[Dict[str, Any]]:
    """document KB:取前 limit 个文件名 + 扩展名给前端卡片封面用。

    失败一律返空 list — 列表页性能敏感,不让单 KB 文件扫描异常拖垮整页加载。
    """
    if not kb_name:
        return []
    try:
        from chayuan.server.knowledge_base.utils import list_files_from_folder
        files = list_files_from_folder(kb_name) or []
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, Any]] = []
    for fname in files[:limit]:
        if not fname:
            continue
        # fname 可能是 "doc.pdf" 或带子目录 "subdir/doc.pdf";取末段
        base = fname.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
        out.append({"name": base, "ext": ext})
    return out


def _resolve_store_name_for_image(raw: int) -> str:
    """与 image_routes._resolve_store_name 同源。"""
    from chayuan.server.api_server.image_routes import _resolve_store_name
    return _resolve_store_name(int(raw))


def _image_items_for_universe(raw: int, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    from chayuan.server.image_source.store import get_store
    store_name = _resolve_store_name_for_image(int(raw))
    store = get_store(store_name)
    items: List[Dict[str, Any]] = []
    for m in store.list_items(200):
        img_id = m.get("id")
        items.append({
            "id": img_id,
            "url": f"/knowledge_source/{int(raw)}/image/{img_id}",
            "thumb_url": f"/knowledge_source/{int(raw)}/image/{img_id}",
            "caption": m.get("tags") or "",
            "state": m.get("state", "ready"),
            "progress": int(m.get("progress") or 0),
            "error": m.get("error"),
            "has_text_vector": bool(m.get("has_text_vector")),
            "ocr_text": (m.get("ocr_text") or "")[:200] or None,
            "meta": {
                "size_bytes": m.get("size_bytes"),
                "md5": m.get("md5"),
                "embedder_model": m.get("embedder_model"),
                "created_at": m.get("created_at"),
            },
        })
    return items


def _source_to_universe(row: Dict[str, Any], current_uid: Optional[int], user: Any = None) -> Dict[str, Any]:
    """knowledge_source row → 统一 item"""
    raw_kind = (row.get("kind") or "").lower()
    universe_kind = _SOURCE_KIND_MAP.get(raw_kind, raw_kind)
    guest_mode = _is_guest_mode(user)
    access_role = None
    try:
        from chayuan.server.auth.source_access import source_access_for
        access_role = source_access_for(user, int(row.get("id"))) if user is not None else None
    except Exception:  # noqa: BLE001
        access_role = None
    if guest_mode:
        access_role = "owner"

    src_id = row.get("id")
    out: Dict[str, Any] = {
        "ku_id": _encode_id("source", src_id),
        "kind": universe_kind,
        "sub_kind": raw_kind,  # 'sql'/'mongo'/'es'/'image'/'vector'/'vs'
        "name": row.get("name"),
        "display_name": row.get("display_name") or row.get("name"),
        "description": row.get("description") or "",
        "count": int(row.get("counts", {}).get("items", 0)) if row.get("counts") else None,
        "owner_id": row.get("owner_id"),
        "mine": guest_mode or (current_uid is not None and row.get("owner_id") == current_uid),
        "access_role": access_role or "",
        "can_write": guest_mode or access_role in ("owner", "editor"),
        "visibility": row.get("visibility") or "private",
        "create_time": row.get("create_time"),
    }

    # structured KB:从 connection 查 dialect(mysql / postgresql / mongodb / ...),
    # 前端 KbCard 用它选品牌 logo 渲染封面。失败返空,KbCard 走 sub_kind 启发式。
    if universe_kind == "structured" and src_id is not None:
        try:
            from chayuan.server.db.repository.knowledge_source_repository import (
                connection_spec_for_source,
            )
            resolved = connection_spec_for_source(int(src_id))
            if resolved is not None:
                dialect_str, _spec = resolved
                if dialect_str:
                    out["dialect"] = str(dialect_str)
        except Exception:  # noqa: BLE001
            pass

    # image KB:取前 4 张图给前端做封面九宫格;复用现有 image preview URL。
    # 失败返空,KbCard 走渐变 + 大 icon 兜底。
    if universe_kind == "image" and src_id is not None:
        try:
            out["cover_thumbnails"] = _image_kb_cover_thumbnails(int(src_id), limit=4)
        except Exception:  # noqa: BLE001
            pass

    return out


def _image_kb_cover_thumbnails(source_id: int, *, limit: int = 4) -> List[str]:
    """image KB:取前 limit 张图的 preview URL 给卡片封面用。"""
    from chayuan.server.image_source.store import get_store
    try:
        store_name = _resolve_store_name_for_image(source_id)
    except Exception:  # noqa: BLE001
        return []
    store = get_store(store_name)
    items = store.list_items(limit)
    return [f"/knowledge_source/{source_id}/image/{m.get('id')}" for m in items if m.get("id")]


def can_read_ku(user: Any, ku_id: str) -> bool:
    """统一 ku_id 读权限检查，供 universe 与旧 KB 兼容端点共用。"""
    if user is None:
        return True
    storage, raw = _decode_id(ku_id)
    if storage == "doc":
        from chayuan.server.auth.access import can_read_kb
        return can_read_kb(user, raw)
    if storage == "src":
        from chayuan.server.auth.source_access import can_read
        return can_read(user, int(raw))
    return False


def list_universe_items(
    *,
    kind: Optional[str] = None,
    include_vector: bool = True,
    user: Any = None,
) -> List[Dict[str, Any]]:
    """返回与前端智库列表一致的用户可见全集。"""
    current_uid = user.get("id") if isinstance(user, dict) else None
    is_admin = isinstance(user, dict) and user.get("role") == "admin"
    kind = (kind or "").lower() or None

    # 用户显式问 vector 时也允许；兼容旧调用可传 include_vector=False 隐藏。
    if kind == "vector" and not include_vector:
        return []

    items: List[Dict[str, Any]] = []

    if kind in (None, "document"):
        try:
            from chayuan.server.knowledge_base.kb_api import list_kbs
            doc_resp = list_kbs()
            doc_rows = doc_resp.data if hasattr(doc_resp, "data") else (doc_resp or [])
            accessible = None
            if not is_admin and current_uid is not None:
                try:
                    from chayuan.server.auth.access import list_accessible_kbs
                    accessible = set(list_accessible_kbs(user))
                except Exception:
                    accessible = set()
            for r in doc_rows:
                d = r if isinstance(r, dict) else (r.model_dump() if hasattr(r, "model_dump") else dict(r))
                name = d.get("kb_name")
                if accessible is not None and name not in accessible:
                    continue
                items.append(_doc_kb_to_universe(d, current_uid, user))
        except Exception as e:  # noqa: BLE001
            logger.exception("universe list: 文档 KB 拉取失败:%s", e)

    if kind in (None, "image", "structured", "vector"):
        try:
            from chayuan.server.db.repository.knowledge_source_repository import (
                list_accessible_source_ids, list_sources,
            )
            wanted_raw_kinds = _REVERSE_KIND_MAP.get(kind, []) if kind else None
            hidden_raw_kinds: set = set() if include_vector else {"vector", "vs"}
            accessible_src = None
            if not is_admin and current_uid is not None:
                try:
                    accessible_src = set(list_accessible_source_ids(current_uid))
                except Exception:
                    accessible_src = set()
            for r in list_sources():
                raw_kind = (r.get("kind") or "").lower()
                if wanted_raw_kinds is not None and raw_kind not in wanted_raw_kinds:
                    continue
                if raw_kind in hidden_raw_kinds:
                    continue
                if accessible_src is not None:
                    owner = r.get("owner_id")
                    if owner is not None and owner != current_uid and r.get("visibility") != "public":
                        if int(r.get("id")) not in accessible_src:
                            continue
                items.append(_source_to_universe(r, current_uid, user))
        except Exception as e:  # noqa: BLE001
            logger.exception("universe list: knowledge_source 拉取失败:%s", e)

    return items


# ---------------------------------------------------------------------------
# GET /knowledge_universe/list
# ---------------------------------------------------------------------------

@ku_router.get("/list", summary="列出全部知识库(文档 / 图像 / 结构化 / 向量)")
def universe_list(
    kind: Optional[str] = Query(None, description="document / image / structured / vector"),
    include_vector: bool = Query(
        True,
        description="是否返回 vector 类知识库；默认返回，保证与前端智库列表一致。",
    ),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    """统一列表;按用户可见性过滤(admin 全量,普通用户只看 owner / public / 被授权)。
    """
    items = list_universe_items(kind=kind, include_vector=include_vector, user=user)
    return {"code": 0, "msg": "ok", "data": items}


# ---------------------------------------------------------------------------
# GET /knowledge_universe/tree
# ---------------------------------------------------------------------------

# 与前端 chayuan/src/services/kb/kbCatalog.js 的 _buildTreeFromUniverse 对齐:
# 每个 group 节点形如 { id, name, kb_names: [ku_id...], children: [...] }。
# 前端用 ku_id 作为分组下挂载 KB 的 key,因此这里的 kb_names 必须填 ku_id。
_KIND_LABELS: Dict[str, str] = {
    "document": "文档知识库",
    "structured": "结构化数据",
    "image": "图像知识库",
    "vector": "向量知识库",
}


@ku_router.get("/tree", summary="按 kind 分组返回知识库树")
def universe_tree(
    include_vector: bool = Query(True),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    """按 kind 给前端一个分组视图;无分组数据时也返回空 children,前端会回退到 flat。"""
    items = list_universe_items(kind=None, include_vector=include_vector, user=user)
    grouped: Dict[str, List[str]] = {}
    for item in items:
        kind = str(item.get("kind") or "").lower() or "other"
        ku_id = str(item.get("ku_id") or "")
        if not ku_id:
            continue
        grouped.setdefault(kind, []).append(ku_id)
    order = ["document", "structured", "image", "vector"]
    nodes: List[Dict[str, Any]] = []
    seen_kinds: set = set()
    for kind in order:
        if kind in grouped:
            nodes.append({
                "id": f"kind:{kind}",
                "name": _KIND_LABELS.get(kind, kind),
                "kb_names": grouped[kind],
                "children": [],
            })
            seen_kinds.add(kind)
    for kind, ku_ids in grouped.items():
        if kind in seen_kinds:
            continue
        nodes.append({
            "id": f"kind:{kind}",
            "name": _KIND_LABELS.get(kind, kind or "其他"),
            "kb_names": ku_ids,
            "children": [],
        })
    return {"code": 0, "msg": "ok", "data": nodes}


# ---------------------------------------------------------------------------
# GET /knowledge_universe/{ku_id}/detail
# ---------------------------------------------------------------------------

@ku_router.get("/{ku_id:path}/detail", summary="按 ku_id 拉详情(文件 / 图像 / 表 / 集合)")
def universe_detail(
    ku_id: str,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    storage, raw = _decode_id(ku_id)

    if storage == "doc":
        # 文档 KB:复用 list_files + 元信息
        #
        # 注意:不要再用 load_kb_from_db,它返回的是 (kb_name, vs_type, embed_model)
        # 三元组,KB 不存在时返回 (None, None, None) — 是 tuple 不是 None,
        # 之前的 `if kb_meta is None` 永远不成立,后续 `kb_meta.model_dump()`
        # 直接 AttributeError 500。
        # 改走 list_kbs_from_db():返 KnowledgeBaseSchema 列表,字段齐全
        # (kb_name / kb_info / file_count / owner_id / visibility / vs_type / embed_model / create_time),
        # 正好对得上 _doc_kb_to_universe 的取字段集合。
        try:
            from chayuan.server.db.repository.knowledge_base_repository import list_kbs_from_db
            all_kbs = list_kbs_from_db(min_file_count=-1)
            kb_row = next(
                (k for k in all_kbs if (getattr(k, "kb_name", None) or "").lower() == raw.lower()),
                None,
            )
            if kb_row is None:
                raise HTTPException(404, f"document kb '{raw}' not found")
            kb_dict = kb_row.model_dump() if hasattr(kb_row, "model_dump") else dict(kb_row)

            # list_files 内部会按 vs_type 实例化 KBService(faiss/milvus/pg/...)
            # 任何一个底层向量库不可达都会抛(Milvus down → ConnectionNotExistException 等)。
            # 不能让这些拖死整个详情页 — 详情页 UI 只是要列文件名,以及 chunks/in_db 这些
            # 元数据,即便底层不通,KB 元信息(kb_name/file_count/visibility) 仍可显示。
            # 失败时把 files 置空,加 degraded 字段 + 错误说明,UI 渲染为"文件列表暂不可用"。
            files: list = []
            degraded: Optional[str] = None
            try:
                from chayuan.server.knowledge_base.kb_doc_api import list_files
                files_resp = list_files(knowledge_base_name=raw)
                files = files_resp.data if hasattr(files_resp, "data") else (files_resp or [])
            except Exception as e:  # noqa: BLE001
                logger.warning("list_files for '%s' failed (vs backend down?): %r", raw, e)
                degraded = f"{type(e).__name__}: {e}"

            data: Dict[str, Any] = {
                "kind": "document",
                "ku_id": ku_id,
                "meta": _doc_kb_to_universe(
                    kb_dict,
                    user.get("id") if isinstance(user, dict) else None,
                    user,
                ),
                "files": files,
            }
            if degraded:
                data["degraded"] = degraded
            return {"code": 0, "msg": "ok", "data": data}
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("universe doc detail failed: %s", e)
            raise HTTPException(500, f"document detail failed: {e}")

    # storage == 'src'
    try:
        from chayuan.server.db.repository.knowledge_source_repository import (
            get_source, get_connection,
        )
        src = get_source(int(raw))
        if src is None:
            raise HTTPException(404, f"source #{raw} not found")
        sub_kind = (src.get("kind") or "").lower()
        universe_kind = _SOURCE_KIND_MAP.get(sub_kind, sub_kind)
        meta = _source_to_universe(
            src,
            user.get("id") if isinstance(user, dict) else None,
            user,
        )

        payload: Dict[str, Any] = {"kind": universe_kind, "sub_kind": sub_kind,
                                   "ku_id": ku_id, "meta": meta}

        # 按 sub_kind 拉具体内容
        if sub_kind == "image":
            try:
                items = _image_items_for_universe(int(raw), payload)
                payload["items"] = items
            except Exception as e:  # noqa: BLE001
                logger.warning("image source list failed: %r", e)
                payload["items"] = []

        elif sub_kind in ("sql", "mongo", "es"):
            # 列表 / 表 / 集合 / 索引;走真实 connector.introspect()
            #
            # 之前两处 bug:
            #   1) `from ...knowledge_source.router import build_connector` —
            #      build_connector 在 registry.py,不在 router.py;导致 ImportError 被
            #      try/except 吞掉,tables 永远空
            #   2) connector.list_tables() 这个方法根本不存在;BaseConnector 只有
            #      `introspect(sample_rows) → SchemaSnapshot`
            try:
                from chayuan.server.knowledge_source.registry import build_connector
                from chayuan.server.db.repository.knowledge_source_repository import (
                    row_to_connection_spec,
                )
                conn_row = get_connection(int(src["connection_id"])) if src.get("connection_id") else None
                if conn_row is None:
                    raise RuntimeError("source 缺 connection_id 或已被删除")
                # ConnectionSpec(**conn_row) 之前会拿到 SQLAlchemy ORM 对象 → TypeError;
                # row_to_connection_spec 会读 ORM 字段 + 解密 password + 解析 JSON 列
                spec = row_to_connection_spec(conn_row)
                kind_for_introspect = "vs" if sub_kind == "es" and src.get("metadata", {}).get("ext_vs") else ""
                connector = build_connector(spec=spec, source_id=int(raw), kind=kind_for_introspect)
                snapshot = connector.introspect(sample_rows=0)  # 详情页不要 sample,慢且占带宽
                payload["dialect"] = getattr(snapshot, "dialect", spec.dialect or "")
                payload["tables"] = [
                    {
                        "name": t.name,
                        "schema": t.schema,
                        "comment": t.comment,
                        "columns": [
                            {
                                "name": c.name, "type": c.type, "nullable": c.nullable,
                                "primary_key": c.primary_key, "comment": c.comment,
                            }
                            for c in t.columns
                        ],
                        "row_count_estimate": t.row_count_estimate,
                    }
                    for t in snapshot.tables
                ]
            except Exception as e:  # noqa: BLE001
                logger.exception("structured introspect failed: %s", e)
                payload["tables"] = []
                payload["degraded"] = f"{type(e).__name__}: {e}"

        elif sub_kind in ("vector", "vs"):
            # 向量库:列集合 / collections — 用 introspect 拿表名,VectorKbConnector 内部
            # 把 collections 模拟成 TableInfo
            try:
                from chayuan.server.knowledge_source.registry import build_connector
                from chayuan.server.db.repository.knowledge_source_repository import (
                    row_to_connection_spec,
                )
                conn_row = get_connection(int(src["connection_id"])) if src.get("connection_id") else None
                if conn_row is None:
                    raise RuntimeError("source 缺 connection_id 或已被删除")
                spec = row_to_connection_spec(conn_row)
                connector = build_connector(spec=spec, source_id=int(raw), kind="vs")
                snapshot = connector.introspect(sample_rows=0)
                payload["collections"] = [
                    {"name": t.name, "comment": t.comment, "row_count_estimate": t.row_count_estimate}
                    for t in snapshot.tables
                ]
            except Exception as e:  # noqa: BLE001
                logger.exception("vector introspect failed: %s", e)
                payload["collections"] = []
                payload["degraded"] = f"{type(e).__name__}: {e}"

        return {"code": 0, "msg": "ok", "data": payload}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("universe source detail failed: %r", e)
        raise HTTPException(500, f"source detail failed: {e}")


# ---------------------------------------------------------------------------
# POST /knowledge_universe/structured/rows  —— 分页拉某张表的真实行数据
# ---------------------------------------------------------------------------

@ku_router.post(
    "/structured/rows",
    summary="分页拉取结构化 KB 某张表的行(用于详情页右侧表格预览)",
)
def structured_fetch_rows(
    body: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    """合约:
        body = {
          ku_id: "src:42",
          table: "users",
          schema?: "",          # 可选,SQL Server / PG 的 schema 限定
          page: 1,              # 1-based
          page_size: 20,        # 1..200
        }
        →
        {
          code: 0,
          data: {
            columns: ["id","name",...],
            rows: [[1,"alice"], ...],
            total: 1234,
            page: 1,
            page_size: 20,
          }
        }

    安全:
      - 走 connector.introspect() 拿到合法 table 集合,把请求的 table 与之对照
        防 SQL 注入(表名不能直接 f-string 拼到 SELECT 里,用白名单白通过 + 用
        sqlalchemy.sql.quoted_name 包裹后再渲染)
      - read_only=True(连接器默认),即便 LLM/前端误传 DDL 也会被引擎拒绝
      - 仅 SQL/PG/MySQL/SQLite 系支持(SQLAlchemy 引擎);Mongo/ES 暂不支持,
        返 400 让 UI 给"该源类型暂不支持表预览"提示
    """
    ku_id = str(body.get("ku_id") or "")
    table = str(body.get("table") or "").strip()
    schema = str(body.get("schema") or "").strip()
    page = max(1, int(body.get("page") or 1))
    page_size = max(1, min(200, int(body.get("page_size") or 20)))

    if not ku_id or not table:
        raise HTTPException(400, "ku_id 与 table 必填")
    storage, raw = _decode_id(ku_id)
    if storage != "src":
        raise HTTPException(400, "仅 src:* 类的结构化 KB 支持表预览")

    try:
        from chayuan.server.db.repository.knowledge_source_repository import (
            get_source, get_connection, row_to_connection_spec,
        )
        from chayuan.server.knowledge_source.registry import build_connector
        from chayuan.server.knowledge_source.sql.connector import SqlConnector

        src = get_source(int(raw))
        if src is None:
            raise HTTPException(404, f"source #{raw} not found")
        sub_kind = (src.get("kind") or "").lower()
        if sub_kind not in ("sql",):
            raise HTTPException(400, f"暂不支持 {sub_kind} 类型的表预览(仅 SQL)")

        conn_row = get_connection(int(src["connection_id"])) if src.get("connection_id") else None
        if not conn_row:
            raise HTTPException(400, "源未配置 connection")
        spec = row_to_connection_spec(conn_row)
        connector = build_connector(spec=spec, source_id=int(raw))
        if not isinstance(connector, SqlConnector):
            raise HTTPException(400, "仅 SQL connector 支持表预览")

        # 白名单校验:用 introspect 拿合法表名
        snapshot = connector.introspect(sample_rows=0)
        valid = {t.name for t in snapshot.tables}
        valid_with_schema = {f"{t.schema}.{t.name}": t for t in snapshot.tables if t.schema}
        full_name = f"{schema}.{table}" if schema else table
        if table not in valid and full_name not in valid_with_schema:
            raise HTTPException(403, f"表 {full_name!r} 不在该源的可见列表内")

        engine = connector._get_engine()
        # 用 sqlalchemy quoted_name 防注入;同时尊重 schema
        from sqlalchemy.sql import quoted_name
        from sqlalchemy import text

        qtable = quoted_name(table, quote=True)
        qschema = quoted_name(schema, quote=True) if schema else None
        ref = f"{qschema}.{qtable}" if qschema else f"{qtable}"

        offset = (page - 1) * page_size
        with engine.connect() as cx:
            # 用 server-side cursor 友好;部分 dialect 不支持就直接 fetchall
            res = cx.execute(text(f"SELECT * FROM {ref} LIMIT :lim OFFSET :off"), {"lim": page_size, "off": offset})
            columns = list(res.keys())
            rows = [list(r) for r in res.fetchall()]
            # 总数:大表慢,但是 v1 先这样;有 row_count_estimate 时优先用估算
            total: Optional[int] = None
            try:
                cnt = cx.execute(text(f"SELECT COUNT(*) FROM {ref}")).scalar_one()
                total = int(cnt) if cnt is not None else None
            except Exception as e:  # noqa: BLE001
                logger.warning("count(*) for %s failed: %r", ref, e)
                total = None

        # JSON-safe 化 rows 里的非常规类型(datetime / Decimal / bytes 等)
        def _safe(v: Any) -> Any:
            if v is None or isinstance(v, (str, int, float, bool)):
                return v
            try:
                import datetime as _dt
                from decimal import Decimal
                if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
                    return v.isoformat()
                if isinstance(v, Decimal):
                    return float(v)
                if isinstance(v, (bytes, bytearray, memoryview)):
                    return f"<{len(bytes(v))} bytes>"
            except Exception:  # noqa: BLE001
                pass
            return str(v)

        rows_safe = [[_safe(c) for c in r] for r in rows]

        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "columns": columns,
                "rows": rows_safe,
                "total": total,
                "page": page,
                "page_size": page_size,
                "table": table,
                "schema": schema,
            },
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("structured fetch_rows failed: %s", e)
        raise HTTPException(500, f"fetch rows failed: {e}")


def _run_async_connector_search(connector: Any, query: str, top_k: int):
    from chayuan.server.knowledge_source.types import NLQuery

    return asyncio.run(connector.search(NLQuery(query=query, top_k=top_k)))


def _vector_chunks_to_table(chunks: List[Any]) -> Tuple[List[str], List[List[Any]]]:
    meta_keys: List[str] = []
    rows: List[Dict[str, Any]] = []
    for chunk in chunks:
        citation = getattr(chunk, "citation", None)
        meta = dict(getattr(citation, "meta", {}) or {})
        for key in meta.keys():
            if key not in meta_keys:
                meta_keys.append(str(key))
        rows.append({
            "score": round(float(getattr(chunk, "score", 0.0) or 0.0), 4),
            "content": getattr(chunk, "content", "") or "",
            "title": getattr(citation, "title", "") if citation else "",
            "collection": meta.get("collection", ""),
            "metadata": meta,
        })
    columns = ["score", "content", "title", "collection", *[f"meta.{k}" for k in meta_keys]]
    data: List[List[Any]] = []
    for row in rows:
        meta = row.pop("metadata", {}) or {}
        data.append([row.get("score"), row.get("content"), row.get("title"), row.get("collection"), *[meta.get(k, "") for k in meta_keys]])
    return columns, data


@ku_router.post(
    "/vector/rows",
    summary="预览外部向量库某个 collection 的检索样例(用于详情页右侧数据面板)",
)
def vector_fetch_rows(
    body: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    ku_id = str(body.get("ku_id") or "")
    collection = str(body.get("collection") or "").strip()
    query = str(body.get("query") or "").strip() or collection
    page = max(1, int(body.get("page") or 1))
    page_size = max(1, min(50, int(body.get("page_size") or 10)))
    if not ku_id or not collection:
        raise HTTPException(400, "ku_id 与 collection 必填")
    storage, raw = _decode_id(ku_id)
    if storage != "src":
        raise HTTPException(400, "仅 src:* 类向量库支持集合预览")
    try:
        from chayuan.server.db.repository.knowledge_source_repository import (
            get_source, get_connection, row_to_connection_spec,
        )
        from chayuan.server.knowledge_source.registry import build_connector

        src = get_source(int(raw))
        if src is None:
            raise HTTPException(404, f"source #{raw} not found")
        sub_kind = (src.get("kind") or "").lower()
        if sub_kind not in ("vector", "vs"):
            raise HTTPException(400, f"{sub_kind} 不是向量库")
        conn_row = get_connection(int(src["connection_id"])) if src.get("connection_id") else None
        if not conn_row:
            raise HTTPException(400, "源未配置 connection")
        spec = row_to_connection_spec(conn_row)
        snapshot = build_connector(spec=spec, source_id=int(raw), kind="vs").introspect(sample_rows=0)
        valid = {t.name for t in snapshot.tables}
        if collection not in valid:
            raise HTTPException(403, f"collection {collection!r} 不在该源的可见列表内")
        spec.allowed_collections = [collection]
        connector = build_connector(spec=spec, source_id=int(raw), kind="vs")
        chunks = _run_async_connector_search(connector, query, page_size)
        columns, rows = _vector_chunks_to_table(chunks or [])
        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "columns": columns,
                "rows": rows,
                "total": len(rows),
                "page": page,
                "page_size": page_size,
                "collection": collection,
                "query": query,
            },
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("vector fetch_rows failed: %s", e)
        raise HTTPException(500, f"vector rows failed: {e}")


# ---------------------------------------------------------------------------
# query_database config 构造:从 connection row 拼 sqlalchemy_connect_str
# ---------------------------------------------------------------------------

def _build_sql_config_for_source(conn_row: Any, top_k: int, *, model: Optional[str] = None) -> Dict[str, Any]:
    """把一行 knowledge_source.connection (ORM 行 / dict / ConnectionSpec) 转成
    query_database 期望的 config dict。

    支持的 dialect → sqlalchemy URL 映射(只挑常见的;其他直接拼 dialect+driver)。

    model 选择级联:
      1) 显式传入的 model(对话端把 composer.modelId 透传过来 — 用户选什么用什么)
      2) get_default_llm()(扫描可用 llm 列表挑一个)
      3) 全空 → 抛 RuntimeError 让上层包装清晰文案,而不是落到 NL→SQL 内部去抛
         "模型 '' 未配置"

    注意 conn_row 的来源:
      - get_connection(...) 返 KnowledgeSourceConnectionModel ORM 对象 → 走属性
      - 历史代码也可能传 dict / ConnectionSpec → 走 .get / 属性
      - password 字段需要解密(原始 ORM 是 password_enc),用 row_to_connection_spec
    """
    from chayuan.server.db.repository.knowledge_source_repository import (
        row_to_connection_spec,
    )
    # 统一收敛到 ConnectionSpec(密码已解密),后面读字段就不用纠结来源
    if hasattr(conn_row, "password_enc"):
        spec = row_to_connection_spec(conn_row)
        dialect = (spec.dialect or "").lower()
        user, pwd = spec.username or "", spec.password or ""
        host, port, db = spec.host or "", spec.port or 0, spec.database or ""
    elif isinstance(conn_row, dict):
        dialect = (conn_row.get("dialect") or "").lower()
        user = conn_row.get("username") or ""
        pwd = conn_row.get("password") or ""
        host = conn_row.get("host") or ""
        port = conn_row.get("port") or 0
        db = conn_row.get("database") or ""
    else:
        dialect = (getattr(conn_row, "dialect", "") or "").lower()
        user = getattr(conn_row, "username", "") or ""
        pwd = getattr(conn_row, "password", "") or ""
        host = getattr(conn_row, "host", "") or ""
        port = getattr(conn_row, "port", 0) or 0
        db = getattr(conn_row, "database", "") or ""

    # 由 dialect 取默认 driver
    _DRIVER = {
        "mysql": "mysql+pymysql",
        "postgresql": "postgresql+psycopg2",
        "postgres": "postgresql+psycopg2",
        "mssql": "mssql+pymssql",
        "oracle": "oracle+oracledb",
        "sqlite": "sqlite",
    }
    driver = _DRIVER.get(dialect, dialect or "sqlite")

    if driver == "sqlite":
        url = f"sqlite:///{db or ':memory:'}"
    else:
        auth = f"{user}:{pwd}@" if user else ""
        port_str = f":{port}" if port else ""
        url = f"{driver}://{auth}{host}{port_str}/{db}"

    from chayuan.settings import Settings
    # 按级联挑 model_name(见函数 docstring)
    model_name = (model or "").strip()
    if not model_name:
        try:
            from chayuan.server.utils import get_default_llm
            model_name = (get_default_llm() or "").strip()
        except Exception:  # noqa: BLE001
            model_name = ""
    if not model_name:
        raise RuntimeError(
            "未配置可用 LLM:请到「模型广场」添加并启用至少一个平台的 llm_models,"
            "或在 model_settings.yaml 显式设 DEFAULT_LLM_MODEL。"
        )

    return {
        "model_name": model_name,
        "top_k": top_k,
        "return_intermediate_steps": True,
        "sqlalchemy_connect_str": url,
        "read_only": True,  # universe.ask 默认只读
        "table_names": [],
        "table_comments": {},
    }


# ---------------------------------------------------------------------------
# NL→SQL 结果"去 SQL 化" —— 把 query_database 的自由文本拆成
# (sql, rows, debug_text),再让 LLM 写一段亲和的中文总结。
#
# 设计目标:用户问"都有谁注册了账户"不该看到 [(123,'alice',...)],而要看到
# "目前有 8 位用户:alice / bob / ... 最早 2025-01 注册,最近一位 ..."
# 这种举一反三的总结,同时保留底层 sql + rows 给好奇用户/审计回查。
# ---------------------------------------------------------------------------

import re as _re

_SQL_FROM_TEXT_RE = _re.compile(r"执行的sql[:：]'([\s\S]+?)'", _re.M)
_SQL_FENCED_RE = _re.compile(r"```(?:sql)?\s*([\s\S]+?)\s*```", _re.I)
_SQL_LANGCHAIN_RE = _re.compile(r"SQLQuery[:：]\s*([\s\S]+?)(?:\nSQLResult|\nAnswer|$)", _re.I)
_SQL_RAW_SELECT_RE = _re.compile(
    r"\b((?:WITH\s+[\s\S]+?\s+)?SELECT\s+[\s\S]+?(?:;|$))", _re.I,
)


def _extract_sql_from_text(text: str) -> Optional[str]:
    """从 query_database 的自由文本里挖出 SQL,多 pattern 兜底。

    兼容以下常见输出形态:
      1. 自家 query_database 拼的 "执行的sql:'<SQL>'"
      2. langchain SQLDatabaseChain 原始 trace "SQLQuery: ...\\nSQLResult: ..."
      3. LLM 把 SQL 包在 ```sql ... ``` 围栏里
      4. 自由文本里直接出现 SELECT ... 语句

    任一命中后都再过一次 strip_sql_fences,保证不会把 ``` 或后置自白带进 SQL。
    """
    if not text:
        return None
    from chayuan.server.knowledge_source.sql.text2sql import strip_sql_fences

    candidates: List[str] = []
    for regex in (_SQL_FROM_TEXT_RE, _SQL_LANGCHAIN_RE, _SQL_FENCED_RE, _SQL_RAW_SELECT_RE):
        m = regex.search(text)
        if m and m.group(1):
            candidates.append(m.group(1))

    for raw in candidates:
        cleaned = strip_sql_fences(raw)
        if cleaned and _re.match(r"^\s*(WITH|SELECT)\b", cleaned, _re.I):
            return cleaned
    return None


def _rerun_sql_for_rows(sql: str, sqlalchemy_url: str, max_rows: int = 50) -> List[Dict[str, Any]]:
    """re-execute SQL 拿到结构化 rows(dict 形式)。

    intercept_sql 拦截器只允许 SELECT;任何写操作直接拒绝。出错返回空 list,
    由调用方决定是否 fallback 到原 text 显示。这里限制 max_rows 避免一次返回
    巨量数据塞炸前端。
    """
    if not sql or not sqlalchemy_url:
        return []
    try:
        from sqlalchemy import create_engine, text as _sql_text  # type: ignore
        eng = create_engine(sqlalchemy_url, future=True)
        with eng.connect() as conn:
            res = conn.execute(_sql_text(sql))
            cols = list(res.keys())
            rows = res.fetchmany(max_rows + 1)  # +1 用来判断是否截断
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        out: List[Dict[str, Any]] = []
        for r in rows:
            d: Dict[str, Any] = {}
            for i, c in enumerate(cols):
                v = r[i]
                # datetime / Decimal / bytes 走 str 化避免 JSON 序列化炸
                try:
                    import json as _json
                    _json.dumps(v)
                    d[c] = v
                except (TypeError, ValueError):
                    d[c] = str(v)
            out.append(d)
        if truncated:
            out.append({"__truncated__": f"仅展示前 {max_rows} 行,实际更多"})
        return out
    except Exception as e:  # noqa: BLE001
        logger.info("rerun_sql_for_rows failed (%s): %r", sql[:80], e)
        return []


_SUMMARIZE_PROMPT = """你是一位贴心的数据助手,帮用户把数据库查询结果用人话讲清楚。

用户的问题:{query}
执行的 SQL:{sql}
查询结果(JSON,可能截断到前 {n} 行):
{rows_json}

请用自然、亲切的中文写一段简短总结(2~4 句),要求:
1. 直接回答用户原问题,不要复述 SQL,不要 markdown 表格
2. 提炼数量、典型样本、明显规律或极值(最早/最新/最多/最少)
3. 语气温和有人情味,但不油腻;别用"亲爱的"之类
4. 如果结果为空,温和说明"暂时没找到相关数据"并给出可能原因
5. 一段话,不超过 120 字

总结:"""


def _summarize_structured(query: str, sql: Optional[str], rows: List[Dict[str, Any]],
                          model_name: str) -> Optional[str]:
    """让 LLM 把 rows 写成一段亲和的中文总结。失败返回 None,调用方 fallback。"""
    if not model_name:
        return None
    try:
        from chayuan.server.utils import get_ChatOpenAI
        import json as _json
        # 只截取前 30 行做摘要;太多会塞 context
        sample = rows[:30]
        rows_json = _json.dumps(sample, ensure_ascii=False, default=str)[:6000]
        prompt = _SUMMARIZE_PROMPT.format(
            query=query, sql=(sql or "<未捕获>"), n=len(sample), rows_json=rows_json
        )
        llm = get_ChatOpenAI(
            model_name=model_name, temperature=0.4, streaming=False, local_wrap=True, verbose=False,
        )
        # langchain BaseChatModel.invoke 接受 string / list[BaseMessage];这里 string OK
        out = llm.invoke(prompt)
        text = getattr(out, "content", None) or str(out)
        text = (text or "").strip()
        # 防御:有些模型会带 "总结:" 前缀
        for prefix in ("总结:", "总结:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        return text or None
    except Exception as e:  # noqa: BLE001
        logger.info("summarize_structured failed: %r", e)
        return None


# ---------------------------------------------------------------------------
# 跨 KB 综合总结 — 把所有 block 的命中合并喂给 LLM,生成单一答案 + [N] 引用
#
# 这是 deepseek/千问/perplexity 这类对话界面的核心逻辑:
#   "搜索 → 提取关键证据 → 综合写答案 → 用引用回链来源"。
# 服务端搞定这步,前端拿到完整 markdown 答案,源面板默认折叠。
# ---------------------------------------------------------------------------

_UNIVERSE_SYNTHESIS_SYSTEM = """你是一位帮用户从知识库快速找到答案的助手。规则:
1. 只基于"知识来源"中的内容回答,不要编造
2. 直接回答问题,语气自然亲切;不要用"以下是..."这种开场白
3. 引用证据时用 [1] [2] 这种标号,标号对应"知识来源"列表里的编号
4. 结构化数据(SQL 行/数字)优先直接给数值;文档片段引用关键句
5. 若来源完全无关或为空,简短说"暂未在所选知识库中找到相关信息",并建议下一步
6. 不超过 200 字;复杂场景可适当延长但不要堆砌
"""

_UNIVERSE_SYNTHESIS_USER = """用户问题:{query}

知识来源:
{evidence}

请直接回答问题,引用证据时用 [N] 标号。"""


def _build_evidence_blocks(blocks: List[Dict[str, Any]], per_source_chars: int = 600) -> List[Dict[str, Any]]:
    """从 universe blocks 里抽取每条来源的关键文本,做成有编号的证据列表。
    返回:[{idx, name, kind, text}, ...],idx 从 1 开始,与前端引用面板的顺序一致。
    """
    out: List[Dict[str, Any]] = []
    idx = 0
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        ku_id = str(block.get("ku_id") or "")
        kind = str(block.get("kind") or "")
        ok = bool(block.get("ok"))
        # 失败的块也带,让 LLM 知道某来源失败,可如实告诉用户
        if not ok and not block.get("error"):
            continue
        idx += 1
        name = ku_id
        pieces: List[str] = []
        if not ok:
            pieces.append(f"(查询失败:{block.get('error') or '未知原因'})")
        if kind == "structured":
            if block.get("summary"):
                pieces.append(str(block["summary"]).strip())
            elif block.get("text"):
                pieces.append(str(block["text"]).strip())
            sql = (block.get("sql") or "").strip()
            if sql:
                pieces.append(f"SQL: {sql[:200]}")
            rows = block.get("rows") or []
            if isinstance(rows, list) and rows:
                head_rows = rows[:3]
                pieces.append("行: " + "; ".join(
                    (", ".join(f"{k}={v}" for k, v in r.items()) if isinstance(r, dict) else str(r))
                    for r in head_rows
                ))
                if len(rows) > 3:
                    pieces.append(f"(共 {len(rows)} 行)")
        elif kind == "document":
            results = block.get("results") or []
            for hit in results[:3]:
                if not isinstance(hit, dict):
                    continue
                file_name = hit.get("file_name") or hit.get("source") or ""
                snippet = (hit.get("snippet") or hit.get("content") or "").strip()
                if not snippet:
                    continue
                head = f"《{file_name}》" if file_name else ""
                pieces.append(f"{head}{snippet[:per_source_chars]}".strip())
        elif kind == "vector":
            hits = block.get("hits") or []
            for hit in hits[:3]:
                if not isinstance(hit, dict):
                    continue
                content = (hit.get("content") or "").strip()
                if content:
                    pieces.append(content[:per_source_chars])
        elif kind == "image":
            results = block.get("results") or []
            for hit in results[:3]:
                if not isinstance(hit, dict):
                    continue
                caption = (hit.get("caption") or hit.get("title") or "").strip()
                if caption:
                    pieces.append(f"[图] {caption[:per_source_chars]}")
        text = "\n".join(p for p in pieces if p).strip()
        if not text:
            text = "(该来源命中但无可读文本)"
        out.append({"idx": idx, "name": name, "kind": kind, "text": text})
    return out


def _synthesize_universe_answer(
    query: str,
    blocks: List[Dict[str, Any]],
    model_name: Optional[str],
) -> str:
    """对所有 block 的命中做一次 LLM 综合总结,返回一段答案文本。失败返回 ''。"""
    if not query or not blocks:
        return ""
    evidence = _build_evidence_blocks(blocks)
    if not evidence:
        return ""
    if not model_name:
        try:
            from chayuan.server.utils import get_default_llm
            model_name = (get_default_llm() or "").strip()
        except Exception:  # noqa: BLE001
            model_name = ""
    if not model_name:
        return ""
    try:
        from chayuan.server.utils import get_ChatOpenAI
        evidence_text = "\n\n".join(
            f"[{e['idx']}] ({e['kind']}) {e['name']}:\n{e['text']}"
            for e in evidence
        )
        prompt_user = _UNIVERSE_SYNTHESIS_USER.format(query=query, evidence=evidence_text)
        llm = get_ChatOpenAI(
            model_name=model_name,
            temperature=0.3,
            streaming=False,
            local_wrap=True,
            verbose=False,
        )
        # 用 system + user 两条消息;ChatOpenAI 兼容 list[BaseMessage] 与 list[tuple]
        from langchain_core.messages import SystemMessage, HumanMessage
        out = llm.invoke([
            SystemMessage(content=_UNIVERSE_SYNTHESIS_SYSTEM),
            HumanMessage(content=prompt_user),
        ])
        text = getattr(out, "content", None) or str(out)
        return (text or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.info("synthesize_universe_answer failed: %r", e)
        return ""


# ---------------------------------------------------------------------------
# 文档 KB 检索意图兜底:概览 / 文件名优先
# ---------------------------------------------------------------------------

_KB_OVERVIEW_RE = re.compile(
    r"(本知识库|这个知识库|该知识库|知识库).*(有什么|有哪些|包含什么|文件|内容)"
    r"|^(有什么|有哪些|有哪些文件|文件列表|内容列表)$"
)

_FILENAME_STOPWORDS = (
    "本知识库", "这个知识库", "该知识库", "知识库", "文档", "文件", "内容", "里面", "里", "中",
    "有什么", "有哪些", "是什么", "请问", "请", "帮我", "查找", "查询", "搜索", "一下",
    "什么", "多少", "几个", "多少个", "列表", "清单", "是否", "可以", "能不能",
)


def _kb_files(kb_name: str) -> List[Dict[str, Any]]:
    try:
        from chayuan.server.knowledge_base.kb_service.base import get_kb_file_details
        return list(get_kb_file_details(kb_name) or [])
    except Exception as e:  # noqa: BLE001
        logger.info("get_kb_file_details failed(kb=%s): %r", kb_name, e)
        return []


def _is_kb_overview_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    # “提示词文件有什么”这类问题看起来也包含“有什么”，但用户真正要的是
    # 命中文件名后读取内容，不能提前短路成 KB 清单。
    if _has_filename_intent(q):
        return False
    return bool(_KB_OVERVIEW_RE.search(q))


def _inventory_doc(kb_name: str, query: str, embed_model_used: str):
    from langchain_core.documents import Document
    from chayuan.server.file_rag.result_envelope import to_wire

    files = _kb_files(kb_name)
    indexed = [f for f in files if f.get("in_db")]
    lines = [
        f"本知识库共有 {len(files)} 个文件，其中 {len(indexed)} 个已完成入库/索引。",
    ]
    for f in files[:30]:
        status = "已索引" if f.get("in_db") else "未索引"
        docs_count = f.get("docs_count", f.get("doc_count"))
        suffix = f"，{docs_count} 个片段" if docs_count not in (None, "") else ""
        lines.append(f"- {f.get('file_name') or '<未命名>'}（{status}{suffix}）")
    if len(files) > 30:
        lines.append(f"- 其余 {len(files) - 30} 个文件已省略，请用文件名继续追问。")
    doc = Document(
        page_content="\n".join(lines),
        metadata={"file_name": "知识库概览", "source": kb_name, "score": 1.0},
    )
    return to_wire(
        doc,
        query=query,
        kb_name=kb_name,
        embed_model_used=embed_model_used,
        retrieval_path="inventory",
    )


def _filename_terms(query: str) -> List[str]:
    q = (query or "").strip().lower()
    if not q:
        return []
    q = re.sub(r"[?？!！,，。；;：:\s]+", "", q)
    for word in sorted(_FILENAME_STOPWORDS, key=len, reverse=True):
        q = q.replace(word, "")
    terms = [q] if len(q) >= 2 else []
    try:
        import jieba  # type: ignore
        terms.extend(t.strip().lower() for t in jieba.cut_for_search(query) if len(t.strip()) >= 2)
    except Exception:  # noqa: BLE001
        terms.extend(t for t in re.split(r"[^\w一-鿿]+", query.lower()) if len(t) >= 2)
    out: List[str] = []
    seen = set()
    for term in terms:
        if term in seen or term in _FILENAME_STOPWORDS:
            continue
        seen.add(term)
        out.append(term)
    return out[:6]


def _has_filename_intent(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return False
    return bool(_filename_terms(q)) and any(mark in q for mark in ("文件", "文档", "."))


def _filename_docs_for_query(kb: Any, kb_name: str, query: str, top_k: int) -> List[Any]:
    """文件名召回:对"提示词文件有什么"先命中文件名,再取该文件 chunk。"""
    terms = _filename_terms(query)
    if not terms or kb is None:
        return []
    files = _kb_files(kb_name)
    matched: List[str] = []
    for f in files:
        name = str(f.get("file_name") or "")
        low = name.lower()
        if any(term and term in low for term in terms):
            matched.append(name)
    docs: List[Any] = []
    for file_name in matched[:8]:
        try:
            file_docs = kb.list_docs(file_name=file_name) or []
            for d in file_docs:
                meta = getattr(d, "metadata", None) or {}
                if isinstance(meta, dict):
                    meta.setdefault("file_name", file_name)
                    meta.setdefault("source", file_name)
                    meta["retrieval_path"] = "filename"
                    try:
                        d.metadata = meta
                    except Exception:  # noqa: BLE001
                        pass
            docs.extend(file_docs)
        except Exception as e:  # noqa: BLE001
            logger.info("filename list_docs failed(kb=%s,file=%s): %r", kb_name, file_name, e)
        if len(docs) >= max(top_k, 1):
            break
    return docs[: max(top_k, 1)]


def _content_terms(query: str) -> List[str]:
    """提取适合做精确内容召回的关键词,尤其保留 URL / 域名。

    纯向量召回对 ``aidooo.com点击后干什么`` 这类问题不稳定:URL 是关键证据,
    但会被自然语言部分稀释。这里把 URL/domain 和中文关键词作为轻量兜底。
    """
    q = (query or "").strip()
    if not q:
        return []
    terms: List[str] = []
    # URL / domain 优先,保留点号。
    terms.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9.-]+\.[A-Za-z]{2,}", q))
    try:
        import jieba  # type: ignore
        terms.extend(t.strip() for t in jieba.cut_for_search(q) if t.strip())
    except Exception:  # noqa: BLE001
        terms.extend(t for t in re.split(r"[^\w一-鿿.]+", q) if t)

    stop = set(_FILENAME_STOPWORDS) | {
        "后", "干什么", "做什么", "怎么办", "如何", "点击后", "点击", "打开",
        "com", "www", "http", "https",
    }
    out: List[str] = []
    seen = set()
    for raw in terms:
        term = raw.strip().strip("。？?！!,，；;：:")
        if len(term) < 2:
            continue
        low = term.lower()
        if low in seen or low in stop or term in stop:
            continue
        seen.add(low)
        out.append(term)
    return out[:8]


def _doc_content_and_meta(doc: Any) -> Tuple[str, Dict[str, Any]]:
    if hasattr(doc, "page_content"):
        return (getattr(doc, "page_content", "") or ""), dict(getattr(doc, "metadata", None) or {})
    if isinstance(doc, dict):
        return (doc.get("page_content") or doc.get("content") or ""), dict(doc.get("metadata") or {})
    return "", {}


def _content_docs_for_query(kb: Any, kb_name: str, query: str, top_k: int) -> List[Any]:
    """精确内容召回兜底:扫描已入库 chunks,命中 URL / 标识符 / 关键词就作为候选。

    只在 ask 链路里作为一条候选路参与融合,不会替代向量召回。这样 URL、公司名、
    APIKey、配置项等低频字面证据能被稳定列为出处。
    """
    try:
        from chayuan.server.file_rag.hybrid_service import lexical_search_docs
        return lexical_search_docs(kb, query, top_k)
    except Exception as e:  # noqa: BLE001
        logger.info("content lexical list_docs failed(kb=%s): %r", kb_name, e)
        return []


# ---------------------------------------------------------------------------
# POST /knowledge_universe/ask
# ---------------------------------------------------------------------------

def _process_one_ku(
    ku_id: str,
    query: str,
    top_k: int,
    *,
    model: Optional[str] = None,
    use_hybrid: Optional[bool] = None,
    use_rerank: Optional[bool] = None,
    embed_model: Optional[str] = None,
    rewrite_strategy: Optional[str] = None,
    structured_scopes: Optional[Dict[str, List[str]]] = None,
    vector_scopes: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """对单个 ku_id 跑一次问答;**永不抛**,异常压成 block.error 返回。

    被 /ask(同步)和 /ask/stream(并发 to_thread)共用。
    - `model`:LLM 名(structured 那边走 NL→SQL,rewriter 改写也用它);空时走默认级联。
    - `use_hybrid` / `use_rerank`:按请求覆盖检索增强开关;None=全局默认。
    - `embed_model`:兼容旧客户端字段；文档 KB 查询会忽略它,强制使用 KB 创建时记录的嵌入模型。
    - `rewrite_strategy`:'auto'(默认) / 'passthrough' / 'hyde' / 'multi_query' /
      'decompose' / 'off';仅 document KB 生效。
    - `structured_scopes` / `vector_scopes`:按详情页当前选择限制表或集合范围。
    """
    block: Dict[str, Any] = {"ku_id": ku_id, "kind": None, "ok": False}
    try:
        storage, raw = _decode_id(ku_id)

        if storage == "doc":
            block["kind"] = "document"
            from chayuan.server.knowledge_base.kb_doc_api import search_docs
            from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
            from chayuan.server.file_rag.result_envelope import to_wire_list
            from chayuan.settings import Settings
            # 取 KB 配置:查询必须使用 KB 创建/重建索引时记录的嵌入模型。
            # 旧客户端可能仍传 embed_model,这里明确忽略,避免 query 向量空间与索引不一致。
            kb_default_embed = ""
            kb = None
            try:
                kb = KBServiceFactory.get_service_by_name(raw)
                if kb is not None:
                    kb_default_embed = getattr(kb, "embed_model", "") or ""
            except Exception:  # noqa: BLE001
                pass
            embed_model_used = kb_default_embed

            if _is_kb_overview_query(query):
                block["ok"] = True
                block["embed_model"] = embed_model_used
                block["rewritten_queries"] = [query]
                block["rewrite_trace"] = {"strategy_used": "passthrough", "elapsed_ms": 0, "n": 1}
                block["results"] = [_inventory_doc(raw, query, embed_model_used)]
                return block

            # —— Stage 1:Query Rewriter ——
            # auto 路由:短/含代词→hyde,复杂→multi_query,其它 passthrough。
            # 失败任何 stage → 退回 [original],绝不阻塞。
            from chayuan.server.file_rag.retrieval_pipeline import rewriter as _rw
            queries, rewrite_trace = _rw.run(
                query,
                strategy=(rewrite_strategy or "auto"),  # type: ignore[arg-type]
                model_name=model,
            )

            # —— Stage 2:Per-query Retrieval(并发 N 条改写) ——
            # 每条 query 独立向量检索,等量候选;然后 RRF 融合 + 去重 + sandwich。
            # 单条改写失败不阻塞,记空列表参与 RRF。
            from chayuan.server.file_rag.retrieval_pipeline.fuser import (
                dedup_by_chunk_key, rrf_fuse, sandwich_reorder,
            )
            ranked_lists = []
            search_reasons: List[str] = []
            filename_docs = _filename_docs_for_query(kb, raw, query, top_k * 2)
            if filename_docs:
                ranked_lists.append(filename_docs)
            content_docs = _content_docs_for_query(kb, raw, query, top_k * 2)
            if content_docs:
                ranked_lists.append(content_docs)
            for q in queries:
                try:
                    one_list = search_docs(
                        query=q, knowledge_base_name=raw, top_k=top_k,
                        score_threshold=Settings.kb_settings.SCORE_THRESHOLD,
                        use_hybrid=use_hybrid, use_rerank=use_rerank,
                    )
                    ranked_lists.append(one_list or [])
                    # 收集每条 query 的失败原因(只有 0 命中时 KBService 会写)
                    if not one_list and kb is not None:
                        r = getattr(kb, "_last_search_reason", None)
                        if r:
                            search_reasons.append(r)
                except Exception as _e:  # noqa: BLE001
                    logger.info("search_docs(rewritten q=%r) failed: %r", q, _e)
                    ranked_lists.append([])
                    search_reasons.append(f"q={q!r}: {type(_e).__name__}: {_e}")

            # —— Stage 3:RRF 融合 ——
            # 单条 query 时 RRF 退化为按 rank 等价排序,无副作用,代码路径统一。
            fused = rrf_fuse(ranked_lists, top_n=top_k * 3)
            fused = dedup_by_chunk_key(fused)[:top_k]

            # —— Stage 4:Sandwich 重排 ——
            docs = sandwich_reorder(fused)
            block["rewritten_queries"] = queries
            block["rewrite_trace"] = rewrite_trace
            # retrieval_path 由 hybrid_service 写到 doc.metadata 里(rerank_score / 等),
            # 这里给一个聚合标签:有 rerank_score 就标 rerank,否则 vector。
            def _path_for(d):
                m = d.get("metadata") if isinstance(d, dict) else getattr(d, "metadata", {}) or {}
                if m.get("retrieval_path"):
                    return str(m.get("retrieval_path"))
                return "rerank" if m.get("rerank_score") not in (None, "") else "vector"
            block["ok"] = True
            block["embed_model"] = embed_model_used
            # 0 命中时把"为什么"也带回去 — UI 在 EmptyDocResult 里展示,排错不再黑盒
            if not docs and search_reasons:
                # 多条改写的原因可能重复,去重保留首条最具代表性的
                seen = set()
                uniq = []
                for r in search_reasons:
                    if r and r not in seen:
                        seen.add(r)
                        uniq.append(r)
                block["diagnostic"] = " | ".join(uniq[:3])
            block["results"] = [
                # 单条命中按 envelope 打包;retrieval_path 按是否被 rerank 标记
                _r for _r in (
                    {**w, "retrieval_path": _path_for(d)}
                    for d, w in zip(
                        (docs or [])[:top_k],
                        to_wire_list(
                            (docs or [])[:top_k],
                            query=query,
                            kb_name=raw,
                            embed_model_used=embed_model_used,
                            retrieval_path="vector",
                        ),
                    )
                )
            ]
            try:
                from chayuan.server.db.repository import annotation_repository

                votes = annotation_repository.rag_document_votes(knowledge_base_id=raw)
                if votes and isinstance(block.get("results"), list):
                    def _vote_key(hit: Dict[str, Any]) -> str:
                        fname = str(hit.get("file_name") or hit.get("source") or "").strip()
                        chunk = hit.get("chunk_index")
                        return f"{fname}#chunk={chunk}" if fname and chunk not in (None, "") else fname

                    adjusted = []
                    for hit in block["results"]:
                        if not isinstance(hit, dict):
                            adjusted.append(hit)
                            continue
                        key = _vote_key(hit)
                        vote = votes.get(key) or votes.get(str(hit.get("file_name") or ""))
                        if vote:
                            pos = int(vote.get("positive") or 0)
                            neg = int(vote.get("negative") or 0)
                            net = pos - neg
                            old_score = float(hit.get("score") or 0.0)
                            hit = {
                                **hit,
                                "score": old_score + (0.015 * net),
                                "annotation_signal": {
                                    "positive": pos,
                                    "negative": neg,
                                    "net": net,
                                    "task_ids": list(vote.get("task_ids") or [])[:5],
                                },
                            }
                        adjusted.append(hit)
                    adjusted.sort(
                        key=lambda x: float(x.get("score") or 0.0) if isinstance(x, dict) else 0.0,
                        reverse=True,
                    )
                    block["results"] = adjusted[:top_k]
                    block["annotation_loop"] = {
                        "enabled": True,
                        "matched": sum(1 for x in adjusted if isinstance(x, dict) and x.get("annotation_signal")),
                    }
            except Exception as e:  # noqa: BLE001
                logger.debug("annotation ranking signal skipped: %r", e)

        elif storage == "src":
            from chayuan.server.db.repository.knowledge_source_repository import get_source
            src = get_source(int(raw))
            if src is None:
                block["error"] = "source not found"
            else:
                sub_kind = (src.get("kind") or "").lower()
                block["kind"] = _SOURCE_KIND_MAP.get(sub_kind, sub_kind)
                if sub_kind in ("sql", "mongo", "es"):
                    # NL → SQL:走 query_database 直调,sqlalchemy URL 现场拼。
                    # text2sql tool 实际签名只取 query 字符串,旁路 get_tool_config 默认连接,
                    # 不能跨 source 切换;这里走 query_database 直接传 source 自己的 URL。
                    try:
                        # —— 新管道:用 chayuan 自家 generate_sql + ensure_readonly + _rerun_sql_for_rows ——
                        # 老路径走 LangChain SQLDatabaseChain,intermediate_steps 解析和新版 langchain 不兼容
                        # (LLMChain 也已被 deprecate),会导致 SQL 抽不出来 → 没行 → 没 summary。
                        # 这里直接调 chayuan 的 structured-LLM text2sql,得到 Pydantic 类型化 {sql, reason},
                        # 再过 readonly 校验,再用本地 _rerun_sql_for_rows 执行,流程稳定可观察。
                        from chayuan.server.knowledge_source.registry import build_connector
                        from chayuan.server.knowledge_source.sql.text2sql import generate_sql
                        from chayuan.server.knowledge_source.sql.safety import ensure_readonly
                        from chayuan.server.knowledge_source.types import NLQuery
                        from chayuan.server.db.repository.knowledge_source_repository import (
                            get_connection,
                            row_to_connection_spec,
                        )

                        conn = get_connection(int(src["connection_id"])) if src.get("connection_id") else None
                        if not conn:
                            raise RuntimeError("source 缺 connection_id 或已损坏")
                        cfg = _build_sql_config_for_source(conn, top_k, model=model)
                        spec = row_to_connection_spec(conn)
                        dialect = (spec.dialect or "sqlite").lower()
                        sqlalchemy_url = cfg["sqlalchemy_connect_str"]
                        model_name = cfg["model_name"]

                        # 1) 取 schema snapshot(可被 structured_scopes 限制)
                        raw_scope = (structured_scopes or {}).get(ku_id) or (structured_scopes or {}).get(raw) or []
                        connector = build_connector(spec=spec, source_id=int(raw), kind="")
                        snapshot = connector.introspect(sample_rows=3)
                        if raw_scope:
                            allowed = {str(x).strip() for x in raw_scope if str(x).strip()}
                            kept = [
                                t for t in snapshot.tables
                                if t.name in allowed
                                or (getattr(t, "schema", None) and f"{t.schema}.{t.name}" in allowed)
                            ]
                            if kept:
                                snapshot.tables = kept
                                block["scope"] = {"tables": [t.name for t in kept]}

                        logger.info(
                            "structured.text2sql start ku_id=%s query=%r model=%s dialect=%s tables=%d scope=%s",
                            ku_id, query, model_name, dialect, len(snapshot.tables), raw_scope or [],
                        )
                        if not snapshot.tables:
                            block["error"] = (
                                "结构化检索失败:数据源里没有可访问的表,或权限白名单为空。"
                                "请到 source 详情确认连接可达 + 至少有一张表在白名单中。"
                            )
                            return block

                        # 2) NL → SQL (单次结构化 LLM 调用)
                        nl = NLQuery(query=query, top_k=top_k, llm_model=model_name)
                        gen = generate_sql(nl, snapshot, dialect=dialect, llm_model=model_name)
                        sql = (gen.get("sql") or "").strip()
                        reason = gen.get("reason") or ""
                        logger.info(
                            "structured.text2sql generated ku_id=%s sql=%r reason=%r",
                            ku_id, sql, reason[:200],
                        )
                        if not sql:
                            block["error"] = (
                                f"结构化检索失败:LLM 未能为问题生成可执行 SQL。原因:{reason or '(未提供)'}"
                            )
                            return block

                        # 3) 只读安全校验(SELECT/WITH 才放行,DML/DDL 拒绝)
                        try:
                            ensure_readonly(sql, dialect=dialect)
                        except Exception as safety_err:  # noqa: BLE001
                            logger.warning(
                                "structured.text2sql sql blocked by readonly check ku_id=%s sql=%r err=%r",
                                ku_id, sql, safety_err,
                            )
                            block["error"] = (
                                f"生成的 SQL 未通过只读安全校验,已拒绝执行:{safety_err}\n\nSQL:\n{sql}"
                            )
                            return block

                        # 4) 执行 SQL → rows
                        rows = _rerun_sql_for_rows(sql, sqlalchemy_url, max_rows=50)
                        logger.info(
                            "structured.text2sql rerun_rows ku_id=%s row_count=%d",
                            ku_id, len(rows or []),
                        )
                        block["ok"] = True
                        block["sql"] = sql
                        if rows:
                            block["rows"] = rows
                            first = rows[0]
                            if isinstance(first, dict):
                                block["columns"] = list(first.keys())
                        # text 兼容老前端:summary 缺席时仍能展示有意义内容
                        block["text"] = (
                            f"查询结果:{len(rows)} 行\n\n执行的sql:'{sql}'"
                        )

                        # 5) 让 LLM 把 rows 写成亲和中文总结(独立的非流式调用,失败不阻塞)
                        summary = _summarize_structured(query, sql, rows, model_name)
                        if summary:
                            block["summary"] = summary
                    except Exception as e:  # noqa: BLE001
                        logger.exception(
                            "structured.text2sql failed ku_id=%s query=%r: %r",
                            ku_id, query, e,
                        )
                        # 把常见的"模型/网络层异常"翻译成对用户友好的提示。
                        # LangChain SQLDatabaseChain 串行三次 LLM round-trip,
                        # 任何一次远端无响应/中断都会以这些类型/消息抛出。
                        ename = type(e).__name__
                        emsg = str(e)
                        msg_lower = emsg.lower()

                        # 1) 模型不存在 / 平台请求失败 (chayuan-server 自家代理把
                        #    ollama 404 包成 502 + 中文 detail; openai 自身的 404 也走这条)。
                        model_missing = (
                            "model_not_found" in msg_lower
                            or "not_found_error" in msg_lower
                            or "model not found" in msg_lower
                            or ("not found" in msg_lower and "model=" in msg_lower)
                            or "模型请求失败" in emsg
                        )
                        # 2) 网络/响应超时
                        is_timeout = ename in (
                            "TimeoutError",
                            "ReadTimeout",
                            "ReadTimeoutError",
                            "APITimeoutError",
                            "ConnectTimeout",
                            "ConnectTimeoutError",
                        ) or "timeout" in msg_lower
                        # 3) 流式 SSE 中段断开
                        is_streaming_break = (
                            "during streaming" in msg_lower
                            or ("stream" in msg_lower and ename in ("APIError", "APIConnectionError"))
                        )
                        # 4) 上游 5xx (ollama 进程挂了,模型加载失败等)
                        is_upstream_5xx = (
                            ename in ("InternalServerError", "BadGateway", "ServiceUnavailable")
                            or "502 bad gateway" in msg_lower
                            or "503 service unavailable" in msg_lower
                            or "504 gateway timeout" in msg_lower
                        )

                        if model_missing:
                            block["error"] = (
                                "结构化检索失败:所选模型在 LLM 后端不存在。"
                                f"原始报错:{emsg[:300]}\n"
                                "排查:1) `ollama list` 查看本机已拉取模型;"
                                "2) `ollama pull <正确的模型 tag>` 拉取;"
                                "3) 在 chayuan 设置里把默认模型改为已存在的 tag。"
                            )
                        elif is_timeout:
                            block["error"] = (
                                "结构化检索超时:LLM 在生成或执行 SQL 时未及时返回,"
                                "请确认所选模型可用、并适当提高客户端超时时长后重试。"
                            )
                        elif is_streaming_break:
                            block["error"] = (
                                "结构化检索失败:LLM 流式响应在中段断开。"
                                "若使用 ollama / 本地代理,请先重启模型服务并重试;"
                                "服务端已默认关闭非必要的 streaming 调用,本错误若仍持续请检查所选模型可用性。"
                            )
                        elif is_upstream_5xx:
                            block["error"] = (
                                "结构化检索失败:LLM 后端返回 5xx。"
                                f"原始报错:{emsg[:300]}\n"
                                "排查:确认 ollama / OpenAI 兼容服务已启动,且 api_base_url 可达。"
                            )
                        else:
                            block["error"] = f"NL→SQL 失败:{ename}: {emsg[:300]}"
                elif sub_kind in ("vector", "vs"):
                    try:
                        from chayuan.server.knowledge_source.registry import build_connector
                        from chayuan.server.db.repository.knowledge_source_repository import (
                            get_connection, row_to_connection_spec,
                        )
                        conn_row = get_connection(int(src["connection_id"])) if src.get("connection_id") else None
                        if conn_row is None:
                            raise RuntimeError("source 缺 connection_id")
                        spec = row_to_connection_spec(conn_row)
                        raw_scope = (vector_scopes or {}).get(ku_id) or (vector_scopes or {}).get(raw) or []
                        if raw_scope:
                            spec.allowed_collections = [
                                str(x).strip() for x in raw_scope if str(x).strip()
                            ]
                        connector = build_connector(spec=spec, source_id=int(raw), kind="vs")
                        chunks = _run_async_connector_search(connector, query, top_k)
                        hits = [
                            {
                                "content": getattr(c, "content", "") or "",
                                "score": float(getattr(c, "score", 0.0) or 0.0),
                                "metadata": dict(getattr(getattr(c, "citation", None), "meta", {}) or {}),
                            }
                            for c in (chunks or [])
                        ]
                        block["ok"] = True
                        if raw_scope:
                            block["scope"] = {"collections": list(spec.allowed_collections or [])}
                        block["hits"] = hits
                    except Exception as e:  # noqa: BLE001
                        block["error"] = f"向量检索失败:{type(e).__name__}: {e}"
                elif sub_kind == "image":
                    # P1:文本→图(text-to-image)。依赖跨模态 embedder(CLIP/SigLIP);
                    # 仅视觉模型(DINOv2/ResNet)的库会被 ImageConnector 自身识别并返
                    # 错误 chunk,这里把"全是错误 chunk"的情况翻译成 block.error。
                    try:
                        from chayuan.server.knowledge_source.registry import build_connector
                        from chayuan.server.knowledge_source.types import NLQuery
                        from chayuan.server.db.repository.knowledge_source_repository import (
                            get_connection, row_to_connection_spec,
                        )
                        conn_row = get_connection(int(src["connection_id"])) if src.get("connection_id") else None
                        if conn_row is None:
                            raise RuntimeError("source 缺 connection_id")
                        spec = row_to_connection_spec(conn_row)
                        connector = build_connector(spec=spec, source_id=int(raw), kind="image")
                        chunks = connector._search_sync(NLQuery(query=query, top_k=top_k))
                        # 翻译:全是 error chunk(score=0 + meta.error=True)→ block.error
                        valid = [c for c in (chunks or []) if not (c.citation.meta or {}).get("error")]
                        if not valid and chunks:
                            block["error"] = chunks[0].content
                        else:
                            block["ok"] = True
                            results = []
                            for c in valid:
                                meta = c.citation.meta or {}
                                # caption 优先 OCR 文本(用户可读),fallback 文件名
                                cap = ""
                                content = c.content or ""
                                if "**OCR 文本**" in content:
                                    cap = content.split("**OCR 文本**：", 1)[-1].strip()[:200]
                                if not cap:
                                    cap = c.citation.title or ""
                                results.append({
                                    "id": meta.get("image_id"),
                                    "path": meta.get("path"),
                                    "preview_url": c.citation.preview_url,
                                    "download_url": c.citation.download_url,
                                    "title": c.citation.title,
                                    "caption": cap,
                                    "score": float(c.score or 0.0),
                                })
                            block["results"] = results
                    except Exception as e:  # noqa: BLE001
                        block["error"] = f"图像检索失败:{type(e).__name__}: {e}"
                else:
                    block["error"] = f"未知 sub_kind: {sub_kind}"

    except Exception as e:  # noqa: BLE001
        logger.exception("universe.ask block failed (ku_id=%s): %r", ku_id, e)
        block["ok"] = False
        block["error"] = str(e)
    return block


@ku_router.post("/ask", summary="跨多个知识库提问(按 kind 自动 dispatch)")
async def universe_ask(
    query: str = Body(..., description="自然语言问题"),
    ku_ids: List[str] = Body(..., description="选中的 ku_id 列表"),
    top_k: int = Body(5),
    model: Optional[str] = Body(None, description="LLM 模型名(可选;空时走默认级联)"),
    embed_model: Optional[str] = Body(None, description="兼容旧客户端字段；文档 KB 查询会忽略，始终使用 KB 配置"),
    use_hybrid: Optional[bool] = Body(None, description="按请求覆盖 hybrid 检索;None=用全局默认"),
    use_rerank: Optional[bool] = Body(None, description="按请求覆盖 cross-encoder rerank;None=用全局默认"),
    rewrite_strategy: Optional[str] = Body(
        "auto",
        description="query 改写策略:auto(默认) / passthrough / rewrite / multi_query / hyde / decompose / off",
    ),
    structured_scopes: Optional[Dict[str, List[str]]] = Body(
        None,
        description="结构化库查询范围:{ku_id: [table_or_collection,...]};空表示整个结构化库",
    ),
    vector_scopes: Optional[Dict[str, List[str]]] = Body(
        None,
        description="向量库查询范围:{ku_id: [collection,...]};空表示整个向量库",
    ),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    """同步聚合(向后兼容路径);通过统一检索服务并发处理 ku_ids。"""
    if not query.strip():
        raise HTTPException(400, "query 不能为空")
    if not ku_ids:
        raise HTTPException(400, "ku_ids 不能为空")
    denied = [kid for kid in ku_ids if not can_read_ku(user, kid)]
    if denied:
        raise HTTPException(403, f"no read permission on ku_id(s): {', '.join(denied[:5])}")

    from chayuan.server.retrieval.universe import search_ku_blocks

    processor_kwargs = {
        "model": model,
        "embed_model": embed_model,
        "structured_scopes": structured_scopes,
        "vector_scopes": vector_scopes,
    }
    results, errors = await search_ku_blocks(
        [(kid, query, "query", []) for kid in ku_ids],
        top_k=top_k,
        use_hybrid=use_hybrid,
        use_rerank=use_rerank,
        rewrite_strategy=rewrite_strategy or "auto",
        processor_kwargs=processor_kwargs,
    )
    for block in results:
        block.pop("_query_tag", None)
        block.pop("_section_ids", None)
    if errors:
        for block in results:
            if not block.get("ok") and not block.get("error"):
                block["error"] = block.get("diagnostic") or "search failed"
    return {"code": 0, "msg": "ok", "data": {"query": query, "results": results}}


@ku_router.post("/ask/stream", summary="跨多个知识库提问 SSE 流式(并发,完成一个推一个)")
async def universe_ask_stream(
    query: str = Body(..., description="自然语言问题"),
    ku_ids: List[str] = Body(..., description="选中的 ku_id 列表"),
    top_k: int = Body(5),
    model: Optional[str] = Body(None, description="LLM 模型名(可选;空时走默认级联)"),
    embed_model: Optional[str] = Body(None, description="兼容旧客户端字段；文档 KB 查询会忽略，始终使用 KB 配置"),
    use_hybrid: Optional[bool] = Body(None, description="按请求覆盖 hybrid 检索;None=用全局默认"),
    use_rerank: Optional[bool] = Body(None, description="按请求覆盖 cross-encoder rerank;None=用全局默认"),
    rewrite_strategy: Optional[str] = Body(
        "auto",
        description="query 改写策略:auto(默认) / passthrough / rewrite / multi_query / hyde / decompose / off",
    ),
    structured_scopes: Optional[Dict[str, List[str]]] = Body(
        None,
        description="结构化库查询范围:{ku_id: [table_or_collection,...]};空表示整个结构化库",
    ),
    vector_scopes: Optional[Dict[str, List[str]]] = Body(
        None,
        description="向量库查询范围:{ku_id: [collection,...]};空表示整个向量库",
    ),
    user=Depends(require_auth_enabled()),
):
    """SSE 流式:每个 ku_id 在线程池里并发跑,完成一个就推一个 block。

    Events:
      - meta:  {"query": str, "ku_ids": [...], "total": int}
      - block: AskBlock(单 ku 完成时;含 ok/error)
      - done:  {}(全部 ku 处理完)

    UE:配合前端逐项替换骨架卡;真"流式"取代之前的"假流式"。
    """
    if not query.strip():
        raise HTTPException(400, "query 不能为空")
    if not ku_ids:
        raise HTTPException(400, "ku_ids 不能为空")
    denied = [kid for kid in ku_ids if not can_read_ku(user, kid)]
    if denied:
        raise HTTPException(403, f"no read permission on ku_id(s): {', '.join(denied[:5])}")

    async def event_gen():
        # 1) meta
        yield {
            "event": "meta",
            "data": json.dumps(
                {"query": query, "ku_ids": ku_ids, "total": len(ku_ids)},
                ensure_ascii=False,
            ),
        }
        from chayuan.server.retrieval.universe import search_ku_blocks

        processor_kwargs = {
            "model": model,
            "embed_model": embed_model,
            "structured_scopes": structured_scopes,
            "vector_scopes": vector_scopes,
        }
        # 2) 并发投递所有 ku_id;完成顺序不保证
        tasks = [
            asyncio.create_task(search_ku_blocks(
                [(kid, query, "query", [])],
                top_k=top_k,
                use_hybrid=use_hybrid,
                use_rerank=use_rerank,
                rewrite_strategy=rewrite_strategy or "auto",
                processor_kwargs=processor_kwargs,
            ))
            for kid in ku_ids
        ]
        # 收集所有 block,综合总结时复用
        completed_blocks: List[Dict[str, Any]] = []
        try:
            for fut in asyncio.as_completed(tasks):
                try:
                    blocks, _errors = await fut
                    block = blocks[0] if blocks else {"ku_id": "?", "ok": False, "error": "empty result"}
                    block.pop("_query_tag", None)
                    block.pop("_section_ids", None)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    # _process_one_ku 自带 try/except 不应到此;兜底
                    block = {"ku_id": "?", "ok": False, "error": f"unexpected: {e}"}
                completed_blocks.append(block)
                yield {"event": "block", "data": json.dumps(block, ensure_ascii=False)}
        except asyncio.CancelledError:
            # 客户端断流;取消未完成的 task
            for t in tasks:
                if not t.done():
                    t.cancel()
            logger.info("universe.ask stream cancelled by client")
            return

        # 3) summary — 把所有 block 一起喂给 LLM 综合写一段答案 + [N] 引用。
        #    这是 deepseek/千问/perplexity 那种 "搜索 → 总结 → 来源折叠" 模式的核心一步。
        #    与 /api/v1/kb-query/search 共用 retrieval/query/synthesis.py 模块,
        #    避免两条路径各自造轮子导致行为漂移。
        try:
            yield {"event": "summary_start", "data": "{}"}
            from chayuan.server.retrieval.query.synthesis import (
                build_evidence_from_universe_blocks,
                synthesize_answer,
            )
            evidence = build_evidence_from_universe_blocks(completed_blocks)
            answer = await asyncio.to_thread(
                synthesize_answer, query, evidence, model_name=model
            )
            yield {
                "event": "summary",
                "data": json.dumps(
                    {
                        "text": answer,
                        "model": model or "",
                        "evidence_count": len(evidence),
                        "ok": bool(answer),
                    },
                    ensure_ascii=False,
                ),
            }
        except Exception as e:  # noqa: BLE001
            logger.info("universe.ask synthesize summary failed: %r", e)
            yield {
                "event": "summary",
                "data": json.dumps({"text": "", "ok": False, "error": str(e)}, ensure_ascii=False),
            }

        # 4) done
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_gen())
