from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger("chayuan.api.kb")

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile

from chayuan.settings import Settings
from chayuan.server.api_server.api_schemas import OpenAIChatInput, OpenAIChatOutput
from chayuan.server.auth.access import (
    can_read_kb,
    can_write_kb,
    is_kb_owner,
    list_accessible_kbs,
    set_kb_owner_if_missing,
)
from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.chat.file_chat import upload_temp_docs
from chayuan.server.chat.kb_chat import kb_chat
from chayuan.server.knowledge_base.kb_api import create_kb, delete_kb, list_kbs
from chayuan.server.knowledge_base.kb_doc_api import (
    delete_docs,
    download_doc,
    list_files,
    recreate_vector_store,
    search_docs,
    update_docs,
    update_info,
    upload_docs,
    search_temp_docs,
)
from chayuan.server.knowledge_base.kb_summary_api import (
    recreate_summary_vector_store,
    summary_doc_ids_to_vector_store,
    summary_file_to_vector_store,
)
from chayuan.server.utils import BaseResponse, ListResponse, get_default_llm
from chayuan.server.knowledge_base.kb_cache.faiss_cache import memo_faiss_pool


kb_router = APIRouter(prefix="/knowledge_base", tags=["Knowledge Base Management"])


# ---------------------------------------------------------------------------
# 向量库后端不可达 → 友好 503
# ---------------------------------------------------------------------------
#
# KBServiceFactory 的 ___init___ 会立刻连真实的 vector store(milvus/pg/es/...);
# 如果该后端没起,会抛出底层 SDK 的异常(如 pymilvus
# ConnectionNotExistException、psycopg.OperationalError、es ConnectionError 等),
# 透传出去就是 500 + 满屏 traceback。
#
# 用一个白名单 + 内容启发的探测把它们统一收成 503,前端在 toast 里能看到
# "Milvus 不可达,请检查服务" 这种可读文案,而不是空白错误。
#
# 不在 try/except 里硬 import pymilvus / psycopg —— 那些是可选依赖,部署时
# 不一定都装。改用类名匹配 + str(e) 关键词扫描。

_VS_ERR_CLASS_NAMES = {
    # pymilvus
    "ConnectionNotExistException",
    "MilvusException",
    "MilvusUnavailableException",
    # psycopg / sqlalchemy / pgvector
    "OperationalError",
    "DBAPIError",
    "InterfaceError",
    # elasticsearch
    "ConnectionError",
    "ConnectionTimeout",
    "TransportError",
    # chromadb
    "ChromaDBError",
}

_VS_ERR_KEYWORDS = (
    "milvus", "pgvector", "elasticsearch", "elastic search",
    "chroma", "weaviate", "qdrant", "should create connection first",
    "could not connect", "connection refused", "connection timed out",
)


def _vs_unavailable_message(e: BaseException) -> Optional[str]:
    """匹配则返回友好的中文 503 文案;否则 None 让原异常继续上抛。

    匹配条件(任一):
      1) 异常类名在白名单内
      2) str(e) 含关键字(milvus / pgvector / 'connection refused' 等)
    """
    cls = type(e).__name__
    text = str(e or "").lower()
    hit = cls in _VS_ERR_CLASS_NAMES or any(k in text for k in _VS_ERR_KEYWORDS)
    if not hit:
        return None
    return (
        f"向量库后端不可达 ({cls}: {e})。"
        f"请检查 Milvus / PG / ES / Chroma 等服务是否就绪;"
        f"或在创建知识库时改用 faiss / chromadb 等本地后端。"
    )


def _kb_call(fn, *args, **kwargs):
    """所有触达 KBService 的端点统一用这个包一层。
    捕获 vector store 不可达类异常,转 503 + 友好文案;其它异常原样上抛。
    """
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        msg = _vs_unavailable_message(e)
        if msg:
            logger.warning("kb endpoint blocked by vs backend: %s", msg)
            raise HTTPException(status_code=503, detail=msg) from e
        raise


# ---------------------------------------------------------------------------
# 小工具：从入参里拿 kb_name 做访问检查
# ---------------------------------------------------------------------------

def _require_read(user, kb_name: str) -> None:
    # AUTH_REQUIRED=false + 未登录：依然放行（user=None），维持单机调试体验
    if user is None:
        return
    if not can_read_kb(user, kb_name):
        raise HTTPException(status_code=403, detail=f"no read permission on kb {kb_name!r}")


def _require_write(user, kb_name: str) -> None:
    if user is None:
        return
    if not can_write_kb(user, kb_name):
        raise HTTPException(status_code=403, detail=f"no write permission on kb {kb_name!r}")


def _require_owner(user, kb_name: str) -> None:
    if user is None:
        return
    if not is_kb_owner(user, kb_name):
        # admin 也能删（is_kb_owner 已放行 admin）
        raise HTTPException(status_code=403, detail=f"owner only on kb {kb_name!r}")


def _is_universe_target(name: str) -> bool:
    return isinstance(name, str) and name.startswith(("doc:", "src:"))


def _doc_target_name(name: str) -> str:
    if isinstance(name, str) and name.startswith("doc:"):
        return name.split(":", 1)[1]
    return name


def _expand_collection_targets(names: List[str]) -> List[str]:
    """94-6:把 ``coll:<name>`` / ``coll:<id>`` 展开成它的成员 ku_id 列表。

    其它前缀(``doc:`` / ``src:`` / 无前缀)原样透传。
    展开失败(集合不存在)的项跳过。

    这是 WPS 加载项透明适配层 — WPS 端不需要任何改动,把集合当成 KB 名传过来即可。
    """
    if not names:
        return []
    out: List[str] = []
    for raw in names:
        s = str(raw or "").strip()
        if not s:
            continue
        if s.startswith("coll:"):
            ident = s[5:].strip()
            if not ident:
                continue
            try:
                from chayuan.server.db.repository import (
                    kb_collection_repository as _coll_repo,
                )
                coll = None
                # 数字 → 按 id 查;字符串 → 按 name 查
                if ident.isdigit():
                    coll = _coll_repo.get_collection(int(ident))
                else:
                    coll = _coll_repo.get_collection_by_name(ident)
                    if coll:
                        coll = _coll_repo.get_collection(coll["id"])
                if not coll:
                    continue
                for m in coll.get("members") or []:
                    ku = m.get("ku_id")
                    kind = m.get("kind")
                    if not ku:
                        continue
                    # 转成 _split_search_targets 能识别的前缀
                    if kind == "document":
                        out.append(f"doc:{ku}")
                    elif kind == "image":
                        # image source 的 ku_id 已经是 src:<id> 形式
                        out.append(ku if ku.startswith("src:") else f"src:{ku}")
                    else:
                        out.append(ku)
            except Exception:  # noqa: BLE001
                continue
        else:
            out.append(s)
    return out


def _split_search_targets(names: List[str]) -> tuple[List[str], List[str]]:
    # 94-6:先展开 coll:*,再分发到 doc / ku_ids
    names = _expand_collection_targets(names)
    doc_names: List[str] = []
    ku_ids: List[str] = []
    for raw in names or []:
        name = str(raw or "").strip()
        if not name:
            continue
        if name.startswith("doc:"):
            doc_names.append(_doc_target_name(name))
        elif name.startswith("src:"):
            ku_ids.append(name)
        else:
            doc_names.append(name)
    return list(dict.fromkeys(doc_names)), list(dict.fromkeys(ku_ids))


def _legacy_kb_item_from_universe(item: Dict[str, Any]) -> Dict[str, Any]:
    ku_id = str(item.get("ku_id") or "")
    kind = str(item.get("kind") or "document")
    if kind == "document":
        name = str(item.get("name") or "")
        return {
            "kb_name": name,
            "kb_info": item.get("description") or "",
            "vs_type": item.get("vs_type") or "",
            "embed_model": item.get("embed_model") or "",
            "file_count": int(item.get("count") or 0),
            "owner_id": item.get("owner_id"),
            "visibility": item.get("visibility") or "private",
            "create_time": item.get("create_time"),
            "ku_id": ku_id,
            "kind": "document",
            "display_name": item.get("display_name") or name,
            "role": item.get("access_role") or "",
            "can_write": bool(item.get("can_write")),
        }
    display = item.get("display_name") or item.get("name") or ku_id
    return {
        # 兼容旧客户端字段名：选择和搜索时用 ku_id 作为目标名称。
        "kb_name": ku_id,
        "kb_info": item.get("description") or "",
        "vs_type": kind,
        "embed_model": "",
        "file_count": int(item.get("count") or 0) if item.get("count") is not None else 0,
        "owner_id": item.get("owner_id"),
        "visibility": item.get("visibility") or "private",
        "create_time": item.get("create_time"),
        "ku_id": ku_id,
        "kind": kind,
        "sub_kind": item.get("sub_kind") or "",
        "display_name": display,
        "role": item.get("access_role") or "",
        "can_write": bool(item.get("can_write")),
    }


def _kb_list_with_universe(user: Any, *, include_vector: bool = True) -> ListResponse:
    from chayuan.server.api_server.knowledge_universe_routes import list_universe_items

    return ListResponse(data=[
        _legacy_kb_item_from_universe(item)
        for item in list_universe_items(include_vector=include_vector, user=user)
    ])


def _is_admin(user) -> bool:
    if user is None:
        return False
    if isinstance(user, dict):
        return user.get("role") == "admin"
    return getattr(user, "role", "") == "admin"


def _require_admin(user) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="admin only")


def _available_embedding_models(*, check_access: bool = False) -> List[Dict[str, Any]]:
    """Return configured embedding models, optionally probing runtime access.

    Admin UI should only offer models from this list. ``check_access=True`` is
    intentionally opt-in because probing every provider can be slow.
    """
    from chayuan.server.utils import check_embed_model, get_config_models

    models_map = get_config_models(model_type="embed") or {}
    rows: List[Dict[str, Any]] = []
    for model_name, info in sorted(
        models_map.items(),
        key=lambda item: (str(item[1].get("platform_name") or ""), item[0]),
    ):
        available = True
        reason = ""
        if check_access:
            available, reason = check_embed_model(model_name)
        rows.append(
            {
                "id": model_name,
                "model_name": model_name,
                "platform_name": info.get("platform_name") or "",
                "platform_type": info.get("platform_type") or "",
                "model_type": "embed",
                "available": bool(available),
                "reason": reason,
            }
        )
    return rows


