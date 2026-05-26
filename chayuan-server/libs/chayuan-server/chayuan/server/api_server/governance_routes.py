"""数据治理管理 API（P1-9）。

端点分组：
- /governance/policy        策略 CRUD
- /governance/lineage       血缘记录查询 / 排行
- /governance/usage         本人 / 全局用量与额度
- /governance/pii/scan      对任意文本做 PII 扫描（调试 / 测试用）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from chayuan.server.auth.deps import require_auth_enabled

logger = logging.getLogger("chayuan.api.governance")


governance_router = APIRouter(prefix="/governance", tags=["Governance"])


def _is_admin(user) -> bool:
    if user is None:
        return True  # AUTH_REQUIRED=false 场景
    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", "")
    return str(role) == "admin"


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

@governance_router.get("/policy", summary="列出全部治理策略")
def list_policies_endpoint(user=Depends(require_auth_enabled())):
    if not _is_admin(user):
        raise HTTPException(403, "admin only")
    from chayuan.server.governance.policy import list_policies
    return {"code": 0, "data": list_policies()}


@governance_router.post("/policy", summary="新建/更新治理策略")
def upsert_policy_endpoint(
    scope: str = Body(..., description="user:<id> / role:<name> / global"),
    daily_token_budget: int = Body(-1),
    qps: int = Body(-1),
    masking_level: str = Body("loose"),
    allowed_modes: List[str] = Body(default_factory=list),
    extra: Dict = Body(default_factory=dict),
    enabled: int = Body(1),
    user=Depends(require_auth_enabled()),
):
    if not _is_admin(user):
        raise HTTPException(403, "admin only")
    from chayuan.server.governance.policy import upsert_policy
    pid = upsert_policy(
        scope=scope, daily_token_budget=daily_token_budget, qps=qps,
        masking_level=masking_level, allowed_modes=allowed_modes,
        extra=extra, enabled=enabled,
    )
    return {"code": 0, "data": {"id": pid}}


@governance_router.get("/policy/effective", summary="查看某用户当前生效策略")
def effective_policy_endpoint(
    target_user_id: Optional[int] = Query(None),
    target_user_role: str = Query(""),
    user=Depends(require_auth_enabled()),
):
    # 非 admin 只能查自己
    if not _is_admin(user) and isinstance(user, dict):
        if target_user_id not in (None, int(user.get("id") or -1)):
            raise HTTPException(403, "only admin can query other users")
        target_user_id = int(user.get("id") or -1)
        target_user_role = str(user.get("role") or "")
    from chayuan.server.governance.policy import get_policy
    p = get_policy(user_id=target_user_id, user_role=target_user_role)
    return {"code": 0, "data": {
        "scope": p.scope,
        "daily_token_budget": p.daily_token_budget,
        "qps": p.qps, "masking_level": p.masking_level,
        "allowed_modes": p.allowed_modes,
        "extra": p.extra,
    }}


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

@governance_router.get("/lineage", summary="血缘记录列表（非 admin 仅返回自己的记录）")
def list_lineage_endpoint(
    target_user_id: Optional[int] = Query(None),
    mode: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=24 * 90),
    limit: int = Query(200, ge=1, le=1000),
    user=Depends(require_auth_enabled()),
):
    # 非 admin 强制只看自己
    if not _is_admin(user):
        target_user_id = (user or {}).get("id") if isinstance(user, dict) else None
    from chayuan.server.governance.lineage import list_lineage
    return {
        "code": 0,
        "data": list_lineage(
            user_id=int(target_user_id) if target_user_id is not None else None,
            mode=mode, hours=int(hours), limit=int(limit),
        ),
    }


@governance_router.get("/lineage/top_objects", summary="被读最多的对象排行（表/列/文件）")
def top_touched_endpoint(
    object_type: str = Query("", description="table/column/file/collection/index；空为全部"),
    hours: int = Query(168, ge=1, le=24 * 90),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(require_auth_enabled()),
):
    if not _is_admin(user):
        raise HTTPException(403, "admin only")
    from chayuan.server.governance.lineage import top_touched_objects
    return {
        "code": 0,
        "data": top_touched_objects(
            object_type=object_type, hours=int(hours), limit=int(limit),
        ),
    }


# ---------------------------------------------------------------------------
# Usage / Quota
# ---------------------------------------------------------------------------

@governance_router.get("/usage/today", summary="本人今日用量与剩余额度")
def today_usage_endpoint(user=Depends(require_auth_enabled())):
    from chayuan.server.governance.quota import today_usage
    uid = (user or {}).get("id") if isinstance(user, dict) else None
    urole = (user or {}).get("role") if isinstance(user, dict) else ""
    if uid is None:
        return {"code": 0, "data": {"used": 0, "budget": -1, "remaining": -1}}
    return {"code": 0, "data": today_usage(user_id=int(uid), user_role=str(urole or ""))}


# ---------------------------------------------------------------------------
# Guardrail 调试端点
# ---------------------------------------------------------------------------

@governance_router.post("/guardrail/check_input", summary="对任意文本做输入侧 Guardrail 检查（dry-run）")
def guardrail_check_input_endpoint(
    text: str = Body(..., embed=True),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.guardrails import get_guardrail
    verdict = get_guardrail().check_input(text or "")
    return {"code": 0, "data": verdict.to_dict()}


@governance_router.post("/guardrail/check_output", summary="对任意文本做输出侧 Guardrail 检查（dry-run）")
def guardrail_check_output_endpoint(
    text: str = Body(..., embed=True),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.guardrails import get_guardrail
    verdict = get_guardrail().check_output(text or "")
    return {"code": 0, "data": verdict.to_dict()}


@governance_router.get("/guardrail/info", summary="当前生效的 Guardrail 后端信息")
def guardrail_info_endpoint(user=Depends(require_auth_enabled())):
    from chayuan.settings import Settings
    from chayuan.server.guardrails import get_guardrail
    backend = get_guardrail()
    return {
        "code": 0,
        "data": {
            "backend": backend.name,
            "enabled": bool(getattr(Settings.basic_settings, "GUARDRAIL_ENABLED", False)),
            "configured_backend": str(getattr(Settings.basic_settings, "GUARDRAIL_BACKEND", "rules")),
            "llama_model": str(getattr(Settings.basic_settings, "GUARDRAIL_LLAMA_MODEL", "")),
        },
    }


# ---------------------------------------------------------------------------
# PII 扫描（调试）
# ---------------------------------------------------------------------------

@governance_router.post("/pii/scan", summary="对任意文本做 PII 扫描")
def pii_scan_endpoint(
    text: str = Body(...),
    enable_presidio: bool = Body(True),
    user_role: str = Body("user", description="期望的脱敏等级角色；用于 dry-run 预览"),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.governance.masking import apply_masking
    from chayuan.server.governance.pii import scan_text
    entities = scan_text(text, enable_presidio=bool(enable_presidio))
    masked = apply_masking(text, entities, user_role=user_role)
    return {"code": 0, "data": {"entities": entities, "masked": masked}}
