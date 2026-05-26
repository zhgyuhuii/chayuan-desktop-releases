"""/storage/* — 统一文件存储管理 API。

能力：
- GET /storage/status              查看后端 / 每 namespace 对象数
- POST /storage/test_connection    测试 MinIO 连通性（不落盘）
- POST /storage/switch_backend     admin 切换 local/minio 并热重置 factory 缓存
- POST /storage/migrate            local ↔ minio 数据迁移
- GET /storage/stream              LocalStorage 预签名 URL 的服务端代理
- GET /storage/list                列某 namespace 内对象
- DELETE /storage/delete           删除某 key
"""
from __future__ import annotations

import io
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.file_storage import NS, get_storage, reset_cache

logger = logging.getLogger("chayuan.api.storage")

storage_router = APIRouter(prefix="/storage", tags=["File Storage"])


def _is_admin(user) -> bool:
    if user is None:
        return True  # AUTH_REQUIRED=false 下单机 dev
    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", "")
    return str(role) == "admin"


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

@storage_router.get("/status", summary="查看存储后端状态 + 每命名空间统计")
def storage_status(user=Depends(require_auth_enabled())):
    storage = get_storage()
    info = storage.backend_info()
    # 附加：期望的命名空间清单（即便没用过也展示）
    info.setdefault("namespaces", {})
    for ns in NS.all():
        info["namespaces"].setdefault(ns, {"objects": 0, "size_mb": 0})
    return {"code": 0, "data": info}


@storage_router.post("/test_connection", summary="测试 MinIO 连通性（不落盘配置）")
def storage_test_connection(
    endpoint: str = Body(..., embed=True),
    access_key: str = Body(...),
    secret_key: str = Body(...),
    secure: bool = Body(False),
    region: str = Body("us-east-1"),
    user=Depends(require_auth_enabled()),
):
    if not _is_admin(user):
        raise HTTPException(403, "admin only")
    try:
        from chayuan.server.file_storage.minio import MinioStorage
        m = MinioStorage(
            endpoint=endpoint, access_key=access_key, secret_key=secret_key,
            secure=secure, region=region, bucket_prefix="chayuan_probe",
        )
        info = m.backend_info()
    except Exception as e:  # noqa: BLE001
        return {"code": 0, "data": {"ok": False, "error": f"{type(e).__name__}: {e}"}}
    return {"code": 0, "data": {"ok": bool(info.get("healthy")), "info": info}}


# ---------------------------------------------------------------------------
# 后端切换（写 basic_settings + reset cache）
# ---------------------------------------------------------------------------

@storage_router.post("/switch_backend", summary="切换存储后端（admin）")
def storage_switch_backend(
    backend: str = Body(..., embed=True, description="local / minio"),
    minio: Optional[Dict[str, Any]] = Body(None, description="backend=minio 时必填"),
    user=Depends(require_auth_enabled()),
):
    if not _is_admin(user):
        raise HTTPException(403, "admin only")
    backend = (backend or "").strip().lower()
    if backend not in ("local", "minio"):
        raise HTTPException(400, "backend must be local|minio")
    try:
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        bs.FILE_STORAGE_BACKEND = backend
        if backend == "minio" and isinstance(minio, dict):
            bs.MINIO_ENDPOINT = str(minio.get("endpoint") or "")
            bs.MINIO_ACCESS_KEY = str(minio.get("access_key") or "")
            bs.MINIO_SECRET_KEY = str(minio.get("secret_key") or "")
            bs.MINIO_SECURE = bool(minio.get("secure", False))
            bs.MINIO_REGION = str(minio.get("region") or "us-east-1")
            bs.MINIO_BUCKET_PREFIX = str(minio.get("bucket_prefix") or "chayuan")
            if isinstance(minio.get("buckets"), dict):
                bs.MINIO_BUCKETS = minio["buckets"]
        bs.create_template_file(write_file=True)
        reset_cache()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"切换失败：{e}")
    # 重置后新 factory 实例立即生效
    new_info = get_storage().backend_info()
    return {"code": 0, "data": {"backend": backend, "info": new_info}}


# ---------------------------------------------------------------------------
# 迁移（local ↔ minio）
# ---------------------------------------------------------------------------