def _assert_embedding_model_selectable(embed_model: str, *, check_access: bool = True) -> None:
    from chayuan.server.utils import check_embed_model, get_config_models

    model = (embed_model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="missing embed_model")
    if model not in (get_config_models(model_type="embed") or {}):
        raise HTTPException(status_code=400, detail=f"embedding model {model!r} is not configured or enabled")
    if check_access:
        ok, msg = check_embed_model(model)
        if not ok:
            raise HTTPException(status_code=400, detail=msg or f"embedding model {model!r} is unavailable")


def _chunk_to_wire(idx: int, doc: Any) -> Dict[str, Any]:
    metadata = dict(getattr(doc, "metadata", None) or {})
    return {
        "chunk_index": idx,
        "chunk_id": str(getattr(doc, "id", "") or metadata.get("id") or ""),
        "text": getattr(doc, "page_content", "") or "",
        "metadata": metadata,
    }


def _normalize_chunk_payload(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for idx, raw in enumerate(chunks or []):
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail=f"chunk #{idx + 1} must be an object")
        text = str(raw.get("text") or raw.get("page_content") or "").strip()
        if not text:
            continue
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        normalized.append(
            {
                "chunk_id": str(raw.get("chunk_id") or raw.get("id") or ""),
                "text": text,
                "metadata": dict(metadata),
            }
        )
    if not normalized:
        raise HTTPException(status_code=400, detail="chunks 不能为空")
    return normalized


def _list_file_chunks_from_source(
    knowledge_base_name: str,
    file_name: str,
) -> Dict[str, Any]:
    """按源文件重新切片,不实例化 KBService,也不访问向量库。

    这个接口用于前端"查看切片"预览。旧实现通过 kb.list_docs() 读取向量库里的
    chunk 内容,会在 Milvus / PGVector 不可达时失败;预览切片本身只需要源文件和
    splitter 参数,因此这里走纯文件路径。
    """
    import os

    from chayuan.server.db.repository.knowledge_base_repository import load_kb_from_db
    from chayuan.server.db.repository.knowledge_file_repository import get_file_detail
    from chayuan.server.knowledge_base.utils import KnowledgeFile, get_file_path, list_files_from_folder

    kb_name, _, _ = load_kb_from_db(knowledge_base_name)
    if not kb_name:
        raise HTTPException(status_code=404, detail=f"未找到知识库 {knowledge_base_name}")

    # DB 里没有文件元数据时,仍允许基于源文件查看切片。常见于:
    # 1) 文件已上传但还未完成向量化;2) 入库失败;3) 历史中文文件名大小写/编码匹配差异。
    file_detail = get_file_detail(kb_name=kb_name, filename=file_name)
    resolved_file_name = file_detail.get("file_name") or file_name
    local_path = get_file_path(kb_name, resolved_file_name)

    if not file_detail and (not local_path or not os.path.exists(local_path)):
        try:
            for candidate in list_files_from_folder(kb_name):
                if candidate == file_name or candidate.casefold() == file_name.casefold():
                    resolved_file_name = candidate
                    local_path = get_file_path(kb_name, resolved_file_name)
                    break
        except Exception as e:  # noqa: BLE001
            logger.debug("list_files_from_folder failed(kb=%s): %r", kb_name, e)

    if (not local_path or not os.path.exists(local_path)):
        try:
            from chayuan.server.file_storage import NS, get_storage
            storage = get_storage()
            key = f"{kb_name}/{resolved_file_name}"
            if storage.exists(NS.KB_CONTENT, key):
                if local_path:
                    storage.copy_to_local(NS.KB_CONTENT, key, local_path)
        except Exception as e:  # noqa: BLE001
            logger.debug("file_chunks storage fallback failed(kb=%s,file=%s): %r", kb_name, resolved_file_name, e)

    if not local_path or not os.path.exists(local_path):
        raise HTTPException(status_code=404, detail=f"未找到文件 {file_name}")

    kb_file = KnowledgeFile(filename=resolved_file_name, knowledge_base_name=kb_name)
    splitter_name = file_detail.get("text_splitter")
    if splitter_name:
        kb_file.text_splitter_name = str(splitter_name)

    docs = kb_file.file2text(
        chunk_size=Settings.kb_settings.CHUNK_SIZE,
        chunk_overlap=Settings.kb_settings.OVERLAP_SIZE,
        zh_title_enhance=Settings.kb_settings.ZH_TITLE_ENHANCE,
    )
    chunks = [_chunk_to_wire(idx, doc) for idx, doc in enumerate(docs or [])]
    return {
        "knowledge_base_name": kb_name,
        "file_name": resolved_file_name,
        "chunk_count": len(chunks),
        "custom_docs": bool(file_detail.get("custom_docs")),
        "source": "source_file",
        "chunks": chunks,
    }


def _list_file_chunks_from_vector_store(
    knowledge_base_name: str,
    file_name: str,
) -> Dict[str, Any]:
    """源文件无法重新切片时,回退读取已入库向量切片。"""
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory

    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"未找到知识库 {knowledge_base_name}")

    docs = kb.list_docs(file_name=file_name) or []
    if not docs:
        # 文件夹上传或历史数据可能存在路径归一差异,尝试用 basename 再兜一次。
        import os

        base = os.path.basename(file_name)
        if base and base != file_name:
            docs = kb.list_docs(file_name=base) or []
    chunks = [_chunk_to_wire(idx, doc) for idx, doc in enumerate(docs or [])]
    return {
        "knowledge_base_name": knowledge_base_name,
        "file_name": file_name,
        "chunk_count": len(chunks),
        "custom_docs": False,
        "source": "vector_store",
        "chunks": chunks,
    }


# ---------------------------------------------------------------------------
# KB 对话（openai 兼容）
# ---------------------------------------------------------------------------

@kb_router.post(
    "/{mode}/{param}/chat/completions",
    summary="知识库对话，openai 兼容，参数与 /chat/kb_chat 一致",
)
async def kb_chat_endpoint(
    mode: Literal["local_kb", "temp_kb", "search_engine"],
    param: str,
    body: OpenAIChatInput,
    request: Request,
    user=Depends(require_auth_enabled()),
):
    if mode == "local_kb":
        _require_read(user, param)

    if body.max_tokens in [None, 0]:
        body.max_tokens = Settings.model_settings.MAX_TOKENS

    extra = body.model_extra
    model_name = (body.model or "").strip() or get_default_llm()

    # ---- 语义缓存：仅本地 KB 的非流式查询命中 ----
    from chayuan.server.resilience import semcache_get, semcache_set
    uid = (user or {}).get("id") if isinstance(user, dict) else None
    last_query = ""
    try:
        last_query = body.messages[-1].get("content") or "" if body.messages else ""
    except Exception:
        last_query = ""
    cache_scope = {
        "route": "kb_chat",
        "mode": mode,
        "top_k": extra.get("top_k", Settings.kb_settings.VECTOR_SEARCH_TOP_K),
        "score_threshold": extra.get("score_threshold", Settings.kb_settings.SCORE_THRESHOLD),
        "temperature": body.temperature,
    }
    if mode == "local_kb" and not body.stream and last_query and len(body.messages or []) <= 2:
        hit = semcache_get(
            query=last_query, user_id=uid, kb=param, model=model_name, extra=cache_scope,
        )
        if hit is not None:
            return hit

    ret = await kb_chat(
        query=body.messages[-1]["content"],
        mode=mode,
        kb_name=param,
        top_k=extra.get("top_k", Settings.kb_settings.VECTOR_SEARCH_TOP_K),
        score_threshold=extra.get("score_threshold", Settings.kb_settings.SCORE_THRESHOLD),
        history=body.messages[:-1],
        stream=body.stream,
        model=model_name,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        prompt_name=extra.get("prompt_name", "default"),
        return_direct=extra.get("return_direct", False),
        request=request,
    )
    if mode == "local_kb" and not body.stream and last_query and isinstance(ret, dict):
        try:
            if ret.get("choices"):
                semcache_set(
                    query=last_query, response=ret, user_id=uid, kb=param,
                    model=model_name, extra=cache_scope,
                )
        except Exception:
            pass
    return ret


# ---------------------------------------------------------------------------
# KB CRUD （包装原函数，加鉴权 + ACL）
# ---------------------------------------------------------------------------

@kb_router.get(
    "/list_knowledge_bases", response_model=ListResponse, summary="获取知识库列表",
)
def list_kbs_endpoint(
    include_universe: bool = Query(
        True,
        description="是否返回与前端智库列表一致的全集：文档 / 结构化 / 图像 / 向量。",
    ),
    include_vector: bool = Query(
        True,
        description="include_universe=true 时是否包含外部向量类智库。",
    ),
    user=Depends(require_auth_enabled()),
):
    """按用户可见性过滤 KB 列表。

    - 未登录（AUTH_REQUIRED=false 场景）：返回全量，维持旧行为；
    - admin：返回全量；
    - 普通用户：仅返回「owner / 被授权 / public」三类的并集。
    - 同时原样保留 `KnowledgeBaseSchema` 里的 `owner_id / visibility`，前端可直接展示。
    - 默认与 `/knowledge_universe/list` 对齐，额外包含结构化 / 图像 / 向量类智库；
      非文档类条目的 `kb_name` 为 `src:<id>`，搜索时直接传回即可。
    """
    if include_universe:
        return _kb_list_with_universe(user, include_vector=include_vector)

    result = list_kbs()

    def _name_of(x) -> str:
        if isinstance(x, str):
            return x
        if isinstance(x, dict):
            return str(x.get("kb_name", ""))
        return str(getattr(x, "kb_name", ""))

    if user is None:
        return result
    role = user.get("role") if isinstance(user, dict) else None
    if role == "admin":
        return result

    try:
        accessible = set(list_accessible_kbs(user))
    except Exception:
        accessible = set()

    if hasattr(result, "data") and isinstance(result.data, list):
        result.data = [x for x in result.data if _name_of(x) in accessible]
    return result


