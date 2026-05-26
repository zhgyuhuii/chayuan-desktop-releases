"""模型厂商公开 API（``/v1/providers/*``）。

单机版前端「模型广场」入口；底层复用 ``admin/model_platforms`` 同一组
repository + utils 函数，但**鉴权放宽**为 ``get_current_user_optional``——
单机模式不强制登录，所有用户共享一份全局厂商配置（详见 CLAUDE.md §1）。

API 形态（与 CLAUDE.md §1.2 对齐）：

* ``GET    /v1/providers?category=…``      列出厂商，按 7 类分类过滤
* ``POST   /v1/providers``                 新增厂商（仅自定义/手动追加用）
* ``PATCH  /v1/providers/:id``             修改厂商配置
* ``DELETE /v1/providers/:id``             删除厂商
* ``POST   /v1/providers/:id/test``        探测连通性 + 返回检测到的模型清单
* ``POST   /v1/providers/:id/refresh-models``  仅返回 detected 模型清单
* ``GET    /v1/providers/:id/models``      返回该厂商当前已选 + 已禁的模型分组
* ``PATCH  /v1/providers/:id/models/:model_id``  启停 / 别名 / 类型标签

`category` 取值（前端 → 后端 tag 映射）：

============ ================
category     PROVIDER_CATALOG.tags
============ ================
recommended  推荐 (+ 几个内置 EXTRA_RECOMMENDED 兜底)
all          (不过滤)
local        本地
cn           国内
global       国外
gateway      聚合
custom       自定义 (DB 里 ``_custom=true`` 的)
============ ================
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from chayuan.server.auth.deps import get_current_user_optional


logger = logging.getLogger("chayuan.api.providers")

provider_router = APIRouter(prefix="/v1/providers", tags=["Providers"])


# 与前端 modelMarket.region 对齐的分类 → tag 映射。
_CATEGORY_TAG_MAP: Dict[str, str] = {
    "recommended": "推荐",
    "local": "本地",
    "cn": "国内",
    "global": "国外",
    "gateway": "聚合",
    "custom": "自定义",
}

# 推荐位补丁——除了 catalog 里已经标 "推荐" 的，再追加这些热门厂商。
# 与前端 marketplace 模块的 EXTRA_RECOMMENDED_PIDS 保持一致。
_EXTRA_RECOMMENDED_PIDS = frozenset(
    {"volcengine", "moonshot", "minimax", "openai", "anthropic"}
)


class _ProviderBody(BaseModel):
    """新建 / 更新厂商的统一请求体；可选字段 None = 不修改。"""
    pid: Optional[str] = None  # 别名等同 platform_name
    platform_name: Optional[str] = None
    platform_type: Optional[str] = None
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None
    api_proxy: Optional[str] = None
    api_concurrencies: Optional[int] = None
    enabled: Optional[bool] = None
    auto_detect_model: Optional[bool] = None
    llm_models: Optional[List[str]] = None
    embed_models: Optional[List[str]] = None
    text2image_models: Optional[List[str]] = None
    image2text_models: Optional[List[str]] = None
    rerank_models: Optional[List[str]] = None
    speech2text_models: Optional[List[str]] = None
    text2speech_models: Optional[List[str]] = None
    disabled_models: Optional[List[str]] = None
    description: Optional[str] = None


def _body_to_fields(body: _ProviderBody) -> Dict[str, Any]:
    """body → repository fields；None 字段省略，pid 兼容 platform_name。"""
    raw = body.model_dump(exclude_unset=True)
    raw.pop("pid", None)
    raw.pop("platform_name", None)
    fields: Dict[str, Any] = {}
    for k, v in raw.items():
        if v is None:
            continue
        if k == "disabled_models":
            fields[k] = sorted(set(v))
        else:
            fields[k] = v
    return fields


def _resolve_name(body: _ProviderBody) -> Optional[str]:
    """优先 platform_name，回退 pid。"""
    return body.platform_name or body.pid


# ─────────────────────────────────────────────────────────────────────
# GET /v1/providers
# ─────────────────────────────────────────────────────────────────────


@provider_router.get(
    "",
    summary="按分类列出全部厂商（推荐/全部/本地/国内/国外/聚合/自定义）",
)
async def list_providers(
    category: Optional[str] = Query(
        None,
        description=(
            "分类:recommended/all/local/cn/global/gateway/custom；空或 'all' 返回全部"
        ),
    ),
    _user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    """复用 ``admin/model_platforms/catalog`` 的合并逻辑（catalog seed + DB），
    在内存里按 ``tags`` 过滤分类。"""
    # 直接调底层 service 而不是路由 handler，避免 FastAPI 依赖注入循环
    from chayuan.server.api_server.admin_routes import (
        list_model_platform_catalog,
    )

    raw = await list_model_platform_catalog(_user=_user)
    items: List[Dict[str, Any]] = list(raw.get("data") or [])

    cat = (category or "").strip().lower()
    if not cat or cat == "all":
        return {"code": 0, "data": items}

    if cat == "recommended":
        out = [
            e
            for e in items
            if "推荐" in (e.get("tags") or [])
            or e.get("pid") in _EXTRA_RECOMMENDED_PIDS
        ]
        return {"code": 0, "data": out}

    if cat == "custom":
        out = [
            e
            for e in items
            if e.get("_custom") or "自定义" in (e.get("tags") or [])
        ]
        return {"code": 0, "data": out}

    tag = _CATEGORY_TAG_MAP.get(cat)
    if tag is None:
        raise HTTPException(400, f"unknown category: {category!r}")
    out = [e for e in items if tag in (e.get("tags") or [])]
    return {"code": 0, "data": out}


# ─────────────────────────────────────────────────────────────────────
# POST / PATCH / DELETE
# ─────────────────────────────────────────────────────────────────────


@provider_router.post(
    "",
    summary="新增厂商（自定义 OpenAI 兼容 endpoint 等）",
)
async def create_provider(
    body: _ProviderBody,
    user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    name = _resolve_name(body)
    if not name:
        raise HTTPException(400, "platform_name (or pid) 必填")
    from chayuan.server.db.repository.model_platform_repository import (
        create_platform,
    )
    try:
        data = create_platform(
            platform_name=str(name).strip(),
            fields=_body_to_fields(body),
            created_by=(user or {}).get("username") if isinstance(user, dict) else None,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"code": 0, "data": data}


@provider_router.patch(
    "/{pid}",
    summary="修改厂商任意字段（保存即生效，无需重启）",
)
async def patch_provider(
    pid: str,
    body: _ProviderBody,
    user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    fields = _body_to_fields(body)
    if not fields:
        raise HTTPException(400, "nothing to patch")
    from chayuan.server.db.repository.model_platform_repository import (
        upsert_platform,
    )
    try:
        data = upsert_platform(
            platform_name=pid,
            fields=fields,
            by=(user or {}).get("username") if isinstance(user, dict) else None,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"code": 0, "data": data}


@provider_router.delete(
    "/{pid}",
    summary="删除厂商 DB 行（catalog seed 仍可作为 fallback 兜底显示）",
)
async def delete_provider(
    pid: str,
    _user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    from chayuan.server.db.repository.model_platform_repository import (
        delete_platform,
    )
    ok = delete_platform(pid)
    return {"code": 0 if ok else 1, "msg": "ok" if ok else "not found"}


# ─────────────────────────────────────────────────────────────────────
# 连通性测试 / 拉取模型列表
# ─────────────────────────────────────────────────────────────────────


def _probe_provider(pid: str) -> Dict[str, Any]:
    """探测厂商连通性，返回 detected 模型分类清单。

    拷自 ``admin_routes.test_model_platform`` 的同步主体；分离出来便于被
    ``test`` 与 ``refresh-models`` 两个路由复用。
    """
    from chayuan.server.utils import (
        detect_ollama_models,
        detect_xf_models,
        get_all_platforms_with_state,
        get_base_url,
        get_config_platforms,
    )

    plat = get_config_platforms().get(pid)
    if not plat:
        # 未启用的也允许测试：从全集里取一份
        for d in get_all_platforms_with_state():
            if d.get("platform_name") == pid:
                plat = d
                break
    if not plat:
        raise HTTPException(404, f"provider {pid!r} not found")

    ptype = (plat.get("platform_type") or "").lower()
    api_base = plat.get("api_base_url") or ""
    detected: Dict[str, List[str]] = {}
    error: Optional[str] = None

    try:
        if ptype == "ollama":
            try:
                detect_ollama_models.cache_clear()  # type: ignore[attr-defined]
            except AttributeError:
                pass
            detected = detect_ollama_models(api_base)
        elif ptype == "xinference":
            try:
                detect_xf_models.cache_clear()  # type: ignore[attr-defined]
            except AttributeError:
                pass
            detected = detect_xf_models(get_base_url(api_base))
        else:
            # OpenAI 兼容（含 Anthropic / DeepSeek / 通义等）：用 sync openai 客户端探一下
            import openai  # noqa: WPS433

            client = openai.Client(
                base_url=api_base,
                api_key=plat.get("api_key") or "EMPTY",
                timeout=8.0,
            )
            resp = client.models.list()
            ids = [m.id for m in (resp.data or [])]
            detected = {"llm_models": ids}
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    ok = error is None
    return {
        "platform_name": pid,
        "platform_type": ptype,
        "api_base_url": api_base,
        "reachable": ok,
        "detected": detected,
        "error": error,
    }


@provider_router.post(
    "/{pid}/test",
    summary="探测厂商连通性 + 返回 detected 模型清单（不改任何配置）",
)
async def test_provider(
    pid: str,
    _user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    data = _probe_provider(pid)
    return {
        "code": 0 if data["reachable"] else 1,
        "msg": "ok" if data["reachable"] else (data["error"] or "unreachable"),
        "data": data,
    }


@provider_router.post(
    "/{pid}/refresh-models",
    summary="拉一次该厂商可用模型清单（同 test，但只返 detected）",
)
async def refresh_provider_models(
    pid: str,
    _user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    data = _probe_provider(pid)
    return {
        "code": 0 if data["reachable"] else 1,
        "msg": "ok" if data["reachable"] else (data["error"] or "unreachable"),
        "data": {
            "platform_name": pid,
            "detected": data["detected"],
        },
    }


# ─────────────────────────────────────────────────────────────────────
# 模型列表 / 启停 / 别名
# ─────────────────────────────────────────────────────────────────────


_MODEL_FIELDS = (
    "llm_models",
    "embed_models",
    "text2image_models",
    "image2text_models",
    "rerank_models",
    "speech2text_models",
    "text2speech_models",
)


@provider_router.get(
    "/{pid}/models",
    summary="返回该厂商当前已选模型分组 + disabled_models 黑名单",
)
async def list_provider_models(
    pid: str,
    _user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    from chayuan.server.utils import get_all_platforms_with_state

    plat: Optional[Dict[str, Any]] = None
    for d in get_all_platforms_with_state() or []:
        if d.get("platform_name") == pid:
            plat = d
            break
    if not plat:
        raise HTTPException(404, f"provider {pid!r} not found")

    grouped: Dict[str, List[str]] = {f: list(plat.get(f) or []) for f in _MODEL_FIELDS}
    disabled = list(plat.get("disabled_models") or [])
    return {
        "code": 0,
        "data": {
            "platform_name": pid,
            "models": grouped,
            "disabled_models": disabled,
        },
    }


class _ModelPatchBody(BaseModel):
    enabled: Optional[bool] = None  # 启停（写 disabled_models）
    # 后续可扩展：alias / capability 标签等


@provider_router.patch(
    "/{pid}/models/{model_id}",
    summary="启停单个模型（写厂商的 disabled_models 黑名单）",
)
async def patch_provider_model(
    pid: str,
    model_id: str,
    body: _ModelPatchBody,
    user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    if body.enabled is None:
        raise HTTPException(400, "nothing to patch")

    from chayuan.server.db.repository.model_platform_repository import (
        upsert_platform,
    )
    from chayuan.server.utils import get_all_platforms_with_state

    plat: Optional[Dict[str, Any]] = None
    for d in get_all_platforms_with_state() or []:
        if d.get("platform_name") == pid:
            plat = d
            break
    if not plat:
        raise HTTPException(404, f"provider {pid!r} not found")

    disabled = set(plat.get("disabled_models") or [])
    if body.enabled:
        disabled.discard(model_id)
    else:
        disabled.add(model_id)

    data = upsert_platform(
        platform_name=pid,
        fields={"disabled_models": sorted(disabled)},
        by=(user or {}).get("username") if isinstance(user, dict) else None,
    )
    return {"code": 0, "data": data}
