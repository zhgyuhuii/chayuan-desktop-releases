from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.db.repository import data_mount_repository as repo


data_mount_router = APIRouter(prefix="/data-mounts", tags=["Training Data Mounts"])


def _uid(user: Optional[Dict[str, Any]]) -> Optional[int]:
    try:
        return int((user or {}).get("id") or (user or {}).get("user_id"))
    except Exception:  # noqa: BLE001
        return None


def _is_admin(user: Optional[Dict[str, Any]]) -> bool:
    return str((user or {}).get("role") or "").lower() == "admin"


def _ensure_scope_allowed(scope_type: str, scope_id: str, user: Optional[Dict[str, Any]]) -> None:
    if _is_admin(user):
        return
    uid = _uid(user)
    if scope_type == "global":
        raise HTTPException(403, "只有管理员可以创建全局训练数据挂载")
    if scope_type == "user" and str(scope_id or "") not in ("", str(uid or "")):
        raise HTTPException(403, "只能为当前用户创建个人挂载")
    # group/app/kb 权限后续接入对应 ACL；这里先要求登录用户，避免匿名创建。
    if uid is None:
        raise HTTPException(401, "invalid user")


class DataMountCreateBody(BaseModel):
    name: str
    description: str = ""
    scope_type: str = "user"
    scope_id: str = ""
    source_filter: Dict[str, Any] = {}
    mount_modes: List[str] = ["preference", "fewshot", "retrieval_boost"]
    priority: int = 0
    max_items: int = 20
    max_tokens: int = 1600
    publish: bool = False


class DataMountPatchBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    scope_type: Optional[str] = None
    scope_id: Optional[str] = None
    source_filter: Optional[Dict[str, Any]] = None
    mount_modes: Optional[List[str]] = None
    priority: Optional[int] = None
    max_items: Optional[int] = None
    max_tokens: Optional[int] = None
    enabled: Optional[bool] = None


@data_mount_router.post("", summary="创建训练数据挂载")
def create_data_mount(body: DataMountCreateBody, user=Depends(require_auth_enabled())) -> Dict[str, Any]:
    scope_id = body.scope_id
    if body.scope_type == "user" and not scope_id:
        scope_id = str(_uid(user) or "")
    _ensure_scope_allowed(body.scope_type, scope_id, user)
    row = repo.create_mount(
        name=body.name,
        description=body.description,
        scope_type=body.scope_type,
        scope_id=scope_id,
        source_filter=body.source_filter,
        mount_modes=body.mount_modes,
        priority=body.priority,
        max_items=body.max_items,
        max_tokens=body.max_tokens,
        created_by=_uid(user),
    )
    if body.publish:
        row = repo.publish_mount(row["id"], actor_id=_uid(user)) or row
    return {"code": 0, "msg": "ok", "data": row}