@kb_router.post(
    "/create_knowledge_base", response_model=BaseResponse, summary="创建知识库",
)
def create_kb_endpoint(
    knowledge_base_name: str = Body(..., examples=["samples"]),
    vector_store_type: str = Body(Settings.kb_settings.DEFAULT_VS_TYPE),
    kb_info: str = Body("", description="知识库简介，用于Agent"),
    embed_model: str = Body(None, description="embedding 模型；留空用默认"),
    visibility: str = Body("private", description="private / public"),
    storage_backend: str = Body(
        "", description="按 KB 存储后端：''=跟随全局 / 'local' / 'minio'"
    ),
    user=Depends(require_auth_enabled()),
):
    # 未登录且 AUTH_REQUIRED=true 的情况下，require_auth_enabled 会直接 401
    from chayuan.server.utils import get_default_embedding
    if not embed_model:
        embed_model = get_default_embedding()
    try:
        result = create_kb(
            knowledge_base_name=knowledge_base_name,
            vector_store_type=vector_store_type,
            kb_info=kb_info,
            embed_model=embed_model,
        )
    except Exception as e:  # noqa: BLE001
        # vector store 不通(milvus 没起 / pg 连不上 / es 不可达 等)→ 返 503 + 友好原因,
        # 不要透出 500 让前端只能看到通用 "Internal Server Error"
        msg = str(e) or f"{type(e).__name__}"
        logger.exception("create_kb failed: %s", msg)
        # detail 用字符串,前端 client.ts 直接拿来做 toast 文案
        raise HTTPException(
            status_code=503,
            detail=(
                f"创建知识库失败({vector_store_type}):{msg}。"
                f"提示:若所选向量库服务未就绪,可改用 faiss / chromadb 本地选项。"
            ),
        )
    code = getattr(result, "code", None)
    if code in (200, 0, None) and user is not None:
        try:
            set_kb_owner_if_missing(knowledge_base_name, user.get("id"))
            from chayuan.server.db.session import session_scope
            from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
            with session_scope() as s:
                kb = s.query(KnowledgeBaseModel).filter(
                    KnowledgeBaseModel.kb_name == knowledge_base_name
                ).one_or_none()
                if kb and visibility in ("private", "public"):
                    kb.visibility = visibility
                if kb and storage_backend and storage_backend in ("local", "minio"):
                    kb.storage_backend = storage_backend
        except Exception:  # noqa: BLE001
            pass
    return result


@kb_router.get(
    "/storage_backend", summary="查询某 KB 的存储后端 + 全局默认 + 可选值",
)
def get_kb_storage_backend_endpoint(
    knowledge_base_name: str,
    user=Depends(require_auth_enabled()),
):
    """返回 ``{ kb, resolved, override, global, available: [...] }``。
    - ``override``：KB 记录里的 ``storage_backend``（空=跟随全局）
    - ``resolved``：当前实际生效的后端
    - ``global``：全局默认
    - ``available``：根据运行时依赖/配置可用的后端列表
    """
    _require_read(user, knowledge_base_name)
    from chayuan.server.db.repository.knowledge_base_repository import (
        get_kb_storage_backend,
    )
    from chayuan.server.file_storage import get_storage, get_storage_for_kb
    try:
        override = (get_kb_storage_backend(knowledge_base_name) or "").strip().lower()
    except Exception:
        override = ""
    try:
        global_backend = str(
            getattr(Settings.basic_settings, "FILE_STORAGE_BACKEND", "local") or "local"
        ).strip().lower()
    except Exception:
        global_backend = "local"
    try:
        resolved_info = get_storage_for_kb(knowledge_base_name).backend_info()
    except Exception as e:  # noqa: BLE001
        resolved_info = {"type": "unknown", "error": f"{type(e).__name__}: {e}"}
    try:
        global_info = get_storage().backend_info()
    except Exception:
        global_info = {"type": global_backend}
    # available：local 恒有；minio 看 MINIO_ENDPOINT 是否填了 + minio 包是否可用
    available = ["local"]
    try:
        endpoint = str(getattr(Settings.basic_settings, "MINIO_ENDPOINT", "") or "")
        if endpoint:
            import importlib
            try:
                importlib.import_module("minio")
                available.append("minio")
            except Exception:
                pass
    except Exception:
        pass
    return {
        "code": 0,
        "data": {
            "kb": knowledge_base_name,
            "override": override,
            "global": global_backend,
            "global_info": global_info,
            "resolved": resolved_info.get("type", "unknown"),
            "resolved_info": resolved_info,
            "available": available,
        },
    }


@kb_router.post(
    "/update_storage_backend", response_model=BaseResponse,
    summary="修改某 KB 的存储后端（owner 或 admin）",
)
def update_kb_storage_backend_endpoint(
    knowledge_base_name: str = Body(..., embed=True),
    storage_backend: str = Body(
        "", description="''=跟随全局 / 'local' / 'minio'"
    ),
    migrate: bool = Body(
        False, description="是否把 kb_content 命名空间里属于本 KB 的对象迁到新后端"
    ),
    dry_run: bool = Body(False, description="migrate=True 时可先 dry run 看影响面"),
    user=Depends(require_auth_enabled()),
):
    """三个语义：
    1. 只改记录：migrate=False → 旧数据留在原后端（读老文件仍能走，新写入落新后端）。
    2. 改 + 迁：migrate=True → 把旧后端里该 KB 的对象逐个 put 到新后端，成功后可选清理。
    3. 可先 dry_run=True 看看会迁多少个对象、多少体积。
    """
    _require_owner(user, knowledge_base_name)
    backend = (storage_backend or "").strip().lower()
    if backend and backend not in ("local", "minio"):
        return BaseResponse(code=400, msg="storage_backend 只能是 local / minio / 空")

    from chayuan.server.db.repository.knowledge_base_repository import (
        get_kb_storage_backend, set_kb_storage_backend,
    )
    from chayuan.server.file_storage import (
        NS, get_storage, get_storage_for_kb, reset_cache,
    )

    old_backend = (get_kb_storage_backend(knowledge_base_name) or "").strip().lower()
    old_storage = get_storage_for_kb(knowledge_base_name)

    # 迁移过程：务必在写 DB 之前列清单（否则 get_storage_for_kb 一改就指向新后端）
    prefix = f"{knowledge_base_name}/"
    migrated: list = []
    errors: list = []
    total_bytes = 0
    if migrate:
        try:
            items = old_storage.list(NS.KB_CONTENT, prefix=prefix, limit=10000)
        except Exception as e:  # noqa: BLE001
            return BaseResponse(code=500, msg=f"列旧后端对象失败：{e}")
        total_bytes = sum(int(o.size or 0) for o in items)
        if dry_run:
            return BaseResponse(code=0, msg="ok", data={
                "dry_run": True, "count": len(items),
                "bytes": total_bytes, "old": old_backend or "(global)",
                "new": backend or "(global)",
                "preview": [o.key for o in items[:20]],
            })

    # 真实切换：先写 DB，再 reset 缓存
    ok = set_kb_storage_backend(knowledge_base_name, backend)
    if not ok:
        return BaseResponse(code=404, msg=f"未找到 KB {knowledge_base_name}")
    reset_cache()

    if migrate:
        new_storage = get_storage_for_kb(knowledge_base_name)
        try:
            items = old_storage.list(NS.KB_CONTENT, prefix=prefix, limit=10000)
        except Exception as e:  # noqa: BLE001
            return BaseResponse(
                code=500,
                msg=f"新后端已生效，但列旧对象迁移失败：{e}；可在 /storage/migrate 重试",
            )
        for obj in items:
            try:
                data = old_storage.get(NS.KB_CONTENT, obj.key)
                new_storage.put(NS.KB_CONTENT, obj.key, data,
                                  content_type=obj.content_type or "")
                migrated.append(obj.key)
            except Exception as e:  # noqa: BLE001
                errors.append({"key": obj.key, "error": f"{type(e).__name__}: {e}"})

    return BaseResponse(code=0, msg="ok", data={
        "kb": knowledge_base_name,
        "old": old_backend or "(global)",
        "new": backend or "(global)",
        "migrated_count": len(migrated),
        "errors_count": len(errors),
        "errors_preview": errors[:5],
    })


@kb_router.post(
    "/delete_knowledge_base", response_model=BaseResponse, summary="删除知识库",
)
def delete_kb_endpoint(
    knowledge_base_name: str = Body(..., embed=True, examples=["samples"]),
    user=Depends(require_auth_enabled()),
):
    _require_owner(user, knowledge_base_name)
    return _kb_call(delete_kb, knowledge_base_name=knowledge_base_name)


@kb_router.get(
    "/preview-config",
    summary="文件预览配置 — 返回 kkFileView 旁车地址(空字符串 = 未配置)",
)
def get_preview_config():
    """前端预览面板调用:有 kkfileview_url 就走 kkFileView 旁车,
    覆盖 Office/WPS/3D/CAD/视频转码等 100+ 格式;空就走内置 renderer。

    public 端点 — 不返回敏感信息,无须鉴权,以便预览面板首屏即可决定路由。
    """
    return {
        "kkfileview_url": (Settings.kb_settings.KKFILEVIEW_URL or "").strip(),
    }


@kb_router.get(
    "/list_files", response_model=ListResponse, summary="获取知识库内的文件列表",
)
def list_files_endpoint(
    knowledge_base_name: str,
    user=Depends(require_auth_enabled()),
):
    _require_read(user, knowledge_base_name)
    return _kb_call(list_files, knowledge_base_name)