@storage_router.post("/migrate", summary="把某 namespace 的对象从当前后端迁移到另一后端")
def storage_migrate(
    namespace: str = Body(..., embed=True),
    direction: str = Body("local_to_minio", description="local_to_minio / minio_to_local"),
    dry_run: bool = Body(True),
    limit: int = Body(500),
    user=Depends(require_auth_enabled()),
):
    if not _is_admin(user):
        raise HTTPException(403, "admin only")
    if namespace not in NS.all():
        raise HTTPException(400, f"unknown namespace: {namespace}")
    try:
        from chayuan.server.file_storage.local import LocalStorage
        from chayuan.server.file_storage.minio import MinioStorage
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        src: Any
        dst: Any
        if direction == "local_to_minio":
            src = LocalStorage()
            dst = MinioStorage(
                endpoint=str(getattr(bs, "MINIO_ENDPOINT", "")),
                access_key=str(getattr(bs, "MINIO_ACCESS_KEY", "")),
                secret_key=str(getattr(bs, "MINIO_SECRET_KEY", "")),
                secure=bool(getattr(bs, "MINIO_SECURE", False)),
                region=str(getattr(bs, "MINIO_REGION", "us-east-1")),
                bucket_prefix=str(getattr(bs, "MINIO_BUCKET_PREFIX", "chayuan")),
            )
        elif direction == "minio_to_local":
            dst = LocalStorage()
            src = MinioStorage(
                endpoint=str(getattr(bs, "MINIO_ENDPOINT", "")),
                access_key=str(getattr(bs, "MINIO_ACCESS_KEY", "")),
                secret_key=str(getattr(bs, "MINIO_SECRET_KEY", "")),
                secure=bool(getattr(bs, "MINIO_SECURE", False)),
                region=str(getattr(bs, "MINIO_REGION", "us-east-1")),
                bucket_prefix=str(getattr(bs, "MINIO_BUCKET_PREFIX", "chayuan")),
            )
        else:
            raise HTTPException(400, "direction must be local_to_minio|minio_to_local")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"后端实例化失败：{e}")

    t0 = time.time()
    items = src.list(namespace, limit=int(limit))
    migrated: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for obj in items:
        if dry_run:
            migrated.append({"key": obj.key, "size": obj.size})
            continue
        try:
            data = src.get(namespace, obj.key)
            dst.put(namespace, obj.key, data)
            migrated.append({"key": obj.key, "size": obj.size})
        except Exception as e:  # noqa: BLE001
            errors.append({"key": obj.key, "error": f"{type(e).__name__}: {e}"})

    return {
        "code": 0,
        "data": {
            "dry_run": bool(dry_run),
            "namespace": namespace,
            "direction": direction,
            "migrated_count": len(migrated),
            "errors_count": len(errors),
            "elapsed_sec": round(time.time() - t0, 2),
            "preview": migrated[:20],
            "errors_preview": errors[:10],
        },
    }


# ---------------------------------------------------------------------------
# 内部流式代理（LocalStorage 预签名 URL 对应的服务端）
# ---------------------------------------------------------------------------

@storage_router.get("/stream", summary="LocalStorage 预签名 URL 的内部代理流式下载")
def storage_stream(
    ns: str = Query(...),
    key: str = Query(...),
    token: str = Query(...),
    exp: int = Query(...),
    download: bool = Query(False),
):
    from chayuan.server.file_storage.local import verify_local_signature
    if not verify_local_signature(ns, key, exp, token):
        raise HTTPException(403, "invalid or expired signature")
    storage = get_storage()
    if not storage.exists(ns, key):
        raise HTTPException(404, "object not found")

    def _iter():
        stream = storage.open_read(ns, key)
        try:
            # 优先 ctx manager
            reader = stream.__enter__() if hasattr(stream, "__enter__") else stream
            try:
                while True:
                    chunk = reader.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if hasattr(stream, "__exit__"):
                    stream.__exit__(None, None, None)
                elif hasattr(stream, "close"):
                    stream.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("storage_stream 读流失败：%r", e)
            return

    import os
    filename = os.path.basename(key) or "download.bin"
    dispo = f'attachment; filename="{filename}"' if download else f'inline; filename="{filename}"'
    return StreamingResponse(
        _iter(), media_type="application/octet-stream",
        headers={"Content-Disposition": dispo},
    )


# ---------------------------------------------------------------------------
# 列/删
# ---------------------------------------------------------------------------

@storage_router.get("/list", summary="列某 namespace 内对象")
def storage_list(
    ns: str = Query(...),
    prefix: str = Query(""),
    limit: int = Query(200),
    user=Depends(require_auth_enabled()),
):
    if ns not in NS.all():
        raise HTTPException(400, f"unknown namespace: {ns}")
    items = get_storage().list(ns, prefix=prefix, limit=int(limit))
    return {
        "code": 0,
        "data": [
            {
                "key": o.key, "size": o.size,
                "last_modified": o.last_modified,
                "etag": o.etag, "content_type": o.content_type,
            }
            for o in items
        ],
    }


@storage_router.delete("/object", summary="删除某 key（admin）")
def storage_delete_object(
    ns: str = Query(...),
    key: str = Query(...),
    user=Depends(require_auth_enabled()),
):
    if not _is_admin(user):
        raise HTTPException(403, "admin only")
    if ns not in NS.all():
        raise HTTPException(400, f"unknown namespace: {ns}")
    ok = get_storage().delete(ns, key)
    return {"code": 0 if ok else 1, "msg": "ok" if ok else "not found"}