@data_mount_router.get("", summary="列出训练数据挂载")
def list_data_mounts(
    status: Optional[str] = Query(None),
    scope_type: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    rows, total = repo.list_mounts(
        status=status, scope_type=scope_type, enabled=enabled, limit=limit, offset=offset,
    )
    return {"code": 0, "msg": "ok", "data": {"items": rows, "total": total}}


@data_mount_router.get("/{mount_id}", summary="读取训练数据挂载详情")
def get_data_mount(
    mount_id: str,
    include_artifacts: bool = Query(False),
    _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    row = repo.get_mount(mount_id, include_artifacts=include_artifacts)
    if row is None:
        raise HTTPException(404, "data mount not found")
    return {"code": 0, "msg": "ok", "data": row}


@data_mount_router.patch("/{mount_id}", summary="更新训练数据挂载草稿")
def patch_data_mount(
    mount_id: str,
    body: DataMountPatchBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    scope_type = patch.get("scope_type")
    scope_id = patch.get("scope_id")
    if scope_type is not None:
        _ensure_scope_allowed(str(scope_type), str(scope_id or ""), user)
    row = repo.update_mount(mount_id, patch=patch, actor_id=_uid(user))
    if row is None:
        raise HTTPException(404, "data mount not found")
    return {"code": 0, "msg": "ok", "data": row}


@data_mount_router.post("/{mount_id}/preview", summary="预览训练数据挂载物化结果")
def preview_data_mount(mount_id: str, _user=Depends(require_auth_enabled())) -> Dict[str, Any]:
    row = repo.preview_mount(mount_id)
    if row is None:
        raise HTTPException(404, "data mount not found")
    return {"code": 0, "msg": "ok", "data": row}


@data_mount_router.post("/{mount_id}/publish", summary="发布训练数据挂载")
def publish_data_mount(mount_id: str, user=Depends(require_auth_enabled())) -> Dict[str, Any]:
    row = repo.publish_mount(mount_id, actor_id=_uid(user))
    if row is None:
        raise HTTPException(404, "data mount not found")
    return {"code": 0, "msg": "ok", "data": row}


@data_mount_router.post("/{mount_id}/disable", summary="停用训练数据挂载")
def disable_data_mount(mount_id: str, user=Depends(require_auth_enabled())) -> Dict[str, Any]:
    row = repo.set_mount_enabled(mount_id, enabled=False, actor_id=_uid(user))
    if row is None:
        raise HTTPException(404, "data mount not found")
    return {"code": 0, "msg": "ok", "data": row}


@data_mount_router.post("/{mount_id}/enable", summary="启用训练数据挂载")
def enable_data_mount(mount_id: str, user=Depends(require_auth_enabled())) -> Dict[str, Any]:
    row = repo.set_mount_enabled(mount_id, enabled=True, actor_id=_uid(user))
    if row is None:
        raise HTTPException(404, "data mount not found")
    return {"code": 0, "msg": "ok", "data": row}


@data_mount_router.get("/{mount_id}/hits", summary="查询训练数据挂载命中日志")
def list_data_mount_hits(
    mount_id: str,
    limit: int = Query(100, ge=1, le=500),
    _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    return {"code": 0, "msg": "ok", "data": repo.list_hits(mount_id, limit=limit)}


# ===========================================================================
# 通用源适配 API —— 给 12 种源用
# ===========================================================================

class SourceProbeBody(BaseModel):
    source_type: str
    options: Dict[str, Any] = {}
    max_items: int = 1000


class SourceAnalyzeBody(BaseModel):
    source_type: str
    options: Dict[str, Any] = {}
    sample_size: int = 50


class MountImportBody(BaseModel):
    """批量导入 mount draft.

    ``format`` = 'json' 时,``content`` 是一个 JSON list, 每项是 mount 配置。
    ``format`` = 'csv'  时,``content`` 是 CSV 文本(首行表头);要求至少有
    ``name``, ``source_type``, ``mount_modes``(逗号分隔), 其它列拼到 options。
    """
    format: str  # "json" / "csv"
    content: str
    scope_type: str = "user"
    scope_id: str = ""
    publish: bool = False  # 默认创草稿;true = 同时发布


@data_mount_router.get("/sources", summary="列出可用数据源 + 字段表单 schema")
def list_sources(_user=Depends(require_auth_enabled())) -> Dict[str, Any]:
    from chayuan.server.data_mount import get_registry
    return {"code": 0, "data": get_registry().to_catalog()}


@data_mount_router.post("/sources/probe", summary="对给定源配置做探活")
def probe_source(
    body: SourceProbeBody, _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    from chayuan.server.data_mount import get_registry, SourceSpec

    adapter = get_registry().get(body.source_type)
    if adapter is None:
        raise HTTPException(404, f"unknown source_type: {body.source_type}")
    spec = SourceSpec(source_type=body.source_type, options=body.options,
                      max_items=body.max_items)
    return {"code": 0, "data": adapter.probe(spec).to_dict()}


@data_mount_router.post("/sources/analyze", summary="抽样并自动分析字段 schema")
def analyze_source(
    body: SourceAnalyzeBody, _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    from chayuan.server.data_mount import get_registry, SourceSpec

    adapter = get_registry().get(body.source_type)
    if adapter is None:
        raise HTTPException(404, f"unknown source_type: {body.source_type}")
    spec = SourceSpec(source_type=body.source_type, options=body.options)
    sample = adapter.sample(spec, n=int(body.sample_size or 50))
    return {"code": 0, "data": sample.to_dict()}


@data_mount_router.post("/import", summary="批量导入 mount 配置(json / csv)")
def import_data_mounts(
    body: MountImportBody, user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    items = _parse_import_payload(body.format, body.content)
    if not items:
        raise HTTPException(400, "未解析到任何 mount 配置")
    created: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for raw in items:
        try:
            create_body = DataMountCreateBody(
                name=str(raw.get("name") or "未命名挂载")[:128],
                description=str(raw.get("description") or ""),
                scope_type=str(raw.get("scope_type") or body.scope_type),
                scope_id=str(raw.get("scope_id") or body.scope_id or ""),
                source_filter=raw.get("source_filter") or {},
                mount_modes=list(raw.get("mount_modes") or []),
                priority=int(raw.get("priority") or 0),
                max_items=int(raw.get("max_items") or 20),
                max_tokens=int(raw.get("max_tokens") or 1600),
            )
            created.append(create_data_mount(create_body, user))
            if body.publish and created:
                last = created[-1].get("data") or {}
                mid = last.get("id")
                if mid:
                    publish_data_mount(mid, user)
        except Exception as e:  # noqa: BLE001
            errors.append({"name": raw.get("name"), "error": str(e)})
    return {"code": 0, "data": {"created": created, "errors": errors}}


@data_mount_router.get("/{mount_id}/export", summary="导出 mount 配置 + artifact (JSON)")
def export_data_mount(
    mount_id: str, _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    full = repo.get_mount(mount_id, include_artifacts=True) if hasattr(repo, "get_mount") else None
    if full is None:
        # repo.get_mount 接受 session 参数;转一下
        from chayuan.server.db.session import session_scope
        with session_scope() as session:
            full = repo.get_mount(session, mount_id, include_artifacts=True)
    if full is None:
        raise HTTPException(404, "data mount not found")
    return {"code": 0, "data": full}


# 模板示例行的过滤规则
# 命中任一即跳过(让用户既能下载模板看示例,又能直接改改提交不必删除)
_SAMPLE_NAME_PREFIXES = ("[示例]", "[example]", "[sample]", "[模板]")


def _is_sample_row(row: Dict[str, Any]) -> bool:
    """判断是否为模板示例行 (导入时跳过)。

    规则:
    1. ``_example`` / ``_sample`` / ``_template`` 任一为 truthy → 是示例
    2. ``name`` 以 ``[示例]`` / ``[example]`` 等前缀开头 → 是示例
    3. ``source_type`` 为 ``__example__`` 这种 sentinel → 是示例
    """
    if not isinstance(row, dict):
        return False
    for k in ("_example", "_sample", "_template", "_is_example"):
        if row.get(k):
            return True
    name = str(row.get("name") or "").strip().lower()
    for pref in _SAMPLE_NAME_PREFIXES:
        if name.startswith(pref.lower()):
            return True
    if str(row.get("source_type") or "").startswith("__example"):
        return True
    return False


def _strip_sample_meta(row: Dict[str, Any]) -> Dict[str, Any]:
    """去掉 ``_example`` / ``_comment`` 这类元字段,避免污染 create_body。"""
    return {k: v for k, v in row.items()
            if not (k.startswith("_") or k.startswith("#"))}


def _parse_import_payload(fmt: str, content: str) -> List[Dict[str, Any]]:
    fmt = (fmt or "").strip().lower()
    if fmt == "json":
        import json
        # 兼容 JSON 顶层 object 形如 {"_meta": {...}, "items": [...]}
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}") from e
        # 顶层若是 object 且含 items 字段 → 把 items 作为列表;
        # 否则若是 dict 当成单条;若是 list 直接用
        if isinstance(data, dict):
            if isinstance(data.get("items"), list):
                data = data["items"]
            else:
                data = [data]
        if not isinstance(data, list):
            raise HTTPException(400, "JSON 顶层必须是 object 或 list")
        # 过滤示例行 + 去元字段
        return [_strip_sample_meta(it) for it in data
                if isinstance(it, dict) and not _is_sample_row(it)]
    if fmt == "csv":
        import csv
        import io
        # 跳过以 # 开头的注释行(stdlib csv 不原生支持注释,自己 pre-filter)
        cleaned_lines = [ln for ln in content.splitlines()
                         if ln.strip() and not ln.lstrip().startswith("#")]
        reader = csv.DictReader(io.StringIO("\n".join(cleaned_lines)))
        out: List[Dict[str, Any]] = []
        for row in reader:
            if _is_sample_row(row):
                continue
            modes_raw = (row.get("mount_modes") or "").strip()
            modes = [m.strip() for m in modes_raw.split(",") if m.strip()]
            source_type = (row.get("source_type") or "").strip()
            # CSV 其它列(除已命名字段外) 拼成 source_filter.spec.options
            reserved = {"name", "description", "scope_type", "scope_id",
                        "source_type", "mount_modes", "priority",
                        "max_items", "max_tokens", "target_kb",
                        "_example", "_sample", "_template", "_is_example",
                        "_comment"}
            options = {k: v for k, v in row.items()
                       if k not in reserved and not k.startswith("_")
                       and v not in (None, "")}
            out.append({
                "name": row.get("name") or "未命名挂载",
                "description": row.get("description") or "",
                "scope_type": row.get("scope_type") or "user",
                "scope_id": row.get("scope_id") or "",
                "source_filter": {
                    "spec": {
                        "source_type": source_type,
                        "options": options,
                        "max_items": int(row.get("max_items") or 1000),
                    },
                    "target_kb": row.get("target_kb") or "",
                },
                "mount_modes": modes,
                "priority": int(row.get("priority") or 0),
                "max_items": int(row.get("max_items") or 20),
                "max_tokens": int(row.get("max_tokens") or 1600),
            })
        return out
    raise HTTPException(400, f"不支持的格式: {fmt} (期望 json / csv)")