@kb_router.post("/search_docs", response_model=List[dict], summary="搜索知识库")
def search_docs_endpoint(
    query: str = Body("", description="用户输入", examples=["你好"]),
    knowledge_base_name: str = Body(..., description="知识库名称"),
    top_k: int = Body(Settings.kb_settings.VECTOR_SEARCH_TOP_K),
    score_threshold: float = Body(Settings.kb_settings.SCORE_THRESHOLD),
    file_name: str = Body(""),
    metadata: dict = Body({}),
    use_hybrid: Optional[bool] = Body(None, description="按请求覆盖 hybrid 开关;None=用全局默认"),
    use_rerank: Optional[bool] = Body(None, description="按请求覆盖 rerank 开关;None=用全局默认"),
    user=Depends(require_auth_enabled()),
):
    _require_read(user, knowledge_base_name)
    return _kb_call(
        search_docs,
        query=query,
        knowledge_base_name=knowledge_base_name,
        top_k=top_k,
        score_threshold=score_threshold,
        file_name=file_name,
        metadata=metadata,
        use_hybrid=use_hybrid,
        use_rerank=use_rerank,
    )


def _require_read_search_target(user: Any, target: str) -> None:
    if user is None:
        return
    if target.startswith(("src:", "doc:")):
        from chayuan.server.api_server.knowledge_universe_routes import can_read_ku
        if not can_read_ku(user, target):
            raise HTTPException(status_code=403, detail=f"no read permission on ku_id {target!r}")
        return
    if not can_read_kb(user, target):
        raise HTTPException(status_code=403, detail=f"no read permission on kb {target!r}")


def _text_from_universe_hit(hit: Dict[str, Any]) -> str:
    for key in ("content", "page_content", "text", "summary", "snippet", "caption", "title"):
        val = hit.get(key)
        if val not in (None, ""):
            return str(val)
    if hit.get("rows"):
        return str(hit.get("rows"))
    return ""


def _universe_block_to_chunks(block: Dict[str, Any], *, tag: str, section_ids: List[str]) -> List[Dict[str, Any]]:
    from chayuan.server.retrieval.universe import block_to_chunks

    return block_to_chunks(block, tag=tag, section_ids=section_ids)


async def _run_universe_targets_for_batch(body: Any, ku_ids: List[str], user: Any):
    from chayuan.server.retrieval.universe import search_ku_chunks

    top_k = int(getattr(body, "per_query_top_k", 6) or 6)
    use_hybrid = getattr(body, "use_hybrid", None)
    use_rerank = getattr(body, "use_rerank", None)
    targets = []
    for ku_id in ku_ids:
        for q in body.queries:
            targets.append((ku_id, q.text, q.tag, list(q.section_ids or [])))
    chunks, errors, _blocks = await search_ku_chunks(
        targets,
        top_k=top_k,
        use_hybrid=use_hybrid,
        use_rerank=use_rerank,
        rewrite_strategy=getattr(body, "rewrite_strategy", "passthrough") or "passthrough",
    )
    chunks.sort(key=lambda c: float(c.get("score_fused") or 0.0), reverse=True)
    return chunks[: int(getattr(body, "final_top_k", 12) or 12)], errors


async def _run_mixed_search_batch(body: Any, *, user: Any, subject_for_token: Any):
    from chayuan.server.file_rag import batch_search as _bs

    doc_names, ku_ids = _split_search_targets(body.knowledge_base_names)
    merged: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    if doc_names:
        doc_body = body.model_copy(update={"knowledge_base_names": doc_names})
        doc_out = await _bs.run_batch(doc_body, user=user, subject_for_token=subject_for_token)
        merged.extend(list(doc_out.merged or []))
        errors.extend(list(doc_out.errors or []))
    if ku_ids:
        ku_chunks, ku_errors = await _run_universe_targets_for_batch(body, ku_ids, user)
        merged.extend(ku_chunks)
        errors.extend(ku_errors)
    merged.sort(key=lambda c: float(c.get("score_fused") or c.get("score_raw") or 0.0), reverse=True)
    final_top_k = int(getattr(body, "final_top_k", 12) or 12)
    merged = merged[:final_top_k]
    return _bs.SearchBatchOut(
        merged=merged,
        summary=_bs.SearchBatchSummary(
            queries=len(body.queries),
            knowledge_bases=len(doc_names) + len(ku_ids),
            candidates_before_rerank=len(merged),
            chunks_returned=len(merged),
            fused_by=body.fusion or "rrf",
            failed_subqueries=len(errors),
        ),
        errors=errors,
    )


@kb_router.post(
    "/search_batch",
    summary="批量子查询并行检索 + RRF 融合 + 来源/标签透传",
    description=(
        "前端「四层漏斗」(详见 plan v1.3 §3.2.5) 切出来的 N 条短查询，一次性下发。\n"
        "服务端为每个 (kb × query) 起子任务并行跑 search_docs，按 RRF 融合，\n"
        "返回带 `from_query_tags / from_section_ids / download_token` 的合并 chunk 列表。\n\n"
        "Hard limits（见 plan §4.1）：queries ≤ 32, sum(text) ≤ 24k, kbs ≤ 8, top_k ≤ 50。\n\n"
        "ACL：每个 kb 都会过 _require_read；无权 KB 直接 403。"
    ),
)
async def search_batch_endpoint(
    request: Request,
    user=Depends(require_auth_enabled()),
):
    from pydantic import ValidationError as _PydErr
    from chayuan.server.file_rag import batch_search as _bs
    from chayuan.server.observability.audit import AuditStopwatch
    from chayuan.server.auth import kb_audit

    rid = request.headers.get("x-request-id", "")
    raw = await request.json()
    try:
        body = _bs.SearchBatchIn(**(raw or {}))
    except _PydErr as e:
        raise HTTPException(status_code=400, detail={"code": 4001, "msg": "invalid params",
                                                     "errors": e.errors()})
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail={"code": 4001, "msg": str(e)})

    # ACL：逐 KB 过；任何一个无权直接 403。
    # 这是显式拒绝（vs 静默过滤）— 客户端如果要"软过滤"应该先调 list_knowledge_bases。
    from chayuan.server.observability import metrics as _metrics
    for kb in body.knowledge_base_names:
        try:
            _require_read_search_target(user, kb)
        except HTTPException:
            kb_audit.denied(user, kb=kb, action="read", code=4031,
                            reason="no read permission", request_id=rid)
            _metrics.record_kb_denied(kb_audit.subject_kind_of(user), 4031, "read")
            raise HTTPException(status_code=403,
                                detail={"code": 4031, "msg": f"no read permission on kb {kb!r}"})

    # subject_for_token = user dict（download_token 内部转字符串 sub）
    with AuditStopwatch() as w:
        try:
            out = await _run_mixed_search_batch(body, user=user, subject_for_token=user)
        except Exception:
            _metrics.record_kb_search(
                kb_audit.subject_kind_of(user), body.fusion or "rrf", "error",
                duration_s=w.elapsed_ms / 1000.0 if hasattr(w, "elapsed_ms") else 0.0,
                sub_queries=len(body.queries), chunks_returned=0,
            )
            raise
    kb_audit.search_ok(user,
                       kbs=body.knowledge_base_names,
                       queries=len(body.queries),
                       chunks=len(out.merged),
                       elapsed_ms=w.elapsed_ms,
                       request_id=rid)
    _metrics.record_kb_search(
        kb_audit.subject_kind_of(user), body.fusion or "rrf", "ok",
        duration_s=w.elapsed_ms / 1000.0,
        sub_queries=len(body.queries), chunks_returned=len(out.merged),
    )
    return out


