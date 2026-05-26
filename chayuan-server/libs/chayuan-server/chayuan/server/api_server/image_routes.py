"""图像知识源 FastAPI 路由（B 补丁 + 新增 kind=image）。

路径：/knowledge_source/{source_id}/image/*
以及独立的 /image_models/*（模型管理，不绑 source）
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.auth.source_access import can_read, can_write

logger = logging.getLogger("chayuan.api.image")

image_router = APIRouter(prefix="/knowledge_source", tags=["Knowledge Source - Image"])
image_model_router = APIRouter(prefix="/image_models", tags=["Image Models"])


def _get_source_or_404(source_id: int) -> Dict[str, Any]:
    from chayuan.server.db.repository.knowledge_source_repository import get_source
    src = get_source(int(source_id))
    if src is None:
        raise HTTPException(404, "source not found")
    if src.get("kind") != "image":
        raise HTTPException(400, "this endpoint only for kind=image sources")
    return src


def _build_image_connector(source_id: int):
    from chayuan.server.db.repository.knowledge_source_repository import (
        connection_spec_for_source,
    )
    from chayuan.server.image_source.connector import ImageConnector
    resolved = connection_spec_for_source(int(source_id))
    if resolved is None:
        raise HTTPException(400, "image source missing connection")
    _, spec = resolved
    return ImageConnector(spec=spec, source_id=int(source_id))


def _resolve_store_name(source_id: int) -> str:
    """统一 store 名解析,与 ImageConnector 完全同源。"""
    return _build_image_connector(int(source_id)).source_name


def purge_image_source_storage(source_id: int, store_name: str) -> None:
    """删图像知识源后,清掉它在磁盘 + 进程内的全部痕迹。

    根因:``delete_source`` 只删 DB 行(source / connection / grants /
    schema_cache),图像知识源真正的数据全在 DB 之外:

      * ``data/image_indexes/<source_name>/`` —— meta.json + 向量 .npy;
      * ``data/image_indexes/<source_id>_files/`` —— 原图文件;
      * ``get_store`` 的进程内缓存 ``_STORES``(按 source_name 键)。

    这三处 delete_source 都不碰。而 ``source_name`` 取自 spec.options /
    spec.database —— **跟名字走、稳定**。于是「删图像知识库 → 同名重建」时
    ``get_store`` 命中旧 meta.json / 旧缓存对象 → 旧图全部复活。删除时必须
    把这三处一并清掉。
    """
    import shutil  # noqa: F401 — force_rmtree 已够用,保留以备
    from chayuan.server.image_source.store import _image_indexes_root, invalidate
    from chayuan.server.knowledge_base.kb_service.base import force_rmtree

    # 1. 进程内 store 缓存(_STORES,按 source_name 键)
    try:
        invalidate(store_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("[delete_source] 清 store 缓存失败 %s: %r", store_name, e)

    # 2. 磁盘:store 目录(按 source_name)+ 原图文件目录(按 source_id)
    root = _image_indexes_root()
    for d in (root / store_name, root / f"{int(source_id)}_files"):
        try:
            if not d.exists():
                continue
            if force_rmtree(str(d)):
                logger.info("[delete_source] 已清图像目录 %s", d)
            else:
                logger.error("[delete_source] 图像目录清不掉 %s —— 同名重建可能复活", d)
        except Exception as e:  # noqa: BLE001
            logger.warning("[delete_source] 清图像目录失败 %s: %r", d, e)

    # 3. FileStorage(IMAGE_FILES 命名空间,按 source_id 前缀)
    try:
        from chayuan.server.file_storage import NS, get_storage
        storage = get_storage()
        for obj in storage.list(
            NS.IMAGE_FILES, prefix=f"{int(source_id)}/", limit=100000
        ):
            storage.delete(NS.IMAGE_FILES, obj.key)
    except Exception as e:  # noqa: BLE001
        logger.debug("[delete_source] 清 IMAGE_FILES FileStorage 失败 %s: %r",
                     source_id, e)


# ---------------------------------------------------------------------------
# Upload / list / delete
# ---------------------------------------------------------------------------

@image_router.post("/{source_id}/image/upload", summary="上传图像到知识源(即时占位 + 异步流水线)")
async def upload_image_endpoint(
    source_id: int,
    files: List[UploadFile] = File(...),
    tags: str = Form(""),
    background_tasks: "BackgroundTasks" = None,
    user=Depends(require_auth_enabled()),
):
    """同步阶段:落盘 + 插占位(state=queued)。异步阶段:OCR ‖ CLIP + 默认文本向量模型。

    返回每张图的 ``state="queued"`` item;前端立即渲染占位卡片,然后轮询
    ``/image/{image_id}/status`` 拿状态更新。
    """
    from fastapi import BackgroundTasks as _BG
    if background_tasks is None:
        background_tasks = _BG()
    if not can_write(user, int(source_id)):
        raise HTTPException(403, "no write permission")
    _get_source_or_404(int(source_id))
    conn = _build_image_connector(int(source_id))
    store_name = conn.source_name

    from chayuan.server.image_source.store import _image_indexes_root, get_store
    from chayuan.server.image_source.pipeline import process_item
    import hashlib

    store = get_store(store_name)
    img_dir = _image_indexes_root() / f"{int(source_id)}_files"
    img_dir.mkdir(parents=True, exist_ok=True)

    try:
        from chayuan.server.file_storage import NS, get_storage
        storage = get_storage()
    except Exception:  # noqa: BLE001
        storage = None

    added: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for f in files:
        data = await f.read()
        if not data:
            errors.append({"filename": f.filename or "?", "error": "empty file"})
            continue
        md5_full = hashlib.md5(data).hexdigest()
        md5 = md5_full[:8]
        fname = os.path.basename(f.filename or "upload.bin")
        out = img_dir / f"{md5}_{fname}"
        item_id = f"img_{md5_full[:12]}"

        # 同 hash 去重:已存在的 item 直接返回,不再走 pipeline
        existing = store.get(item_id)
        if existing is not None:
            added.append(_item_to_wire(existing, int(source_id)))
            continue

        # 落盘(永不阻断主流程;失败也算 error 但不抛)
        try:
            out.write_bytes(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("upload_image 落盘失败 %s: %r", fname, e)
            errors.append({"filename": fname, "error": f"落盘失败: {e}"})
            continue
        if storage is not None:
            try:
                storage.put(
                    NS.IMAGE_FILES, f"{int(source_id)}/{md5}_{fname}",
                    data, content_type=f.content_type or "image/jpeg",
                )
            except Exception as _e:  # noqa: BLE001
                logger.debug("image_files 存储同步失败(忽略):%r", _e)

        rec = store.insert_placeholder(
            item_id=item_id, filename=fname,
            mime_type=f.content_type or "image/octet-stream",
            size_bytes=len(data), path=str(out),
            md5=md5_full, tags=tags or "",
        )
        added.append(_item_to_wire(rec, int(source_id)))
        background_tasks.add_task(
            process_item, store_name, item_id, data,
            model_name=conn._model_name,
        )

    return {
        "code": 0,
        "data": {"added": added, "errors": errors},
    }


def _item_to_wire(rec: Dict[str, Any], source_id: int) -> Dict[str, Any]:
    """统一前端契约:image_id / state / progress / preview url / has_text_vector。"""
    img_id = rec.get("id")
    return {
        "filename": rec.get("filename"),
        "image_id": img_id,
        "path": rec.get("path"),
        "state": rec.get("state", "ready"),
        "progress": int(rec.get("progress") or 0),
        "error": rec.get("error"),
        "has_text_vector": bool(rec.get("has_text_vector")),
        "ocr_text": rec.get("ocr_text"),
        "preview_url": f"/knowledge_source/{int(source_id)}/image/{img_id}",
        # 老字段保留
        "vector_status": (
            "indexed" if rec.get("image_vector_id") is not None
            else ("queued" if rec.get("state") == "queued" else "missing")
        ),
    }


def _looks_like_missing_embedder(e: Exception) -> bool:
    """判断错误是否为"图像 embedder 不可用"。

    匹配关键字:
        * ``embedder``  / ``encode_image``
        * ``transformers`` / ``torch`` 缺失
        * ``model files``  not found
        * ``huggingface_hub`` 加载错
    """
    msg = (str(e) or "").lower()
    keys = (
        "embedder", "encode_image", "no module named 'transformers'",
        "no module named 'torch'", "siglip", "clip-vit",
        "open_clip", "weights_only", "snapshot_download",
        "could not load",
    )
    return any(k in msg for k in keys)


def _suggest_image_embedder() -> Dict[str, str]:
    """返回当前默认图像 embedder 的推荐安装信息。

    优先级:
      1. capability_defaults.clip(89-5 真源)
      2. ``Settings.kb_settings.IMAGE_EMBEDDER``(老兼容)
      3. 内置推荐 ``jinaai/jina-clip-v1``(89 设计:Infinity 主推,跨模态)

    runtime 默认 ``infinity``(89-13 后 Infinity 是真源)。
    """
    repo = ""
    try:
        from chayuan.server.image_source.embedder import resolve_default
        repo, _plat = resolve_default()
    except Exception:  # noqa: BLE001
        pass
    if not repo:
        try:
            from chayuan.settings import Settings  # type: ignore
            repo = (getattr(Settings.kb_settings, "IMAGE_EMBEDDER", "") or "").strip()
        except Exception:  # noqa: BLE001
            repo = ""
    repo = repo or "jinaai/jina-clip-v1"
    return {
        "repo": repo,
        "capability": "image-embedding",
        "runtime": "infinity",
        # 89-11:前端用这个 deeplink 直接跳到模型广场预筛选好的页面
        "marketplace_deeplink": (
            "/admin#/model-settings/marketplace?"
            f"capability=image-embedding&model={repo}&runtime=infinity"
        ),
    }


@image_router.get("/{source_id}/image/list", summary="列出图像索引条目")
def list_images_endpoint(
    source_id: int, limit: int = Query(200),
    user=Depends(require_auth_enabled()),
):
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    _get_source_or_404(int(source_id))
    from chayuan.server.image_source.store import get_store
    store = get_store(_resolve_store_name(int(source_id)))
    items = []
    for m in store.list_items(int(limit)):
        items.append(_item_to_wire(m, int(source_id)))
    return {
        "code": 0,
        "data": {"count": store.count(), "dim": store.dim(), "items": items},
    }


@image_router.get(
    "/{source_id}/image/{image_id}/status",
    summary="单条图像 item 处理状态(供前端轮询)",
)
def item_status_endpoint(
    source_id: int, image_id: str,
    user=Depends(require_auth_enabled()),
):
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    _get_source_or_404(int(source_id))
    from chayuan.server.image_source.store import get_store
    store = get_store(_resolve_store_name(int(source_id)))
    rec = store.get(image_id)
    if rec is None:
        raise HTTPException(404, f"image {image_id} not found")
    return {
        "code": 0,
        "data": {
            "id": image_id,
            "state": rec.get("state", "ready"),
            "progress": int(rec.get("progress") or 0),
            "error": rec.get("error"),
            "has_text_vector": bool(rec.get("has_text_vector")),
            "ocr_text": (rec.get("ocr_text") or "")[:500] or None,
        },
    }


@image_router.delete("/{source_id}/image/{image_id}", summary="从索引删除图像")
def delete_image_endpoint(
    source_id: int, image_id: str,
    user=Depends(require_auth_enabled()),
):
    if not can_write(user, int(source_id)):
        raise HTTPException(403, "no write permission")
    _get_source_or_404(int(source_id))
    from chayuan.server.image_source.store import get_store
    ok = get_store(_resolve_store_name(source_id)).remove(image_id)
    return {"code": 0 if ok else 1, "msg": "ok" if ok else "not found"}


@image_router.post(
    "/{source_id}/image/{image_id}/reindex",
    summary="对已上传的图像重新跑向量化流水线(OCR + CLIP)",
)
async def reindex_image_endpoint(
    source_id: int, image_id: str,
    background_tasks: "BackgroundTasks" = None,
    user=Depends(require_auth_enabled()),
):
    """重新向量化:对一张已入库的图(常是 state=partial/failed —— 上次 CLIP 没
    成功)重跑 pipeline。从磁盘重读原图、重置状态,后台再跑一遍 OCR + CLIP。
    用户修好图像模型环境后不必重新上传,点"重新向量化"即可。"""
    from fastapi import BackgroundTasks as _BG
    if background_tasks is None:
        background_tasks = _BG()
    if not can_write(user, int(source_id)):
        raise HTTPException(403, "no write permission")
    _get_source_or_404(int(source_id))
    conn = _build_image_connector(int(source_id))
    store_name = conn.source_name

    from chayuan.server.image_source.store import get_store
    from chayuan.server.image_source.pipeline import process_item

    store = get_store(store_name)
    rec = store.get(image_id)
    if rec is None:
        raise HTTPException(404, f"未找到图像 {image_id}")

    img_path = rec.get("path")
    if not img_path or not os.path.exists(img_path):
        raise HTTPException(
            404,
            f"图像原文件已不在({img_path or '无路径'}),无法重新向量化,请重新上传该图。",
        )
    try:
        with open(img_path, "rb") as fh:
            data = fh.read()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"读取图像原文件失败: {e}") from e

    # 重置状态 → 后台重跑 pipeline(OCR + CLIP)
    store.update(image_id, state="queued", progress=0, error=None)
    background_tasks.add_task(
        process_item, store_name, image_id, data,
        model_name=conn._model_name,
    )
    return {"code": 0, "data": _item_to_wire(store.get(image_id), int(source_id))}


@image_router.get("/{source_id}/image/{image_id}", summary="下载/预览图像文件")
def image_file_endpoint(
    source_id: int, image_id: str,
    download: bool = Query(False),
    user=Depends(require_auth_enabled()),
):
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    _get_source_or_404(int(source_id))
    from chayuan.server.image_source.store import get_store
    store = get_store(_resolve_store_name(source_id))
    rec = next((m for m in store._meta if m.get("id") == image_id), None)
    if rec is None:
        raise HTTPException(404, "image not found")
    local_path = rec.get("path") or ""
    fname = os.path.basename(local_path) if local_path else image_id

    # N 补丁：minio 后端优先走预签名 302；local 本地缺失时从 storage 拉回
    try:
        from chayuan.server.file_storage import NS, get_storage
        from fastapi.responses import RedirectResponse
        storage = get_storage()
        key = f"{int(source_id)}/{fname}"
        if storage.name == "minio" and storage.exists(NS.IMAGE_FILES, key):
            return RedirectResponse(storage.presigned_url(NS.IMAGE_FILES, key, expires_sec=3600))
        if local_path and not os.path.isfile(local_path) and storage.exists(NS.IMAGE_FILES, key):
            storage.copy_to_local(NS.IMAGE_FILES, key, local_path)
    except Exception:  # noqa: BLE001
        pass

    if not local_path or not os.path.isfile(local_path):
        raise HTTPException(404, "image file missing")
    return FileResponse(
        local_path,
        filename=fname if download else None,
        media_type=None,
    )


# ---------------------------------------------------------------------------
# 文字搜图
# ---------------------------------------------------------------------------

@image_router.post(
    "/{source_id}/image/search_by_text",
    summary="按文字搜图(用户默认文本向量模型路 + CLIP 跨模态路 RRF 融合)",
)
async def search_by_text_endpoint(
    source_id: int,
    payload: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
):
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    _get_source_or_404(int(source_id))
    query = str(payload.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query required")
    top_k = int(payload.get("top_k") or 20)
    k_rrf = int(payload.get("k_rrf") or 60)

    conn = _build_image_connector(int(source_id))
    from chayuan.server.image_source.text_search import text_search
    out = await text_search(
        conn.source_name, query,
        top_k=top_k, k_rrf=k_rrf,
        source_id=int(source_id),
        model_name=conn._model_name,
    )
    return {"code": 0, "data": out}


# ---------------------------------------------------------------------------
# 以图搜图
# ---------------------------------------------------------------------------

@image_router.post("/{source_id}/image/search_by_image", summary="以图搜图")
async def search_by_image_endpoint(
    source_id: int,
    file: UploadFile = File(...),
    top_k: int = Form(5),
    user=Depends(require_auth_enabled()),
):
    """以图搜图。

    Concurrency: CLIP encode_image 是同步 torch 推理(耗时 1-3s),直接在 async
    route 里跑会阻塞 event loop —— 用户上传时若已有 background process_item 在
    跑 CLIP 编码,搜索请求 30s 内拿不到响应,前端 AbortController 触发
    "BizError: 请求已中止"。

    解法:把 search_by_image 整个调用塞进 asyncio.to_thread,让 event loop 在
    CLIP 期间可以继续接其它请求。process_item 已经走 to_thread,两边互不阻塞。
    """
    import asyncio
    if not can_read(user, int(source_id)):
        raise HTTPException(403, "no read permission")
    _get_source_or_404(int(source_id))
    conn = _build_image_connector(int(source_id))
    try:
        data = await file.read()
        # 丢到临时文件传给 PIL.Image
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tf:
            tf.write(data)
            tmp_path = tf.name
        try:
            chunks = await asyncio.to_thread(
                conn.search_by_image, tmp_path, int(top_k),
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"search_by_image 失败：{e}")
    return {"code": 0, "data": [c.to_wire() for c in chunks]}


# ---------------------------------------------------------------------------
# 图像模型管理（全局，不绑 source）
# ---------------------------------------------------------------------------

@image_model_router.get("/", summary="列出支持的图像模型（含磁盘缓存/依赖/测试三态 + 能力）")
def list_image_models_endpoint(
    ready: bool = Query(
        False,
        description="True=仅返回严格就绪（依赖OK+已缓存+已通过smoke测试）的模型，用于KB下拉",
    ),
    crossmodal_only: bool = Query(
        False,
        description="配合 ready=true 使用：仅返回跨模态模型（text+image 同空间）。"
                    "默认 False 保持兼容；KB 文本查图场景建议传 True。",
    ),
    user=Depends(require_auth_enabled()),
):
    """统一入口——UI / KB 下拉共用。

    - ``ready=false``：面板列表，覆盖所有支持模型 + 三态信息 + capabilities（供"下载/测试/删除"按钮）
    - ``ready=true``：仅严格就绪模型
    - ``ready=true, crossmodal_only=true``：仅跨模态就绪模型（KB 文本查图下拉专用）
    """
    from chayuan.server.image_source.model_registry import (
        list_models_for_ui, list_ready_models,
    )
    if ready:
        data = list_ready_models(crossmodal_only=crossmodal_only)
    else:
        data = list_models_for_ui()
    return {"code": 0, "data": data}


@image_model_router.post("/{model_name:path}/smoke_test",
                         summary="对已下载模型做 smoke 测试（加载+小图 embed）")
def smoke_test_image_model_endpoint(
    model_name: str,
    sync: bool = Query(True, description="False=提交异步任务（首次加载耗时）"),
    user=Depends(require_auth_enabled()),
):
    role = user.get("role") if isinstance(user, dict) else ""
    if role != "admin" and user is not None:
        raise HTTPException(403, "admin only")
    from chayuan.server.shared.jobs import async_enabled, submit_job
    if not sync and async_enabled():
        task_id, status = submit_job(
            "image_model_smoke_test", {"model_name": model_name},
        )
        return {"code": 0, "data": {"task_id": task_id, "status": status}}
    from chayuan.server.image_source.model_manager import smoke_test_model
    ret = smoke_test_model(model_name)
    return {
        "code": 0 if ret.get("ok") else 1,
        "msg": ret.get("error") or ret.get("msg") or "ok",
        "data": ret,
    }


@image_model_router.post("/download", summary="从 HuggingFace Hub 下载模型（同步/长耗时，建议 Arq 异步）")
def download_image_model_endpoint(
    model_name: str = Body(..., embed=True),
    sync: bool = Body(False, embed=True, description="True=同步下载；False=提交异步任务"),
    user=Depends(require_auth_enabled()),
):
    # 仅 admin 可下载（节省带宽）
    role = user.get("role") if isinstance(user, dict) else ""
    if role != "admin" and user is not None:
        raise HTTPException(403, "admin only")
    from chayuan.server.shared.jobs import async_enabled, submit_job
    if not sync and async_enabled():
        task_id, status = submit_job("image_model_download", {"model_name": model_name})
        return {"code": 0, "data": {"task_id": task_id, "status": status}}
    from chayuan.server.image_source.model_manager import download_model
    ret = download_model(model_name)
    return {"code": 0 if ret.get("ok") else 1,
            "msg": ret.get("error") or "ok", "data": ret}


@image_model_router.post("/upload_bundle", summary="用户上传模型 zip/tar.gz 离线安装")
async def upload_model_bundle_endpoint(
    model_name: str = Form(...),
    bundle: UploadFile = File(...),
    user=Depends(require_auth_enabled()),
):
    role = user.get("role") if isinstance(user, dict) else ""
    if role != "admin" and user is not None:
        raise HTTPException(403, "admin only")
    # 临时文件保存
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(bundle.filename or "")[1] or ".zip") as tf:
        data = await bundle.read()
        tf.write(data)
        tmp_path = tf.name
    try:
        from chayuan.server.image_source.model_manager import upload_model_bundle
        ret = upload_model_bundle(model_name=model_name, bundle_path=tmp_path)
        return {"code": 0 if ret.get("ok") else 1,
                "msg": ret.get("error") or "ok", "data": ret}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:  # noqa: BLE001
            pass


@image_model_router.delete("/{model_name:path}", summary="删除已缓存的模型")
def delete_image_model_endpoint(
    model_name: str, user=Depends(require_auth_enabled()),
):
    role = user.get("role") if isinstance(user, dict) else ""
    if role != "admin" and user is not None:
        raise HTTPException(403, "admin only")
    from chayuan.server.image_source.model_manager import delete_model
    ret = delete_model(model_name)
    return {"code": 0 if ret.get("ok") else 1, "msg": ret.get("error") or ret.get("msg", "ok")}


@image_model_router.get("/disk_usage", summary="模型缓存磁盘占用总览")
def disk_usage_endpoint(user=Depends(require_auth_enabled())):
    from chayuan.server.image_source.model_manager import disk_usage_summary
    return {"code": 0, "data": disk_usage_summary()}


# ---------------------------------------------------------------------------
# 89-6:GET /image_models/runtime_status
# 模型广场 + ④ tab 共用的"实时运行状态"查询。
#
# 内容:
#   * default_model_id  / default_platform   ← embedder.resolve_default()
#   * client_in_use     / client_healthy     ← 当前生效 client 类型 + 探活
#   * infinity_loaded[] ← 扫 <CHAYUAN_ROOT>/compose/infinity.yaml 的 MODEL_ID
#                          + docker compose ps state
#   * hf_cache_present[] ← 扫 ~/.cache/huggingface/hub
#
# 5 秒 LRU,避免每次刷卡片都 docker exec / yaml IO。
# ---------------------------------------------------------------------------

import time as _time_mod
from threading import Lock as _Lock

_RUNTIME_CACHE: Dict[str, Any] = {}
_RUNTIME_CACHE_LOCK = _Lock()
_RUNTIME_CACHE_TTL = 5.0


def _scan_infinity_loaded() -> List[Dict[str, Any]]:
    """读 compose/infinity.yaml MODEL_ID env + docker compose ps,返回已上线模型。"""
    out: List[Dict[str, Any]] = []
    try:
        from chayuan.server.config_panel.compose_manager import (
            get_compose_file_for_service,
        )
        yaml_p = get_compose_file_for_service("infinity")
    except Exception:  # noqa: BLE001
        yaml_p = None
    if not yaml_p:
        return out

    # 解析 yaml 拿 MODEL_ID
    model_id = ""
    try:
        import yaml  # type: ignore
        with open(yaml_p, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        env = (doc.get("services") or {}).get("infinity", {}).get("environment") or []
        if isinstance(env, list):
            for item in env:
                s = str(item)
                if s.startswith("MODEL_ID="):
                    model_id = s.split("=", 1)[1].strip()
                    break
        elif isinstance(env, dict):
            model_id = str(env.get("MODEL_ID") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("scan infinity yaml failed: %r", e)

    if not model_id:
        return out

    # 探活:走 _docker_compose_ps;running 才算 loaded
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            _docker_compose_ps,
        )
        state, _url = _docker_compose_ps("infinity")
        if state == "running":
            out.append({"model_id": model_id})
    except Exception as e:  # noqa: BLE001
        logger.debug("docker ps infinity failed: %r", e)
    return out


def _scan_hf_cache_present() -> List[str]:
    """扫 ~/.cache/huggingface/hub 列出所有 ``models--*``。

    转 ``models--owner--repo`` → ``owner/repo`` 形式。
    """
    import re as _re
    out: List[str] = []
    cache_root = Path(
        os.environ.get(
            "HF_HOME",
            str(Path.home() / ".cache" / "huggingface"),
        )
    ) / "hub"
    if not cache_root.exists():
        return out
    try:
        for p in cache_root.iterdir():
            name = p.name
            if not name.startswith("models--"):
                continue
            # models--owner--repo[--sub]
            stripped = name[len("models--"):]
            owner_repo = stripped.replace("--", "/", 1)
            out.append(owner_repo)
    except Exception as e:  # noqa: BLE001
        logger.debug("scan hf cache failed: %r", e)
    return out


@image_model_router.get(
    "/runtime_status",
    summary="89-6:图像嵌入运行时状态(模型广场 / ④ tab 共用)",
)
def image_models_runtime_status(user=Depends(require_auth_enabled())):
    """返回当前默认 / client 类型 / Infinity 已加载 / hf-cache 命中。

    5 秒 LRU 缓存,避免每次刷卡片都 docker exec。
    """
    now = _time_mod.time()
    with _RUNTIME_CACHE_LOCK:
        cached = _RUNTIME_CACHE.get("payload")
        ts = _RUNTIME_CACHE.get("ts", 0.0)
        if cached and now - ts < _RUNTIME_CACHE_TTL:
            return {"code": 0, "data": cached}

    # 1) resolve_default
    try:
        from chayuan.server.image_source.embedder import resolve_default
        default_model_id, default_platform = resolve_default()
    except Exception as e:  # noqa: BLE001
        logger.warning("resolve_default failed: %r", e)
        default_model_id, default_platform = "", None

    # 2) client_in_use + healthy(不强制实例化,只判断 platform 推断 + 主动探活)
    client_in_use = ""
    client_healthy = False
    try:
        from chayuan.server.image_source.embedder import get_client
        cli = get_client()
        client_in_use = cli.kind
        client_healthy = bool(cli.healthcheck())
    except Exception as e:  # noqa: BLE001
        logger.debug("get_client probe failed: %r", e)
        client_in_use = ""
        client_healthy = False

    # 3) infinity_loaded
    infinity_loaded = _scan_infinity_loaded()

    # 4) hf_cache_present
    hf_cache_present = _scan_hf_cache_present()

    payload = {
        "default_model_id": default_model_id,
        "default_platform": default_platform,
        "client_in_use": client_in_use,
        "client_healthy": client_healthy,
        "infinity_loaded": infinity_loaded,
        "hf_cache_present": hf_cache_present,
        "fetched_at": int(now),
    }
    with _RUNTIME_CACHE_LOCK:
        _RUNTIME_CACHE["payload"] = payload
        _RUNTIME_CACHE["ts"] = now
    return {"code": 0, "data": payload}


def _invalidate_runtime_status_cache() -> None:
    """配置变更后(89-9 挂载 / ④ tab 切换)立即清掉 5s 缓存。"""
    with _RUNTIME_CACHE_LOCK:
        _RUNTIME_CACHE.clear()


# ---------------------------------------------------------------------------
# 89-9:POST /image_models/mount_to_infinity
# 把模型 id 写进 infinity.yaml 的 MODEL_ID env,异步执行 docker compose up -d
# 重起 Infinity 容器使其加载该模型。
# ---------------------------------------------------------------------------

def _write_infinity_model_id(model_id: str) -> Path:
    """把 MODEL_ID 写入 infinity.yaml,返回 yaml 路径。

    最小改动原则:只改 ``services.infinity.environment`` 里的 MODEL_ID 行,
    不动其它字段(image / ports / volumes 等)。
    """
    from chayuan.server.config_panel.compose_manager import (
        get_compose_file_for_service,
    )
    yaml_p = get_compose_file_for_service("infinity")
    if yaml_p is None or not yaml_p.exists():
        raise HTTPException(404, "infinity.yaml not found")

    import yaml as _yaml  # type: ignore
    with open(yaml_p, "r", encoding="utf-8") as f:
        doc = _yaml.safe_load(f) or {}
    svc = (doc.get("services") or {}).get("infinity") or {}
    env = svc.get("environment")

    if isinstance(env, list):
        new_env = []
        replaced = False
        for item in env:
            s = str(item)
            if s.startswith("MODEL_ID="):
                new_env.append(f"MODEL_ID={model_id}")
                replaced = True
            else:
                new_env.append(item)
        if not replaced:
            new_env.append(f"MODEL_ID={model_id}")
        svc["environment"] = new_env
    elif isinstance(env, dict):
        env["MODEL_ID"] = model_id
        svc["environment"] = env
    else:
        svc["environment"] = [f"MODEL_ID={model_id}"]

    doc.setdefault("services", {})["infinity"] = svc

    # 写回(原子写)
    tmp = yaml_p.with_suffix(yaml_p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, yaml_p)
    return yaml_p


def _kick_infinity_restart_async(yaml_p: Path) -> None:
    """启动一个后台线程跑 ``docker compose -f <yaml> up -d --wait infinity``。"""
    import subprocess
    import threading

    def _run():
        try:
            subprocess.run(
                ["docker", "compose", "-f", str(yaml_p),
                 "up", "-d", "--wait", "infinity"],
                check=False, timeout=180,
                capture_output=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("infinity restart failed: %r", e)
        finally:
            # 重启后清缓存,让 UI 立刻看到新状态
            try:
                _invalidate_runtime_status_cache()
                from chayuan.server.image_source.embedder import (
                    _invalidate_client_cache,
                )
                _invalidate_client_cache(None)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_run, name="mount-infinity", daemon=True).start()


@image_model_router.get(
    "/local_infinity_pip/status",
    summary="93-1:本地 pip Infinity 当前状态",
)
def local_infinity_pip_status_endpoint(
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.config_panel.local_infinity_pip import (
        get_local_infinity_status,
    )
    st = get_local_infinity_status(probe_port=True)
    return {"code": 0, "data": st.to_dict()}


@image_model_router.post(
    "/local_infinity_pip/start",
    summary="93-4:后台启动本地 pip Infinity 子进程",
)
def local_infinity_pip_start_endpoint(
    payload: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
):
    """需 admin 或 guest(AUTH_REQUIRED=false)。"""
    role = user.get("role") if isinstance(user, dict) else ""
    is_guest = bool(user.get("is_guest")) if isinstance(user, dict) else False
    if role != "admin" and not is_guest and user is not None:
        raise HTTPException(403, "admin only")
    model_id = str(payload.get("model_id") or "").strip()
    if not model_id:
        raise HTTPException(400, "model_id required")
    port = payload.get("port")
    try:
        port_n = int(port) if port else None
    except (TypeError, ValueError):
        port_n = None
    from chayuan.server.config_panel.local_infinity_pip import (
        start_local_infinity_subprocess,
    )
    ret = start_local_infinity_subprocess(model_id, port=port_n)
    return {
        "code": 0 if ret["ok"] else 1,
        "data": ret,
        "msg": ret["msg"],
    }


@image_model_router.post(
    "/mount_to_infinity",
    summary="89-9:把模型挂到 Infinity(写 yaml + 重起容器)",
)
def mount_to_infinity_endpoint(
    payload: Dict[str, Any] = Body(...),
    user=Depends(require_auth_enabled()),
):
    """把 ``model_id`` 写到 infinity.yaml 的 MODEL_ID,异步重起容器。

    需 admin 角色(改 yaml + 重启容器是 high-impact 动作)。
    """
    role = user.get("role") if isinstance(user, dict) else ""
    if role != "admin" and user is not None:
        # AUTH_REQUIRED=false 时 user 是 GUEST_USER dict,允许;
        # 其它情况限管理员
        is_guest = bool(user.get("is_guest")) if isinstance(user, dict) else False
        if not is_guest:
            raise HTTPException(403, "admin only")
    model_id = str(payload.get("model_id") or "").strip()
    if not model_id:
        raise HTTPException(400, "model_id required")
    try:
        yaml_p = _write_infinity_model_id(model_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("mount_to_infinity write yaml failed")
        raise HTTPException(500, f"write infinity.yaml failed: {e}")

    _kick_infinity_restart_async(yaml_p)

    import uuid as _uuid
    return {
        "code": 0,
        "data": {
            "task_id": f"mount-{_uuid.uuid4().hex[:8]}",
            "estimated_seconds": 30,
            "yaml_path": str(yaml_p),
            "model_id": model_id,
        },
        "msg": "ok",
    }