@kb_router.post(
    "/search_batch_stream",
    summary="批量子查询的 SSE 流式版（plan §4.2 帧格式）",
)
async def search_batch_stream_endpoint(
    request: Request,
    user=Depends(require_auth_enabled()),
):
    from pydantic import ValidationError as _PydErr
    import json as _json
    from fastapi.responses import StreamingResponse
    from chayuan.server.file_rag import batch_search as _bs
    from chayuan.server.auth import kb_audit

    rid = request.headers.get("x-request-id", "")
    raw = await request.json()
    try:
        body = _bs.SearchBatchIn(**(raw or {}))
    except _PydErr as e:
        raise HTTPException(status_code=400, detail={"code": 4001, "msg": "invalid params",
                                                     "errors": e.errors()})
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail={"code": 4001, "msg": str(e)})

    for kb in body.knowledge_base_names:
        try:
            _require_read_search_target(user, kb)
        except HTTPException:
            kb_audit.denied(user, kb=kb, action="read", code=4031,
                            reason="no read permission (stream)", request_id=rid)
            raise HTTPException(status_code=403,
                                detail={"code": 4031, "msg": f"no read permission on kb {kb!r}"})

    async def _event_stream():
        chunk_count = 0
        try:
            doc_names, ku_ids = _split_search_targets(body.knowledge_base_names)
            if ku_ids:
                yield f"data: {_json.dumps({'type': 'embed_done', 'kbs': len(doc_names) + len(ku_ids), 'queries': len(body.queries)}, ensure_ascii=False)}\n\n"
                out = await _run_mixed_search_batch(body, user=user, subject_for_token=user)
                chunk_count = len(out.merged)
                yield f"data: {_json.dumps({'type': 'results', 'merged': out.merged, 'summary': out.summary.model_dump(), 'errors': out.errors}, ensure_ascii=False, default=str)}\n\n"
                yield f"data: {_json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            else:
                async for frame in _bs.run_batch_stream(body, user=user, subject_for_token=user):
                    if frame.get("type") == "results":
                        chunk_count = len(frame.get("merged") or [])
                    yield f"data: {_json.dumps(frame, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {_json.dumps({'type': 'error', 'message': repr(e)}, ensure_ascii=False)}\n\n"
        finally:
            kb_audit.search_ok(user,
                               kbs=body.knowledge_base_names,
                               queries=len(body.queries),
                               chunks=chunk_count,
                               request_id=rid)

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@kb_router.post(
    "/hybrid_search",
    summary="多路混合检索 + RRF 融合 + Rerank(SSE 推送进度)",
)
async def hybrid_search_endpoint(
    request: Request,
    user=Depends(require_auth_enabled()),
):
    """Block B 反馈 16:为对话回答里的"折叠检索进度卡片"提供 SSE 帧。

    Body:
      {
        "query": "...",
        "knowledge_base_name": "...",
        "top_k": 6,
        "score_threshold": 0.3,
        "rerank": true,
        "weights": { "vector": 1.0, "bm25": 0.8, "title": 1.5, "section": 1.2 }
      }

    SSE 帧:plan / hyde? / route_start / route_done / fuse / rerank / summary /
    results / done(详见 hybrid_progress.hybrid_search_with_progress)。
    """
    import asyncio  # noqa: F401  (用于 keep-alive 心跳)
    import json as _json
    from fastapi.responses import StreamingResponse
    from chayuan.server.file_rag.hybrid_progress import hybrid_search_with_progress

    body = await request.json()
    kb_name = (body or {}).get("knowledge_base_name") or ""
    if not kb_name:
        raise HTTPException(status_code=400, detail="missing knowledge_base_name")
    _require_read(user, kb_name)
    query = (body or {}).get("query") or ""
    top_k = int((body or {}).get("top_k") or Settings.kb_settings.VECTOR_SEARCH_TOP_K)
    score_threshold = float((body or {}).get("score_threshold") or Settings.kb_settings.SCORE_THRESHOLD)
    rerank = bool((body or {}).get("rerank", True))
    weights = (body or {}).get("weights") or None

    async def _event_stream():
        try:
            async for frame in hybrid_search_with_progress(
                query=query,
                knowledge_base_name=kb_name,
                top_k=top_k,
                score_threshold=score_threshold,
                rerank=rerank,
                weights=weights,
            ):
                yield f"data: {_json.dumps(frame, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {_json.dumps({'type': 'error', 'message': repr(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@kb_router.post(
    "/upload_docs",
    response_model=BaseResponse,
    summary="上传文件到知识库，并/或进行向量化",
)
def upload_docs_endpoint(
    files: List[UploadFile] = File(..., description="上传文件，支持多文件"),
    knowledge_base_name: str = Form(..., description="知识库名称"),
    override: bool = Form(False, description="覆盖已有文件"),
    to_vector_store: bool = Form(True, description="上传文件后是否进行向量化"),
    chunk_size: int = Form(Settings.kb_settings.CHUNK_SIZE),
    chunk_overlap: int = Form(Settings.kb_settings.OVERLAP_SIZE),
    zh_title_enhance: bool = Form(Settings.kb_settings.ZH_TITLE_ENHANCE),
    docs: str = Form("{}"),
    not_refresh_vs_cache: bool = Form(False),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)

    # P3：如果开启了异步入库，则切换到 enqueue 模式
    from chayuan.settings import Settings as _Settings
    if bool(getattr(_Settings.basic_settings, "INGEST_ASYNC_ENABLED", False)):
        from chayuan.server.ingest_queue import enqueue_upload_docs
        return enqueue_upload_docs(
            files=files,
            knowledge_base_name=knowledge_base_name,
            override=override,
            to_vector_store=to_vector_store,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            zh_title_enhance=zh_title_enhance,
            docs=docs,
            user_id=(user or {}).get("id") if isinstance(user, dict) else None,
        )

    return _kb_call(
        upload_docs,
        files=files,
        knowledge_base_name=knowledge_base_name,
        override=override,
        to_vector_store=to_vector_store,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        zh_title_enhance=zh_title_enhance,
        docs=docs,
        not_refresh_vs_cache=not_refresh_vs_cache,
        uploader_id=(user or {}).get("id") if isinstance(user, dict) else None,
    )


@kb_router.post("/check_duplicates", summary="上传前按 SHA-256 检测重复文件")
def check_duplicate_files_endpoint(
    items: List[Dict[str, Any]] = Body(..., description="[{file_name,size,sha256}]"),
    scope: Literal["accessible", "current_kb"] = Body("accessible"),
    knowledge_base_name: str = Body("", description="scope=current_kb 时限定 KB"),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.db.repository.knowledge_file_repository import (
        backfill_missing_hashes_by_sizes,
        find_duplicate_files_by_hashes,
    )

    hashes = [str(x.get("sha256") or "").lower() for x in items if isinstance(x, dict)]
    if scope == "current_kb":
        if not knowledge_base_name:
            raise HTTPException(status_code=400, detail="missing knowledge_base_name")
        _require_read(user, knowledge_base_name)
        accessible = [knowledge_base_name]
    elif user is None:
        accessible = []
    elif _is_admin(user):
        accessible = None
    else:
        accessible = list_accessible_kbs(user)
    duplicates = find_duplicate_files_by_hashes(hashes, accessible_kbs=accessible)
    sizes = [int(x.get("file_size") or x.get("size") or 0) for x in items if isinstance(x, dict)]
    if sizes and any(not duplicates.get(h) for h in hashes):
        # 老数据没有 file_hash。只对同 size 且当前用户可见的历史文件做一次懒回填。
        backfill_missing_hashes_by_sizes(sizes, accessible_kbs=accessible)
        duplicates = find_duplicate_files_by_hashes(hashes, accessible_kbs=accessible)
    data = []
    for item in items:
        h = str(item.get("sha256") or "").lower()
        hits = duplicates.get(h, [])
        data.append({
            "file_name": item.get("file_name") or "",
            "file_size": item.get("file_size") or item.get("size") or 0,
            "sha256": h,
            "duplicate_count": len(hits),
            "duplicates": hits,
        })
    return {"code": 0, "msg": "ok", "data": data}


@kb_router.post(
    "/delete_docs", response_model=BaseResponse, summary="删除知识库内指定文件",
)
def delete_docs_endpoint(
    knowledge_base_name: str = Body(..., examples=["samples"]),
    file_names: List[str] = Body(..., examples=[["file_name.md", "test.txt"]]),
    delete_content: bool = Body(False),
    not_refresh_vs_cache: bool = Body(False),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    return _kb_call(
        delete_docs,
        knowledge_base_name=knowledge_base_name,
        file_names=file_names,
        delete_content=delete_content,
        not_refresh_vs_cache=not_refresh_vs_cache,
    )


@kb_router.post("/update_info", response_model=BaseResponse, summary="更新知识库介绍")
def update_info_endpoint(
    knowledge_base_name: str = Body(..., description="知识库名称"),
    kb_info: str = Body(..., description="知识库介绍"),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    return update_info(knowledge_base_name=knowledge_base_name, kb_info=kb_info)


@kb_router.post(
    "/update_docs", response_model=BaseResponse, summary="更新现有文件到知识库",
)
def update_docs_endpoint(
    knowledge_base_name: str = Body(..., description="知识库名称"),
    file_names: List[str] = Body(..., description="文件名称，支持多文件"),
    chunk_size: int = Body(Settings.kb_settings.CHUNK_SIZE),
    chunk_overlap: int = Body(Settings.kb_settings.OVERLAP_SIZE),
    zh_title_enhance: bool = Body(Settings.kb_settings.ZH_TITLE_ENHANCE),
    override_custom_docs: bool = Body(False),
    docs: str = Body("{}"),
    not_refresh_vs_cache: bool = Body(False),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    return _kb_call(
        update_docs,
        knowledge_base_name=knowledge_base_name,
        file_names=file_names,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        zh_title_enhance=zh_title_enhance,
        override_custom_docs=override_custom_docs,
        docs=docs,
        not_refresh_vs_cache=not_refresh_vs_cache,
    )


@kb_router.post("/reindex_docs", response_model=BaseResponse, summary="重新生成指定文件的向量数据")
def reindex_docs_endpoint(
    knowledge_base_name: str = Body(..., description="知识库名称"),
    file_names: List[str] = Body(..., description="文件名称，支持单个或多个"),
    chunk_size: int = Body(Settings.kb_settings.CHUNK_SIZE),
    chunk_overlap: int = Body(Settings.kb_settings.OVERLAP_SIZE),
    zh_title_enhance: bool = Body(Settings.kb_settings.ZH_TITLE_ENHANCE),
    async_task: bool = Body(True, description="True=提交后台任务；False=同步完成后返回"),
    user=Depends(require_auth_enabled()),
):
    """删除旧向量后从源文件重新切片并写入向量库。

    权限沿用写权限:admin / owner / editor(知识库上传维护人员)可执行。
    """
    _require_write(user, knowledge_base_name)
    names = [str(x).strip() for x in (file_names or []) if str(x).strip()]
    if not names:
        raise HTTPException(status_code=400, detail="file_names 不能为空")

    if async_task:
        from chayuan.server.ingest_queue import enqueue_reindex_kb_files
        return enqueue_reindex_kb_files(
            knowledge_base_name=knowledge_base_name,
            file_names=names,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            zh_title_enhance=zh_title_enhance,
            user_id=(user or {}).get("id") if isinstance(user, dict) else None,
        )

    return _kb_call(
        update_docs,
        knowledge_base_name=knowledge_base_name,
        file_names=names,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        zh_title_enhance=zh_title_enhance,
        override_custom_docs=True,
        docs="{}",
        not_refresh_vs_cache=False,
    )


@kb_router.get("/file_chunks", summary="查看某文件源内容切片")
def list_file_chunks_endpoint(
    knowledge_base_name: str = Query(..., description="知识库名称"),
    file_name: str = Query(..., description="文件名称"),
    user=Depends(require_auth_enabled()),
):
    _require_read(user, knowledge_base_name)
    try:
        data = _list_file_chunks_from_source(knowledge_base_name, file_name)
    except Exception as e:  # noqa: BLE001
        logger.info(
            "file_chunks source split failed, fallback to vector store(kb=%s,file=%s): %r",
            knowledge_base_name,
            file_name,
            e,
        )
        data = _list_file_chunks_from_vector_store(knowledge_base_name, file_name)
    data["editable"] = _is_admin(user)
    return {"code": 0, "msg": "ok", "data": data}


@kb_router.post("/admin/file_chunks", summary="管理员保存文件切片并重新向量化")
def save_file_chunks_endpoint(
    knowledge_base_name: str = Body(..., description="知识库名称"),
    file_name: str = Body(..., description="文件名称"),
    chunks: List[Dict[str, Any]] = Body(..., description="切片列表，每项包含 text / metadata"),
    user=Depends(require_auth_enabled()),
):
    _require_admin(user)
    _require_read(user, knowledge_base_name)
    normalized = _normalize_chunk_payload(chunks)

    def _save():
        from langchain_core.documents import Document

        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
        from chayuan.server.knowledge_base.utils import KnowledgeFile

        kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
        if kb is None:
            raise HTTPException(status_code=404, detail=f"未找到知识库 {knowledge_base_name}")
        if not kb.exist_doc(file_name):
            raise HTTPException(status_code=404, detail=f"未找到文件 {file_name}")

        docs = []
        for idx, item in enumerate(normalized):
            metadata = dict(item["metadata"])
            metadata.setdefault("source", file_name)
            metadata["chunk_index"] = idx
            if item.get("chunk_id"):
                metadata["previous_chunk_id"] = item["chunk_id"]
            metadata["edited_by_admin"] = True
            docs.append(Document(page_content=item["text"], metadata=metadata))

        kb_file = KnowledgeFile(filename=file_name, knowledge_base_name=knowledge_base_name)
        ok = kb.update_doc(kb_file, docs=docs, not_refresh_vs_cache=True)
        if not ok:
            raise HTTPException(status_code=500, detail="保存切片并重新向量化失败")
        kb.save_vector_store()
        return {
            "code": 0,
            "msg": "ok",
            "data": {
                "knowledge_base_name": knowledge_base_name,
                "file_name": file_name,
                "chunk_count": len(docs),
                "custom_docs": True,
                "revectorized": True,
            },
        }

    return _kb_call(_save)


@kb_router.get("/download_doc", summary="下载对应的知识文件")
def download_doc_endpoint(
    request: Request,
    knowledge_base_name: str = Query(..., description="知识库名称"),
    file_name: str = Query(..., description="文件名称"),
    preview: bool = Query(False, description="是：预览；否：下载"),
    user=Depends(require_auth_enabled()),
):
    """两种放行路径（plan v1.3 §4.5）：

    1. **常规鉴权**：JWT (Authorization: Bearer 或 cookie) 或 HMAC 三件套 → ``user`` 不空
       → 走 ``_require_read`` 普通 ACL。
    2. **下载短期 token**：``?token=<dl_token>``（kb_download 类型 JWT，30 分钟）
       → AuthMiddleware 看到不是 access JWT 不写 state.user → 进入这里时 user=None
       → 这里识别 dl_token 并强制 aud + kb + file 匹配，匹配成功视为放行。
    """
    from chayuan.server.auth import kb_audit
    rid = request.headers.get("x-request-id", "")

    # 路径 2：下载短期 token
    if user is None:
        dl_tok = (request.query_params.get("token") or "").strip()
        if dl_tok:
            from chayuan.server.auth.download_token import (
                is_download_token, verify_download,
            )
            from chayuan.server.auth.tokens import TokenError as _TokErr
            if is_download_token(dl_tok):
                try:
                    payload = verify_download(
                        dl_tok,
                        expected_kb=knowledge_base_name,
                        expected_file=file_name,
                    )
                    # 用 dl_token 的 aud 反推 user 信息(便于审计追溯)
                    aud_user = {"id": payload.get("aud") or "", "username": "", "role": "dl_token"}
                    kb_audit.download_ok(aud_user, kb=knowledge_base_name, file_name=file_name,
                                         via_dl_token=True, request_id=rid)
                    from chayuan.server.observability import metrics as _metrics_dl
                    _metrics_dl.record_kb_download("dl_token", True, "ok")
                    return download_doc(
                        knowledge_base_name=knowledge_base_name,
                        file_name=file_name,
                        preview=preview,
                    )
                except _TokErr as e:
                    kb_audit.denied(None, kb=knowledge_base_name, action="download",
                                    code=4011, reason=f"dl_token invalid: {e}", request_id=rid)
                    from chayuan.server.observability import metrics as _metrics_dl
                    _metrics_dl.record_kb_denied("dl_token", 4011, "download")
                    raise HTTPException(
                        status_code=401,
                        detail={"code": 4011, "msg": f"download token invalid: {e}"},
                    )

    # 路径 1：常规 ACL
    from chayuan.server.observability import metrics as _metrics
    if user is not None and not can_read_kb(user, knowledge_base_name):
        kb_audit.denied(user, kb=knowledge_base_name, action="download",
                        code=4031, reason="no read permission", request_id=rid)
        _metrics.record_kb_denied(kb_audit.subject_kind_of(user), 4031, "download")
        raise HTTPException(status_code=403,
                            detail={"code": 4031,
                                    "msg": f"no read permission on kb {knowledge_base_name!r}"})

    kb_audit.download_ok(user, kb=knowledge_base_name, file_name=file_name,
                         via_dl_token=False, request_id=rid)
    _metrics.record_kb_download(kb_audit.subject_kind_of(user), False, "ok")
    return download_doc(
        knowledge_base_name=knowledge_base_name,
        file_name=file_name,
        preview=preview,
    )


@kb_router.get("/preview_doc", summary="浏览器内联预览知识文件")
def preview_doc_endpoint(
    knowledge_base_name: str = Query(..., description="知识库名称"),
    file_name: str = Query(..., description="文件名称"),
    user=Depends(require_auth_enabled()),
):
    """与 download_doc(preview=True) 等价。
    存在独立端点是因为 auth/middleware._QUERY_TOKEN_ALLOWED_PATHS 把
    /knowledge_base/preview_doc 列入了 query-token 白名单 — 浏览器
    iframe / <img> 不能塞 Authorization header,要靠 query token。
    前端 useFileUrl.buildPreviewUrl 也是拼这个路径。"""
    _require_read(user, knowledge_base_name)
    return download_doc(
        knowledge_base_name=knowledge_base_name,
        file_name=file_name,
        preview=True,
    )


@kb_router.post(
    "/recreate_vector_store", summary="根据content中文档重建向量库，流式输出处理进度。",
)
def recreate_vs_endpoint(
    knowledge_base_name: str = Body(..., examples=["samples"]),
    allow_empty_kb: bool = Body(True),
    vs_type: str = Body(Settings.kb_settings.DEFAULT_VS_TYPE),
    embed_model: str = Body(None),
    chunk_size: int = Body(Settings.kb_settings.CHUNK_SIZE),
    chunk_overlap: int = Body(Settings.kb_settings.OVERLAP_SIZE),
    zh_title_enhance: bool = Body(Settings.kb_settings.ZH_TITLE_ENHANCE),
    not_refresh_vs_cache: bool = Body(False),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    from chayuan.server.db.repository.knowledge_base_repository import load_kb_from_db
    from chayuan.server.utils import get_default_embedding
    if not embed_model:
        embed_model = get_default_embedding()
    _, current_vs_type, current_embed_model = load_kb_from_db(knowledge_base_name)
    requested_model = (embed_model or "").strip()
    current_model = (current_embed_model or "").strip()
    if requested_model and current_model and requested_model != current_model:
        _require_admin(user)
        _assert_embedding_model_selectable(requested_model, check_access=True)
    elif requested_model:
        _assert_embedding_model_selectable(requested_model, check_access=False)
    return _kb_call(
        recreate_vector_store,
        knowledge_base_name=knowledge_base_name,
        allow_empty_kb=allow_empty_kb,
        vs_type=current_vs_type or vs_type,
        embed_model=embed_model,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        zh_title_enhance=zh_title_enhance,
        not_refresh_vs_cache=not_refresh_vs_cache,
    )


@kb_router.get("/admin/embedding_models", summary="管理员获取可用于重建知识库的嵌入模型")
def list_admin_embedding_models(
    check_access: bool = Query(False, description="是否逐个调用模型做连通性检测"),
    user=Depends(require_auth_enabled()),
):
    _require_admin(user)
    return {"code": 0, "msg": "ok", "data": _available_embedding_models(check_access=check_access)}


@kb_router.post(
    "/admin/recreate_vector_store",
    summary="管理员重新向量化知识库，可切换可用嵌入模型和重置策略",
)
def admin_recreate_vs_endpoint(
    knowledge_base_name: str = Body(..., examples=["samples"]),
    embed_model: str = Body(..., description="必须来自 /knowledge_base/admin/embedding_models"),
    reset_strategy: Literal["atomic_swap", "clear_then_rebuild"] = Body(
        "atomic_swap",
        description="atomic_swap 优先影子索引切换；clear_then_rebuild 会先清空旧向量再重建",
    ),
    allow_empty_kb: bool = Body(True),
    vs_type: str = Body("", description="留空则沿用当前知识库向量库类型"),
    chunk_size: int = Body(Settings.kb_settings.CHUNK_SIZE),
    chunk_overlap: int = Body(Settings.kb_settings.OVERLAP_SIZE),
    zh_title_enhance: bool = Body(Settings.kb_settings.ZH_TITLE_ENHANCE),
    user=Depends(require_auth_enabled()),
):
    _require_admin(user)
    _assert_embedding_model_selectable(embed_model, check_access=True)

    from chayuan.server.db.repository.knowledge_base_repository import load_kb_from_db

    _, current_vs_type, _ = load_kb_from_db(knowledge_base_name)
    effective_vs_type = (vs_type or current_vs_type or Settings.kb_settings.DEFAULT_VS_TYPE).strip()
    return _kb_call(
        recreate_vector_store,
        knowledge_base_name=knowledge_base_name,
        allow_empty_kb=allow_empty_kb,
        vs_type=effective_vs_type,
        embed_model=embed_model.strip(),
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        zh_title_enhance=zh_title_enhance,
        not_refresh_vs_cache=False,
        atomic_swap=(reset_strategy == "atomic_swap"),
    )


# ---------------------------------------------------------------------------
# 临时 KB（文件对话）
# ---------------------------------------------------------------------------

kb_router.post(
    "/upload_temp_docs", summary="上传文件到临时目录，用于文件对话。",
    dependencies=[Depends(require_auth_enabled())],
)(upload_temp_docs)
kb_router.post(
    "/search_temp_docs", summary="检索临时知识库",
    dependencies=[Depends(require_auth_enabled())],
)(search_temp_docs)


@kb_router.get("/tasks/{task_id}", summary="查询异步入库任务状态")
def get_ingest_task(
    task_id: str,
    user=Depends(require_auth_enabled()),
):
    """仅返回状态元数据；文件内容不走这里。"""
    from chayuan.server.ingest_queue import get_task_status
    st = get_task_status(task_id)
    if st is None:
        raise HTTPException(status_code=404, detail="task not found or expired")
    # 非 admin 且任务带 user_id 时，做一次归属校验
    if user is not None and (user.get("role") if isinstance(user, dict) else "") != "admin":
        uid = user.get("id") if isinstance(user, dict) else None
        owner = st.get("payload_summary", {}).get("user_id") if isinstance(st, dict) else None
        if owner is not None and owner != uid:
            raise HTTPException(status_code=403, detail="not task owner")
    return st


@kb_router.post("/tasks/{task_id}/cancel", summary="取消异步入库 / 重建任务")
def cancel_ingest_task(
    task_id: str,
    reason: str = Body("user_cancelled", embed=True),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.ingest_queue.backend import get_task_status, request_task_cancel
    st = get_task_status(task_id)
    if st is None:
        raise HTTPException(status_code=404, detail="task not found or expired")
    if user is not None and (user.get("role") if isinstance(user, dict) else "") != "admin":
        uid = user.get("id") if isinstance(user, dict) else None
        owner = st.get("payload_summary", {}).get("user_id") if isinstance(st, dict) else None
        if owner is not None and owner != uid:
            raise HTTPException(status_code=403, detail="not task owner")
    request_task_cancel(task_id, reason=reason or "user_cancelled")
    return {"code": 0, "msg": "cancelling", "data": {"task_id": task_id}}


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------

summary_router = APIRouter(prefix="/kb_summary_api")


@summary_router.post("/summary_file_to_vector_store", summary="单个知识库根据文件名称摘要")
def summary_file_endpoint(
    knowledge_base_name: str = Body(..., description="知识库名称"),
    file_name: str = Body(..., description="文件名称"),
    model_name: str = Body(None),
    temperature: float = Body(0.01),
    max_tokens: int = Body(None),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    return summary_file_to_vector_store(
        knowledge_base_name=knowledge_base_name,
        file_name=file_name,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )


@summary_router.post(
    "/summary_doc_ids_to_vector_store",
    summary="单个知识库根据doc_ids摘要",
    response_model=BaseResponse,
)
def summary_doc_ids_endpoint(
    knowledge_base_name: str = Body(..., description="知识库名称"),
    doc_ids: List[str] = Body(..., description="doc_ids"),
    model_name: str = Body(None),
    temperature: float = Body(0.01),
    max_tokens: int = Body(None),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    return summary_doc_ids_to_vector_store(
        knowledge_base_name=knowledge_base_name,
        doc_ids=doc_ids,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )


@summary_router.post("/recreate_summary_vector_store", summary="重建单个知识库文件摘要")
def recreate_summary_endpoint(
    knowledge_base_name: str = Body(..., description="知识库名称"),
    allow_empty_kb: bool = Body(True),
    vs_type: str = Body(Settings.kb_settings.DEFAULT_VS_TYPE),
    embed_model: str = Body(None),
    file_description: str = Body(""),
    model_name: str = Body(None),
    temperature: float = Body(0.01),
    max_tokens: int = Body(None),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    return recreate_summary_vector_store(
        knowledge_base_name=knowledge_base_name,
        allow_empty_kb=allow_empty_kb,
        vs_type=vs_type,
        embed_model=embed_model,
        file_description=file_description,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )


kb_router.include_router(summary_router)


# ---------------------------------------------------------------------------
# KB ACL 管理端点
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# RAPTOR / GraphRAG build（B 路线）
# ---------------------------------------------------------------------------

@kb_router.post("/{knowledge_base_name}/build_raptor", summary="构建 RAPTOR（默认异步；Redis 不可用时同步）")
def build_raptor_endpoint(
    knowledge_base_name: str,
    target_cluster_size: int = Body(5),
    max_levels: int = Body(3),
    llm_model: Optional[str] = Body(None),
    sync: bool = Body(False, description="True=同步跑完再返回；False=提交队列立返 task_id"),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    from chayuan.server.shared.jobs import async_enabled, submit_job
    payload = {
        "kb_name": knowledge_base_name,
        "target_cluster_size": int(target_cluster_size),
        "max_levels": int(max_levels),
        "llm_model": llm_model,
    }
    if not sync and async_enabled():
        task_id, status = submit_job("raptor_build", payload)
        return {
            "code": 0, "msg": "enqueued" if status == "enqueued" else "sync_fallback",
            "data": {"task_id": task_id, "status": status, "kb_name": knowledge_base_name},
        }
    # 同步兜底：小 KB / 无 Redis 直接跑
    from chayuan.server.file_rag.raptor.builder import build_raptor_for_kb
    report = build_raptor_for_kb(
        kb_name=knowledge_base_name,
        target_cluster_size=int(target_cluster_size),
        max_levels=int(max_levels),
        llm_model=llm_model,
    )
    code = 1 if report.error else 0
    return {
        "code": code, "msg": report.error or "ok",
        "data": {
            "kb_name": report.kb_name,
            "levels": report.levels,
            "summaries_added": report.summaries_added,
            "elapsed_sec": report.elapsed_sec,
        },
    }


@kb_router.post("/{knowledge_base_name}/build_graphrag", summary="构建 GraphRAG（默认异步；Redis 不可用时同步）")
def build_graphrag_endpoint(
    knowledge_base_name: str,
    max_chunks: int = Body(10000),
    community_min_size: int = Body(2),
    llm_model: Optional[str] = Body(None),
    sync: bool = Body(False),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    from chayuan.server.shared.jobs import async_enabled, submit_job
    payload = {
        "kb_name": knowledge_base_name,
        "max_chunks": int(max_chunks),
        "community_min_size": int(community_min_size),
        "llm_model": llm_model,
    }
    if not sync and async_enabled():
        task_id, status = submit_job("graphrag_build", payload)
        return {
            "code": 0, "msg": "enqueued" if status == "enqueued" else "sync_fallback",
            "data": {"task_id": task_id, "status": status, "kb_name": knowledge_base_name},
        }
    from chayuan.server.file_rag.graphrag.builder import build_graphrag_for_kb
    report = build_graphrag_for_kb(
        kb_name=knowledge_base_name,
        max_chunks=int(max_chunks),
        community_min_size=int(community_min_size),
        llm_model=llm_model,
    )
    code = 1 if report.error else 0
    return {
        "code": code, "msg": report.error or "ok",
        "data": {
            "kb_name": report.kb_name,
            "chunks_processed": report.chunks_processed,
            "entities": report.entities,
            "relations": report.relations,
            "communities": report.communities,
            "elapsed_sec": report.elapsed_sec,
        },
    }


@kb_router.get("/{knowledge_base_name}/build_status/{task_id}", summary="查询异步构建任务状态")
def build_status_endpoint(
    knowledge_base_name: str, task_id: str,
    user=Depends(require_auth_enabled()),
):
    _require_read(user, knowledge_base_name)
    from chayuan.server.shared.jobs import get_job_status
    st = get_job_status(task_id)
    if st is None:
        raise HTTPException(status_code=404, detail="task not found or expired")
    return {"code": 0, "data": st}


@kb_router.get("/{knowledge_base_name}/build_progress/{task_id}",
                summary="SSE 推送异步构建进度（Redis pub/sub）")
async def build_progress_sse_endpoint(
    knowledge_base_name: str, task_id: str,
    user=Depends(require_auth_enabled()),
):
    """服务端发送事件（SSE）：订阅 ``chayuan:ingest:task:{task_id}:events`` 频道，
    把 task 状态变更实时推给前端；任务结束（state in {success,failed}）后自动关流。

    Redis 不可用时退化为一次性返回当前快照。
    """
    import asyncio
    import json as _json
    from fastapi.responses import StreamingResponse
    from chayuan.server.shared.jobs import get_job_status

    _require_read(user, knowledge_base_name)

    async def _event_stream():
        # 先把当前快照推一次
        st = get_job_status(task_id)
        if st is not None:
            yield f"event: snapshot\ndata: {_json.dumps(st, ensure_ascii=False, default=str)}\n\n"
            if st.get("state") in ("success", "failed"):
                return

        # Redis pub/sub；async redis 客户端首选
        try:
            from chayuan.server.shared import get_redis
            r = get_redis()
            if r is None:
                # 无 redis → 轮询式回退（1.5s 一次，最多 10 分钟）
                for _ in range(400):
                    await asyncio.sleep(1.5)
                    cur = get_job_status(task_id)
                    if cur is None:
                        continue
                    yield f"event: update\ndata: {_json.dumps(cur, ensure_ascii=False, default=str)}\n\n"
                    if cur.get("state") in ("success", "failed"):
                        return
                return
            pubsub = r.pubsub()
            channel = f"chayuan:ingest:task:{task_id}:events"
            await pubsub.subscribe(channel)
            try:
                while True:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=30.0)
                    if msg is None:
                        yield ": keep-alive\n\n"
                        continue
                    data = msg.get("data")
                    if isinstance(data, (bytes, bytearray)):
                        data = data.decode("utf-8", errors="ignore")
                    yield f"event: update\ndata: {data}\n\n"
                    try:
                        parsed = _json.loads(data) if isinstance(data, str) else {}
                        state = (parsed.get("state")
                                 or (parsed.get("patch") or {}).get("state")
                                 or (parsed.get("state") if isinstance(parsed.get("state"), dict) else "")
                                 or (parsed.get("state", {}) if isinstance(parsed, dict) else ""))
                        # 兼容两种 payload 形态
                        final_state = ""
                        if isinstance(parsed, dict):
                            final_state = (parsed.get("patch", {}) or {}).get("state", "") \
                                or (parsed.get("state", {}) or {}).get("state", "") \
                                if isinstance(parsed.get("state"), dict) else \
                                (parsed.get("patch", {}) or {}).get("state", "")
                        if final_state in ("success", "failed"):
                            return
                    except Exception:
                        continue
            finally:
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.close()
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            yield f"event: error\ndata: {_json.dumps({'error': repr(e)})}\n\n"

    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@kb_router.get("/{knowledge_base_name}/graphrag/stats", summary="查看 KB 的 GraphRAG 图谱统计")
def graphrag_stats_endpoint(
    knowledge_base_name: str,
    user=Depends(require_auth_enabled()),
):
    _require_read(user, knowledge_base_name)
    from chayuan.server.db.models.graphrag_model import (
        GraphCommunityModel, GraphEntityModel, GraphRelationModel,
    )
    from chayuan.server.db.session import session_scope
    with session_scope() as s:
        ent_count = s.query(GraphEntityModel).filter(
            GraphEntityModel.kb_name == knowledge_base_name,
        ).count()
        rel_count = s.query(GraphRelationModel).filter(
            GraphRelationModel.kb_name == knowledge_base_name,
        ).count()
        comm_count = s.query(GraphCommunityModel).filter(
            GraphCommunityModel.kb_name == knowledge_base_name,
        ).count()
    return {
        "code": 0,
        "data": {
            "kb_name": knowledge_base_name,
            "entities": int(ent_count),
            "relations": int(rel_count),
            "communities": int(comm_count),
        },
    }


@kb_router.get("/{knowledge_base_name}/grants", summary="列出 KB 的访问授权")
def list_grants(
    knowledge_base_name: str,
    user=Depends(require_auth_enabled()),
):
    _require_owner(user, knowledge_base_name)
    from chayuan.server.auth.service import list_kb_grants
    from chayuan.server.db.session import session_scope
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    with session_scope() as s:
        kb = s.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.kb_name == knowledge_base_name
        ).one_or_none()
        if kb is None:
            raise HTTPException(status_code=404, detail="kb not found")
        kb_id = kb.id
        owner_id = kb.owner_id
        visibility = kb.visibility
    grants = list_kb_grants(kb_id)
    return {
        "kb_name": knowledge_base_name,
        "owner_id": owner_id,
        "visibility": visibility,
        "grants": [
            {"user_id": g.user_id, "role": g.role, "granted_by": g.granted_by}
            for g in grants
        ],
    }


@kb_router.post("/{knowledge_base_name}/grants", summary="授予/更新 KB 访问")
def grant_access(
    knowledge_base_name: str,
    target_user_id: int = Body(..., description="被授权用户 id"),
    role: str = Body("reader", description="reader / editor"),
    user=Depends(require_auth_enabled()),
):
    _require_owner(user, knowledge_base_name)
    from chayuan.server.auth.service import grant_kb_access
    from chayuan.server.db.session import session_scope
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    with session_scope() as s:
        kb = s.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.kb_name == knowledge_base_name
        ).one_or_none()
        if kb is None:
            raise HTTPException(status_code=404, detail="kb not found")
        kb_id = kb.id
    grant_kb_access(
        kb_id, int(target_user_id), role,
        granted_by=(user or {}).get("id") if isinstance(user, dict) else None,
    )
    return {"code": 0, "msg": "ok"}


@kb_router.delete("/{knowledge_base_name}/grants/{target_user_id}", summary="撤销授权")
def revoke_access(
    knowledge_base_name: str,
    target_user_id: int,
    user=Depends(require_auth_enabled()),
):
    _require_owner(user, knowledge_base_name)
    from chayuan.server.auth.service import revoke_kb_access
    from chayuan.server.db.session import session_scope
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    with session_scope() as s:
        kb = s.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.kb_name == knowledge_base_name
        ).one_or_none()
        if kb is None:
            raise HTTPException(status_code=404, detail="kb not found")
        kb_id = kb.id
    revoke_kb_access(kb_id, int(target_user_id))
    return {"code": 0, "msg": "ok"}


@kb_router.patch("/{knowledge_base_name}/visibility", summary="修改 KB 可见性")
def set_visibility(
    knowledge_base_name: str,
    visibility: str = Body(..., embed=True, description="private / public"),
    user=Depends(require_auth_enabled()),
):
    _require_owner(user, knowledge_base_name)
    if visibility not in ("private", "public"):
        raise HTTPException(status_code=400, detail="visibility must be private/public")
    from chayuan.server.db.session import session_scope
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    with session_scope() as s:
        kb = s.query(KnowledgeBaseModel).filter(
            KnowledgeBaseModel.kb_name == knowledge_base_name
        ).one_or_none()
        if kb is None:
            raise HTTPException(status_code=404, detail="kb not found")
        kb.visibility = visibility
    return {"code": 0, "msg": "ok"}


# ===========================================================================
# 数据挂载 corpus_pending 任务的 KB 端确认流程
# ===========================================================================
#
# 设计:
#   * 数据挂载发布 corpus 模式时, 不直接 ingest 进 KB, 而是写一条
#     corpus_pending artifact, 等用户来 KB 详情页"确认"才真 ingest
#   * 这避免了无人审核的数据偷偷写入向量库
#   * UI 在 KB 详情页顶部加 PendingMountsBanner, 显示待确认数量 + 点开列表
#
# 端点:
#   GET    /knowledge_base/{kb}/pending_mounts        列出待 ingest 任务
#   POST   /knowledge_base/{kb}/pending_mounts/{aid}/confirm  确认 + 真 ingest
#   POST   /knowledge_base/{kb}/pending_mounts/{aid}/reject   拒绝 (软删)


@kb_router.get(
    "/{knowledge_base_name}/pending_mounts",
    summary="列出待 ingest 的 corpus_pending 数据挂载任务",
)
def list_pending_mounts(
    knowledge_base_name: str,
    user=Depends(require_auth_enabled()),
):
    _require_read(user, knowledge_base_name)
    from chayuan.server.db.repository import data_mount_repository as repo

    items = repo.list_pending_corpus_for_kb(knowledge_base_name)
    # 过滤已 disabled 的 (rejected / 已 ingest 的)
    active = [it for it in items if (it.get("stats") or {}).get("status") != "disabled"]
    return {"code": 0, "data": {"items": active, "total": len(active)}}


@kb_router.post(
    "/{knowledge_base_name}/pending_mounts/{artifact_id}/confirm",
    summary="确认并把待 ingest 任务真正写入 KB 向量库",
)
def confirm_pending_mount(
    knowledge_base_name: str,
    artifact_id: str,
    user=Depends(require_auth_enabled())
):
    _require_write(user, knowledge_base_name)
    from chayuan.server.db.repository import data_mount_repository as repo

    art = repo.get_artifact(artifact_id)
    if art is None:
        raise HTTPException(status_code=404, detail="artifact 不存在")
    if art.get("artifact_type") != "corpus_pending":
        raise HTTPException(
            status_code=400, detail=f"artifact 类型不匹配: {art.get('artifact_type')}",
        )
    payload = art.get("payload") or {}
    items = payload.get("items") or []
    target_kb = (payload.get("target_kb") or "").strip()
    if target_kb and target_kb != knowledge_base_name:
        raise HTTPException(
            status_code=400,
            detail=f"target_kb 不匹配: artifact 指定 {target_kb}, 当前 KB {knowledge_base_name}",
        )

    # 真实 ingest: 把每条 record 转成 langchain Document, 走 KBService.add_doc
    try:
        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
        from langchain_core.documents import Document
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"KB ingest 模块不可用: {e}") from e

    svc = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if svc is None:
        raise HTTPException(status_code=404, detail=f"KB {knowledge_base_name} 不存在")

    docs = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "")
        if not text:
            continue
        md = dict(it.get("metadata") or {})
        md.setdefault("doc_id", it.get("id") or "")
        md.setdefault("source", md.get("source") or it.get("source_type") or "data_mount")
        md.setdefault("data_mount_artifact_id", artifact_id)
        docs.append(Document(page_content=text, metadata=md))

    ingested = 0
    if docs:
        try:
            ingested = len(svc.do_add_doc(docs)) if hasattr(svc, "do_add_doc") else 0
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"ingest 失败: {e}") from e

    repo.mark_artifact_disabled(artifact_id, reason=f"confirmed by user; ingested {ingested}")
    return {"code": 0, "data": {"ingested": ingested, "total_items": len(items)}}


@kb_router.post(
    "/{knowledge_base_name}/pending_mounts/{artifact_id}/reject",
    summary="拒绝待 ingest 任务 (软删, 不会真删 artifact, 仅标 disabled)",
)
def reject_pending_mount(
    knowledge_base_name: str,
    artifact_id: str,
    reason: str = Body("", embed=True),
    user=Depends(require_auth_enabled()),
):
    _require_write(user, knowledge_base_name)
    from chayuan.server.db.repository import data_mount_repository as repo

    art = repo.get_artifact(artifact_id)
    if art is None:
        raise HTTPException(status_code=404, detail="artifact 不存在")
    if art.get("artifact_type") != "corpus_pending":
        raise HTTPException(status_code=400, detail="只能拒绝 corpus_pending 类型")

    repo.mark_artifact_disabled(artifact_id, reason=reason or "rejected")
    return {"code": 0, "msg": "ok"}
