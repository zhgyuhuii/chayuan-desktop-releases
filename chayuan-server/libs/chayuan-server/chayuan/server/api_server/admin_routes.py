"""Admin 代理：把 Langfuse read-only API 暴露给前端管理员。

设计：
- 客户端**绝不**持有 Langfuse secret key；所有读操作经此处代理。
- httpx.AsyncClient 全局单例（复用连接池）；首次惰性建立。
- TTL 内存缓存 + asyncio.Lock 防 cache stampede（多并发同 key 只查一次）。
- 鉴权：`require_role("admin")` 二级守卫；非 admin 直接 403。
- 失败软降级：Langfuse 不可达返回 `{code:1, msg:..., data:[]}`，UI 不崩。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from chayuan.server.auth.deps import (
    get_current_user_optional,
    require_auth_enabled,
    require_role,
)


logger = logging.getLogger("chayuan.api.admin")

admin_router = APIRouter(prefix="/admin", tags=["Admin / Observability proxy"])


# ── HTTP / cache 全局单例 ──────────────────────────────────────────


_http_client: Optional[httpx.AsyncClient] = None
_http_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is not None:
        return _http_client
    async with _http_lock:
        if _http_client is None:
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=3.0),
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
    return _http_client


# 单进程内存 TTL 缓存（多 worker 时各自独立；够用，无 Redis 依赖）
_cache: Dict[str, Tuple[float, Any]] = {}
_cache_locks: Dict[str, asyncio.Lock] = {}


def _now() -> float:
    return time.monotonic()


async def _cached(key: str, ttl: float, loader):
    hit = _cache.get(key)
    if hit and (_now() - hit[0]) < ttl:
        return hit[1]
    lock = _cache_locks.setdefault(key, asyncio.Lock())
    async with lock:
        # 双检：进入临界区后可能已被他人填好
        hit = _cache.get(key)
        if hit and (_now() - hit[0]) < ttl:
            return hit[1]
        value = await loader()
        _cache[key] = (_now(), value)
        return value


def _lf_settings() -> Dict[str, str]:
    """Langfuse 接入参数；优先环境变量，其次 settings 配置（可扩展）。"""
    host = os.getenv("LANGFUSE_HOST", "").rstrip("/")
    public = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret = os.getenv("LANGFUSE_SECRET_KEY", "")
    project = os.getenv("LANGFUSE_PROJECT_ID", "")
    return {"host": host, "public": public, "secret": secret, "project": project}


def _envelope(code: int, msg: str = "", data: Any = None) -> Dict[str, Any]:
    return {"code": code, "msg": msg, "data": data}


async def _lf_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    s = _lf_settings()
    if not s["host"] or not s["public"] or not s["secret"]:
        raise HTTPException(status_code=503, detail="langfuse not configured")
    url = f"{s['host']}{path}"
    client = await _get_client()
    auth = (s["public"], s["secret"])
    r = await client.get(url, params=params, auth=auth)
    if r.status_code >= 400:
        logger.warning("langfuse %s %s -> %s %s", r.request.method, url, r.status_code, r.text[:200])
        raise HTTPException(status_code=502, detail=f"langfuse upstream {r.status_code}")
    return r.json()


# ── Endpoints ──────────────────────────────────────────────────────


@admin_router.get(
    "/lf/traces",
    summary="[admin] 列出 Langfuse trace",
    dependencies=[Depends(require_role("admin"))],
)
async def list_traces(
    user_id: Optional[str] = Query(None, description="按 user 过滤"),
    session_id: Optional[str] = Query(None, description="按 conversation 过滤"),
    name: Optional[str] = Query(None, description="trace name；如 'chat'"),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": limit, "page": page}
    if user_id:
        params["userId"] = user_id
    if session_id:
        params["sessionId"] = session_id
    if name:
        params["name"] = name
    cache_key = f"lf.traces:{sorted(params.items())}"

    async def _load() -> Dict[str, Any]:
        try:
            data = await _lf_get("/api/public/traces", params=params)
            return _envelope(0, "ok", data)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("traces fetch failed")
            return _envelope(1, str(exc), {"data": [], "meta": {"totalItems": 0}})

    return await _cached(cache_key, ttl=10.0, loader=_load)


@admin_router.get(
    "/lf/traces/{trace_id}",
    summary="[admin] 单条 trace 详情",
    dependencies=[Depends(require_role("admin"))],
)
async def trace_detail(trace_id: str) -> Dict[str, Any]:
    async def _load() -> Dict[str, Any]:
        try:
            data = await _lf_get(f"/api/public/traces/{trace_id}")
            return _envelope(0, "ok", data)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            return _envelope(1, str(exc), None)

    return await _cached(f"lf.trace:{trace_id}", ttl=30.0, loader=_load)


@admin_router.get(
    "/lf/scores",
    summary="[admin] 列出最近 scores（聚合反馈）",
    dependencies=[Depends(require_role("admin"))],
)
async def list_scores(
    name: Optional[str] = Query(None, description="如 user_feedback / aborted"),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": limit}
    if name:
        params["name"] = name
    cache_key = f"lf.scores:{sorted(params.items())}"

    async def _load() -> Dict[str, Any]:
        try:
            data = await _lf_get("/api/public/scores", params=params)
            return _envelope(0, "ok", data)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            return _envelope(1, str(exc), {"data": [], "meta": {"totalItems": 0}})

    return await _cached(cache_key, ttl=15.0, loader=_load)


@admin_router.get(
    "/prompts",
    summary="[admin] 列出 Langfuse 中可用 prompt",
    dependencies=[Depends(require_role("admin"))],
)
async def list_prompts(
    label: Optional[str] = Query(None, description="按 label 过滤；如 production"),
    limit: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"limit": limit}
    if label:
        params["label"] = label
    cache_key = f"lf.prompts:{sorted(params.items())}"

    async def _load() -> Dict[str, Any]:
        try:
            data = await _lf_get("/api/public/v2/prompts", params=params)
            return _envelope(0, "ok", data)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            return _envelope(1, str(exc), {"data": [], "meta": {"totalItems": 0}})

    return await _cached(cache_key, ttl=60.0, loader=_load)


@admin_router.get(
    "/prompts/{name}",
    summary="[admin] 单个 prompt 的所有版本",
    dependencies=[Depends(require_role("admin"))],
)
async def prompt_detail(
    name: str,
    version: Optional[int] = Query(None, ge=1, description="不传则返回最新 production label"),
) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if version is not None:
        params["version"] = version

    async def _load() -> Dict[str, Any]:
        try:
            data = await _lf_get(f"/api/public/v2/prompts/{name}", params=params)
            return _envelope(0, "ok", data)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            return _envelope(1, str(exc), None)

    return await _cached(f"lf.prompt:{name}:v={version}", ttl=60.0, loader=_load)


@admin_router.get(
    "/health",
    summary="[admin] Langfuse 可达性自检",
    dependencies=[Depends(require_role("admin"))],
)
async def health() -> Dict[str, Any]:
    s = _lf_settings()
    if not s["host"]:
        return _envelope(1, "LANGFUSE_HOST not set", {"reachable": False})
    try:
        client = await _get_client()
        r = await client.get(f"{s['host']}/api/public/health", timeout=3.0)
        return _envelope(
            0 if r.status_code < 400 else 1,
            "ok" if r.status_code < 400 else f"http {r.status_code}",
            {"reachable": r.status_code < 400, "status": r.status_code},
        )
    except Exception as exc:  # noqa: BLE001
        return _envelope(1, str(exc), {"reachable": False})


# ── 模型平台:admin 在线开关 ──────────────────────────────────────


from pydantic import BaseModel as _BaseModel  # noqa: E402


class _PlatformBody(_BaseModel):
    """全字段表单——POST 创建 / PATCH 更新都用这个 schema，可选项 None=不改。"""
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


def _body_to_fields(body: _PlatformBody) -> Dict[str, Any]:
    """把 pydantic body 转成 repository 接受的 dict（None 字段省略）。"""
    raw = body.model_dump(exclude_unset=True)
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


@admin_router.get(
    "/model_platforms",
    summary="[auth] 列出全部模型平台 + 当前 enabled / 黑名单状态",
)
async def list_model_platforms(
    _user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    """返回全部平台（含禁用的），合并 yaml seed + DB + JSON overrides。

    鉴权放宽给所有登录用户(配合"模型广场"全员可见 + 全员可编辑);
    api_key 仍然只在该端点回显给已配置过的厂商,游客 401。
    """
    from chayuan.server.utils import get_all_platforms_with_state
    return {"code": 0, "msg": "ok", "data": get_all_platforms_with_state()}


@admin_router.get(
    "/model_platforms/catalog",
    summary="[public] 全量厂商目录 + 当前配置态(国内/国外/本地/聚合/推荐 标签 + 是否已配置/启用)",
)
async def list_model_platform_catalog(
    _user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    """合并 PROVIDER_CATALOG(~60 家)与 DB 实际配置态,前端用于类别 tabs 渲染。

    鉴权: 放宽给所有访问者(含游客),用于"模型广场"展示;api_key 永不返出后端,
    api_base_url 也只暴露已配置过的那一份(等同于 DB 的 platform_name 公开属性)。

    每条返回:
      pid / display_name / platform_type / default_api_base / logo / color / tags
      configured(已经填了 base_url 或 api_key) / enabled(总开关) / api_base_url(去掩码)
      llm_models(已选清单)/ embed_models / ... / disabled_models
      default_models(catalog 内置的"该厂商主流模型"清单,DB 没数据时回退展示用)
      _custom=true 表示 catalog 没有但 DB 里有(用户自己加的)
    """
    from chayuan.server.config_panel.model_config import (
        PROVIDER_CATALOG, model_metadata_seed_for,
    )
    from chayuan.server.db.repository import model_metadata_repository as md_repo
    from chayuan.server.utils import get_all_platforms_with_state

    db_rows: Dict[str, Dict[str, Any]] = {
        r["platform_name"]: r for r in get_all_platforms_with_state() or []
    }

    # 一次拉全 model_metadata,在内存里按 (platform_name -> {model_id -> meta}) 索引,
    # 避免每个 entry 各自查 DB 的 N+1。失败软降级为空 dict,UI 展示仍可用。
    md_index: Dict[str, Dict[str, Dict[str, Any]]] = {}
    try:
        for row in md_repo.list_all() or []:
            pn = row.get("platform_name") or ""
            mid = row.get("model_id") or ""
            if not pn or not mid:
                continue
            md_index.setdefault(pn, {})[mid] = row
    except Exception as e:  # noqa: BLE001
        logger.warning("model_metadata join failed; degrading: %r", e)

    out: List[Dict[str, Any]] = []
    seen: set = set()

    _MODEL_FIELDS = (
        "llm_models", "embed_models", "text2image_models", "image2text_models",
        "rerank_models", "speech2text_models", "text2speech_models",
    )

    def _merge_metadata(
        seed: Dict[str, Dict[str, Any]],
        db: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """seed + DB 合并 — DB 有值的字段覆盖 seed,没值的回落 seed;两边都没的字段留空。

        seed 形态: {model_id: {description, release_date, context_length, performance_note}}
        db 形态:  {model_id: ModelMetadataModel.to_dict()}(同字段,外加 source / update_time 等)

        合并粒度到字段级,因为 DB 行可能只有 description 是 LLM 写的,其它仍要看 seed。
        """
        out: Dict[str, Dict[str, Any]] = {}
        for mid, s in seed.items():
            out[mid] = {
                "platform_name": "",
                "model_id": mid,
                "description": s.get("description") or "",
                "release_date": s.get("release_date") or "",
                "context_length": s.get("context_length"),
                "performance_note": s.get("performance_note") or "",
                "source": "seed",
                "source_model": "",
            }
        for mid, d in db.items():
            base = out.setdefault(mid, {
                "platform_name": d.get("platform_name") or "",
                "model_id": mid,
                "description": "", "release_date": "",
                "context_length": None, "performance_note": "",
                "source": "llm", "source_model": "",
            })
            # 字段级覆盖:DB 有值才覆盖 seed
            if d.get("description"):
                base["description"] = d["description"]
            if d.get("release_date"):
                base["release_date"] = d["release_date"]
            if d.get("context_length"):
                base["context_length"] = d["context_length"]
            if d.get("performance_note"):
                base["performance_note"] = d["performance_note"]
            if d.get("source"):
                base["source"] = d["source"]
            if d.get("source_model"):
                base["source_model"] = d["source_model"]
            base["platform_name"] = d.get("platform_name") or base["platform_name"]
        return out

    def _entry(meta_pid: str, display_name: str, platform_type: str,
               default_api_base: str, logo: str, color: str,
               tags: List[str], icon: str, custom: bool,
               apply_key_url: str = "", install_guide_url: str = "",
               website_url: str = "",
               default_models: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        row = db_rows.get(meta_pid) or {}
        # 不要把 raw api_key 返出去;只标 configured 与否
        api_key = (row.get("api_key") or "").strip()
        api_base = (row.get("api_base_url") or "").strip()
        configured = bool(api_key or api_base)
        defaults = default_models or {}

        # 65 题:初始化时下拉**应该是空的**,只有用户配置过厂商才显示模型清单
        # 之前未配置时会 fallback 到 catalog 内置 default_models,导致下拉一开始
        # 就有数据 — 用户原话"初始时不放任何数据进来"。现在只返 DB 实际数据。
        # ``default_models`` 字段独立返(下方 ``"default_models": ...``),客户端
        # 如需"未配置时建议清单"提示可单独读这个字段,不再混入 *_models。
        def _models_for(field: str) -> List[str]:
            return list(row.get(field) or [])

        return {
            "pid": meta_pid,
            "display_name": display_name,
            "platform_type": platform_type,
            "default_api_base": default_api_base,
            "logo": logo,
            "color": color,
            "tags": list(tags),
            "icon": icon,
            "configured": configured,
            "enabled": bool(row.get("enabled", False)),
            "api_base_url": api_base,
            "api_concurrencies": int(row.get("api_concurrencies") or 5),
            "auto_detect_model": bool(row.get("auto_detect_model") or False),
            **{f: _models_for(f) for f in _MODEL_FIELDS},
            "disabled_models": list(row.get("disabled_models") or []),
            "description": row.get("description") or "",
            # 厂商外部链接;UI 侧据此渲染"申请 Key" / "安装指南" / "官网"按钮
            "apply_key_url": apply_key_url,
            "install_guide_url": install_guide_url,
            "website_url": website_url,
            # catalog 内置的默认模型清单(只读,前端用于显示"未配置时也能看到的清单"提示)
            "default_models": {k: list(v) for k, v in defaults.items()},
            # 该厂商所有模型的元信息 ── DB(model_metadata 表)优先 > seed(model_config 内置)。
            # seed 是开箱可用的 30+ 主流模型简介快照,DB 行由 enrich 端点写入(可覆盖 seed)。
            # 前端 ProviderCard chip hover 时直接 lookup,无需再多打一发请求。
            "model_metadata": _merge_metadata(
                model_metadata_seed_for(meta_pid),
                md_index.get(meta_pid, {}),
            ),
            "_custom": custom,
        }

    # 1) 按 catalog 顺序输出(已被推荐 / 国内 / 国外 / 本地 / 聚合 顺序铺好)
    for meta in PROVIDER_CATALOG:
        out.append(_entry(
            meta.pid, meta.display_name, meta.platform_type,
            meta.default_api_base, meta.logo, meta.color,
            list(meta.tags), meta.icon, custom=False,
            apply_key_url=meta.apply_key_url,
            install_guide_url=meta.install_guide_url,
            website_url=meta.website_url,
            default_models=meta.default_models,
        ))
        seen.add(meta.pid)

    # 2) DB 里有但 catalog 没有的 → 自定义厂商,补在最后,标 "自定义"
    for name, row in db_rows.items():
        if name in seen:
            continue
        out.append(_entry(
            name, name, row.get("platform_type") or "openai",
            row.get("api_base_url") or "", "", "#6b7280",
            ["自定义"], "", custom=True,
        ))

    return {"code": 0, "msg": "ok", "data": out}


@admin_router.post(
    "/model_platforms",
    summary="[auth] 新建模型平台（落库；保存即生效，无需重启）",
)
async def create_model_platform(
    body: _PlatformBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    if not body.platform_name:
        raise HTTPException(400, "platform_name 必填")
    from chayuan.server.db.repository.model_platform_repository import (
        create_platform,
    )
    try:
        data = create_platform(
            platform_name=body.platform_name.strip(),
            fields=_body_to_fields(body),
            created_by=(user or {}).get("username") if isinstance(user, dict) else None,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"code": 0, "msg": "ok", "data": data}


@admin_router.patch(
    "/model_platforms/{name}",
    summary="[auth] 更新模型平台（任意字段；保存即生效，无需重启）",
)
async def patch_model_platform(
    name: str,
    body: _PlatformBody,
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    """优先写入 ``model_platform`` DB 表（落库 + 立即热生效）。"""
    fields = _body_to_fields(body)
    if not fields:
        raise HTTPException(status_code=400, detail="nothing to patch")
    from chayuan.server.db.repository.model_platform_repository import (
        upsert_platform,
    )
    try:
        data = upsert_platform(
            platform_name=name,
            fields=fields,
            by=(user or {}).get("username") if isinstance(user, dict) else None,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"code": 0, "msg": "ok", "data": data}


@admin_router.delete(
    "/model_platforms/{name}",
    summary="[auth] 删除模型平台 DB 行（yaml seed 仍可作为 fallback 兜底）",
)
async def delete_model_platform(
    name: str, _=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    from chayuan.server.db.repository.model_platform_repository import (
        delete_platform,
    )
    ok = delete_platform(name)
    return {"code": 0 if ok else 1, "msg": "ok" if ok else "not found"}


@admin_router.post(
    "/model_platforms/{name}/test",
    summary="[auth] 探测平台连通性 + 拉一下可用模型（不改任何配置）",
)
async def test_model_platform(
    name: str, _=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    """对 ollama / xinference / 任意 OpenAI 兼容平台，做一次连通探测。

    - ollama → GET /api/tags
    - xinference → GET /v1/models
    - 其他 → openai client.models.list() 探一下
    """
    from chayuan.server.utils import (
        detect_ollama_models,
        detect_xf_models,
        get_base_url,
        get_config_platforms,
    )
    plat = get_config_platforms().get(name)
    if not plat:
        # 未启用的也允许测试：从全集里取一份
        from chayuan.server.utils import get_all_platforms_with_state
        for d in get_all_platforms_with_state():
            if d.get("platform_name") == name:
                plat = d
                break
    if not plat:
        raise HTTPException(404, f"platform '{name}' not found")

    ptype = (plat.get("platform_type") or "").lower()
    api_base = plat.get("api_base_url") or ""
    detected: Dict[str, List[str]] = {}
    error: Optional[str] = None
    try:
        if ptype == "ollama":
            detect_ollama_models.cache_clear()  # type: ignore[attr-defined]
            detected = detect_ollama_models(api_base)
        elif ptype == "xinference":
            detect_xf_models.cache_clear()  # type: ignore[attr-defined]
            detected = detect_xf_models(get_base_url(api_base))
        else:
            # OpenAI 兼容：用 sync openai 客户端探一下 /models
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
        "code": 0 if ok else 1,
        "msg": "ok" if ok else error,
        "data": {
            "platform_name": name,
            "platform_type": ptype,
            "api_base_url": api_base,
            "reachable": ok,
            "detected": detected,
            "error": error,
        },
    }


# ── 模型元数据(LLM 自动补全)─────────────────────────────────────


class _EnrichBody(_BaseModel):
    """``POST /admin/model_platforms/{name}/enrich`` 的请求体。

    - ``models`` 为空 = 默认拉这个厂商当前 DB 行里所有 *_models 的并集再去重。
    - 给定 list 时,只补这些 model_id;前端可用于"勾选哪些去 AI 生成"。
    """
    models: Optional[List[str]] = None


@admin_router.post(
    "/model_platforms/{name}/enrich",
    summary="[auth] 用 LLM 给指定模型 id 生成简介/发布日期/上下文长度/性能短评 → 落 model_metadata",
)
async def enrich_model_platform(
    name: str,
    body: _EnrichBody,
    _=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    """对一组 model_id 调一次内置 LLM 厂商的对话接口,产出 JSON,落库。

    选 LLM 的策略见 ``_model_enrich._pick_llm_for_enrich``:
    优先 deepseek/zhipu/openai 等已 enabled 且配齐 key 的厂商;一个都没有就 503。
    """
    from chayuan.server.api_server._model_enrich import enrich_models_with_llm
    from chayuan.server.config_panel.model_config import _PROVIDER_BY_ID
    from chayuan.server.utils import get_all_platforms_with_state

    # 找平台 & display_name(catalog 里有就用 catalog 的中文名)
    target_meta = _PROVIDER_BY_ID.get(name)
    target_display = target_meta.display_name if target_meta else name

    # 模型清单:优先用 body.models;否则取该平台 DB 行里所有 *_models 并集
    models = list(dict.fromkeys((body.models or [])))
    if not models:
        for d in get_all_platforms_with_state():
            if d.get("platform_name") != name:
                continue
            for f in (
                "llm_models", "embed_models", "rerank_models", "text2image_models",
                "image2text_models", "speech2text_models", "text2speech_models",
            ):
                for m in (d.get(f) or []):
                    if m and m not in models:
                        models.append(m)
            break

    if not models:
        raise HTTPException(400, '请先在该厂商的"模型清单"里添加 model id 再用 AI 补全')

    try:
        result = await enrich_models_with_llm(
            target_platform=name,
            target_display_name=target_display,
            models=models,
        )
    except RuntimeError as e:
        # _pick_llm_for_enrich 找不到可用 LLM 厂商
        raise HTTPException(
            status_code=503,
            detail=str(e) or "no LLM platform available; 请先配置任意一个有 LLM 的厂商再用 AI 补全",
        ) from e
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("enrich %s failed", name)
        raise HTTPException(502, f"LLM 调用失败: {type(e).__name__}: {e}") from e

    return {"code": 0, "msg": "ok", "data": result}


@admin_router.post(
    "/model_platforms/{name}/describe/stream",
    summary="[auth] 用 LLM 流式生成厂商简介(用于设置弹窗的描述字段)",
)
async def describe_model_platform_stream(
    name: str,
    _=Depends(require_auth_enabled()),
) -> StreamingResponse:
    """SSE 流式输出厂商简介。

    协议(text/event-stream):
      - 每个 token: ``event: chunk`` + ``data: <json {"text": "..."}>`` + 空行
      - 流结束:    ``event: done``  + ``data: {}`` + 空行
      - 出错:      ``event: error`` + ``data: <json {"message": "..."}>`` + 空行

    前端: ``modelPlatform.describeStream`` 消费,把每个 chunk.text 拼接到 description Input。
    """
    import json as _json

    from chayuan.server.api_server._platform_describe_stream import (
        describe_platform_stream,
    )
    from chayuan.server.config_panel.model_config import _PROVIDER_BY_ID

    target_meta = _PROVIDER_BY_ID.get(name)
    target_display = target_meta.display_name if target_meta else name

    async def _gen():
        try:
            async for piece in describe_platform_stream(
                target_platform=name,
                target_display_name=target_display,
            ):
                # 每个 chunk 用独立 SSE frame;data 必须是单行 JSON,不含裸换行
                yield (
                    "event: chunk\n"
                    f"data: {_json.dumps({'text': piece}, ensure_ascii=False)}\n\n"
                )
            yield "event: done\ndata: {}\n\n"
        except RuntimeError as e:
            # 找不到可用 LLM
            yield (
                "event: error\n"
                f"data: {_json.dumps({'message': str(e), 'code': 503}, ensure_ascii=False)}\n\n"
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("describe stream %s failed", name)
            yield (
                "event: error\n"
                f"data: {_json.dumps({'message': f'{type(e).__name__}: {e}', 'code': 502}, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            # 避免代理 buffering 把流憋住;Tauri webview 不会经过代理,保险起见仍设
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@admin_router.post(
    "/home/intro/stream",
    summary="[public] 用 LLM 流式生成首页产品介绍(每次不同视角)",
)
async def home_intro_stream_route() -> StreamingResponse:
    """SSE 流式输出察元 AI 产品介绍(150 字内)。

    协议同 ``/admin/model_platforms/{name}/describe/stream``:
      - ``event: chunk`` + ``data: {"text": "..."}`` 每个 token
      - ``event: done`` + ``data: {}``                 流结束
      - ``event: error`` + ``data: {"message": "..."}`` 出错(LLM 不可用 / 失败)

    前端 ``home.introStream`` 消费;LLM 不可用时前端会捕获 error 事件并 fallback
    到本地随机静态文案,所以本端点对 503 / 502 都直接 emit error 即可。
    """
    import json as _json

    from chayuan.server.api_server._home_intro_stream import home_intro_stream

    async def _gen():
        try:
            async for piece in home_intro_stream():
                yield (
                    "event: chunk\n"
                    f"data: {_json.dumps({'text': piece}, ensure_ascii=False)}\n\n"
                )
            yield "event: done\ndata: {}\n\n"
        except RuntimeError as e:
            yield (
                "event: error\n"
                f"data: {_json.dumps({'message': str(e), 'code': 503}, ensure_ascii=False)}\n\n"
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("home intro stream failed")
            yield (
                "event: error\n"
                f"data: {_json.dumps({'message': f'{type(e).__name__}: {e}', 'code': 502}, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@admin_router.get(
    "/model_platforms/{name}/metadata",
    summary="[public] 列出指定厂商已写入 model_metadata 的全部模型元信息",
)
async def list_model_metadata(
    name: str,
    _user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    """前端在 SettingsDialog / ProviderCard hover 时按需拉一份;catalog 端点也会
    一次性 join 全表数据,前端可在内存里直接索引。"""
    from chayuan.server.db.repository import model_metadata_repository as repo
    return {"code": 0, "msg": "ok", "data": repo.list_for_platform(name)}


# ── 一键安装 sibling 包 ────────────────────────────────────────────────
#
# 用户只装 chayuan-server,没装 chayuan-modelmgr / chayuan-runtime / chayuan-core
# 是常见误装。给个一键安装端点:UI 按钮直接调,后端跑 pip install -e libs/<name>。

@admin_router.post(
    "/install_sibling",
    summary="[admin] 一键安装 chayuan sibling 包 (modelmgr / runtime / core)",
)
async def install_sibling(
    name: str = Body("all", embed=True,
                     description="包名: modelmgr / runtime / core / all"),
    user=Depends(require_auth_enabled()),
) -> Dict[str, Any]:
    """对应 SettingsDialog → 模型配置 → 镜像源面板 的「一键修复」按钮。"""
    import sys
    from pathlib import Path

    valid = {"modelmgr", "runtime", "core", "all"}
    if name not in valid:
        raise HTTPException(400, f"不支持: {name}; 可选: {sorted(valid)}")

    # 仓库根:从本文件上溯找 libs/chayuan-modelmgr 目录
    here = Path(__file__).resolve()
    repo_root = None
    for parent in here.parents:
        if (parent / "libs" / "chayuan-modelmgr").is_dir():
            repo_root = parent
            break
    if repo_root is None:
        raise HTTPException(500, "找不到 libs/chayuan-modelmgr 目录;请手动安装")

    targets = (
        ["chayuan-modelmgr"] if name == "modelmgr"
        else ["chayuan-runtime"] if name == "runtime"
        else ["chayuan-core"] if name == "core"
        else ["chayuan-modelmgr", "chayuan-runtime", "chayuan-core"]
    )

    logs: List[str] = []
    failed: List[str] = []
    for pkg in targets:
        pkg_dir = repo_root / "libs" / pkg
        if not pkg_dir.is_dir():
            failed.append(f"{pkg}: 目录不存在 {pkg_dir}")
            continue
        cmd = [sys.executable, "-m", "pip", "install", "-e", str(pkg_dir)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            logs.append(raw.decode("utf-8", errors="replace").rstrip()[:300])
        rc = await proc.wait()
        if rc != 0:
            failed.append(f"{pkg}: rc={rc}")
    return {
        "code": 0 if not failed else 1,
        "data": {
            "installed": [t for t in targets if not any(f.startswith(t + ":") for f in failed)],
            "failed": failed,
            "log": logs[-50:],
        },
    }


# ── capability_defaults:9 类默认模型 (chat/embedding/clip/...) ─────────────
#
# 单机版没有 chayuan-gateway,gateway 的 /v1/admin/defaults 不可达。
# 这里给前端一个等价 shape 的服务端实现:capability_router 的 yaml 真源 +
# 已配置的全部 model_platform 汇总作为候选。

@admin_router.get(
    "/capability_defaults",
    summary="[public] 列出 9 类 capability 的默认模型 + 候选",
)
async def get_capability_defaults(
    _user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    """返回 ``{ capabilities, defaults, candidates }``,前端模型广场 / 设置页直接消费。

    candidates 来源:遍历 ``model_platform`` 表,按 ``_CAP_TO_GROUP_KEY``
    把每个平台对应字段的模型扁平化为 ``{id, runtime, format, size_bytes, is_default}``。
    """
    from chayuan.server.config_panel.runtime_framework_panel import (
        CAPABILITY_LABELS,
        _CAP_TO_YAML_KEY,
        _CAP_TO_GROUP_KEY,
        _load_capability_defaults,
        _save_capability_default,
    )
    from chayuan.server.db.repository import model_platform_repository as repo

    capabilities = [cap for cap, _ in CAPABILITY_LABELS]
    defaults_raw = _load_capability_defaults()  # {cap: model_id_or_""}
    defaults: Dict[str, Optional[str]] = {
        cap: (defaults_raw.get(cap) or None) for cap in capabilities
    }

    # 汇总所有平台的模型清单
    candidates: Dict[str, List[Dict[str, Any]]] = {cap: [] for cap in capabilities}
    try:
        platforms = repo.list_platforms() or []
    except Exception as e:  # noqa: BLE001
        logger.warning("[capability_defaults] list_platforms 失败: %r", e)
        platforms = []

    seen: Dict[str, set] = {cap: set() for cap in capabilities}
    # list_platforms() 返回 Dict[str, Any] 列表(ORM.to_dict),用 .get() 访问
    for p in platforms:
        if not p.get("enabled", True):
            continue
        platform_type = p.get("platform_type")
        platform_name = p.get("platform_name")
        for cap in capabilities:
            field = _CAP_TO_GROUP_KEY.get(cap)
            if not field:
                continue
            models = p.get(field) or []
            if isinstance(models, str):
                models = [m.strip() for m in models.split(",") if m.strip()]
            for mid in models:
                if not isinstance(mid, str) or not mid or mid in seen[cap]:
                    continue
                seen[cap].add(mid)
                candidates[cap].append({
                    "id": mid,
                    "runtime": platform_type,
                    # platform_name 用于前端按厂商分组(本地 sidecar 是 'local-<cap>',
                    # 云厂商是 'deepseek' / 'qwen' / 'openrouter' 等)
                    "platform_name": platform_name,
                    "format": None,
                    "size_bytes": 0,
                    "is_default": defaults.get(cap) == mid,
                })

    # 把 local_index 扫到的本地模型(layout.yaml 释放出来的 GGUF 等)合并进候选;
    # platforms 端的同名 model_id 优先,本地补齐前者覆盖不到的条目。
    # 详见 chayuan.server.model_registry.candidates_bridge 模块文档。
    try:
        from chayuan.server.model_registry.candidates_bridge import (
            merge_local_into_candidates,
        )
        merge_local_into_candidates(candidates, defaults=defaults)
    except Exception as e:  # noqa: BLE001
        logger.warning("[capability_defaults] merge local_index 失败: %r", e)

    # 自动落默认 — 用户在模型广场配好了厂商和模型,但还没去设置页里
    # 选「默认聊天/文本嵌入/图像嵌入/重排」时,把每个 cap 的第一个候选
    # promote 成默认并持久化。前端拿到的 defaults 已经填好,业务侧一调
    # ``_load_capability_defaults`` 也命中,无需用户多走一步。
    auto_assigned: List[str] = []
    for cap in capabilities:
        if defaults.get(cap):
            continue
        if not candidates[cap]:
            continue
        first_id = candidates[cap][0]["id"]
        try:
            ok, _msg = _save_capability_default(cap, first_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[capability_defaults] auto-assign %s failed: %r", cap, e)
            continue
        if not ok:
            continue
        defaults[cap] = first_id
        candidates[cap][0]["is_default"] = True
        auto_assigned.append(cap)
    if auto_assigned:
        logger.info(
            "[capability_defaults] auto-assigned first candidate as default: %s",
            ", ".join(f"{c}={defaults[c]}" for c in auto_assigned),
        )

    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "capabilities": capabilities,
            "defaults": defaults,
            "candidates": candidates,
            "auto_assigned": auto_assigned,
        },
    }


@admin_router.post(
    "/capability_defaults",
    summary="[admin] 批量设置 capability 默认模型",
)
async def set_capability_defaults(
    payload: Dict[str, str] = Body(...,
                                    description="{cap: model_id, ...},如 {chat: deepseek-chat}"),
    _user=Depends(get_current_user_optional),
) -> Dict[str, Any]:
    """每条独立写盘,失败逐条返回原因,不让一个错挡住其它写入。"""
    from chayuan.server.config_panel.runtime_framework_panel import (
        _save_capability_default,
    )

    results: Dict[str, Dict[str, Any]] = {}
    for cap, model_id in (payload or {}).items():
        if not isinstance(cap, str) or not isinstance(model_id, str):
            results[str(cap)] = {"ok": False, "error": "类型不合法"}
            continue
        ok, msg = _save_capability_default(cap, model_id)
        results[cap] = {"ok": ok, "model": model_id} if ok else {"ok": False, "error": msg}

    return {"code": 0, "msg": "ok", "data": {"results": results}}


# ── 模型库自检（首启向导 / 模型管理面板 / 健康检查）────────────────


@admin_router.get(
    "/models/bootstrap",
    summary="[gate] 模型库自检 — 必需 capability 是否都已就绪",
    tags=["Admin / Models"],
)
def admin_models_bootstrap(
    do_scan: bool = Query(True, description="是否先跑一次扫盘刷新本地索引"),
    required: Optional[str] = Query(
        None,
        description=("自定义必需 capability 列表（逗号分隔，例如 'chat,text-embedding'）。"
                     "留空则用默认 chat+text-embedding+rerank。"),
    ),
) -> Dict[str, Any]:
    """给 sidecar-gate 首启向导 + 前端模型管理面板用。

    返回 ``ready`` / ``missing`` / 每个 capability 的候选清单；缺则附加
    ``install_hints`` 提示该下哪个 release。

    这个端点**不**走 ``require_role("admin")``——sidecar-gate 在用户尚未登录
    时也要能拉到模型状态。端点本身只读，且不暴露 secret。
    """
    from chayuan.server.model_registry.bootstrap import (
        DEFAULT_REQUIRED,
        check_bootstrap,
    )
    from chayuan.server.model_registry.install_hints import build_install_hints

    if required:
        caps = tuple(c.strip() for c in required.split(",") if c.strip())
    else:
        caps = DEFAULT_REQUIRED

    report = check_bootstrap(required=caps, do_scan=do_scan)
    hints = build_install_hints(report.missing) if report.missing else []
    return {
        **report.to_dict(),
        "install_hints": [h.to_dict() for h in hints],
    }


# ── 模型下载编排 ──────────────────────────────────────────────────


@admin_router.post(
    "/models/install_release",
    summary="[gate] 触发后台下载 release(lite/standard/pro)",
    tags=["Admin / Models"],
)
def admin_models_install_release(
    payload: Dict[str, Any] = Body(
        ...,
        description=('{"release": "lite", "mirror": "hf-mirror"} '
                     'mirror 可选,白名单:hf-mirror / huggingface'),
    ),
) -> Dict[str, Any]:
    """启动一个后台任务跑 ``chayuan_packaging fetch + stage``。

    立即返回 job_id;前端轮询 ``GET /admin/models/install_release/{job_id}``
    拿状态。任务在 server 进程内 subprocess 跑,server 重启会丢失运行中任务
    (但 ``packaging/.cache`` 自带断点续传,下次请求会续跑)。
    """
    from chayuan.server.model_registry.install_job import get_install_manager

    release = str(payload.get("release") or "").strip()
    mirror = payload.get("mirror")
    mirror = str(mirror).strip() if mirror else None

    try:
        job = get_install_manager().start(release, mirror=mirror)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # 同 release 已有 running 任务 — 409 Conflict
        raise HTTPException(status_code=409, detail=str(e))
    return {"code": 0, "msg": "ok", "data": job.to_dict()}


@admin_router.get(
    "/models/install_release/{job_id}",
    summary="[gate] 查询下载任务状态",
    tags=["Admin / Models"],
)
def admin_models_install_release_status(job_id: str) -> Dict[str, Any]:
    """轮询任务状态。404 = 未知 job_id;200 = job dict。"""
    from chayuan.server.model_registry.install_job import get_install_manager

    job = get_install_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id!r}")
    return {"code": 0, "msg": "ok", "data": job.to_dict()}


@admin_router.get(
    "/models/install_release",
    summary="[gate] 列出最近的下载任务(倒序)",
    tags=["Admin / Models"],
)
def admin_models_install_release_list(
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    from chayuan.server.model_registry.install_job import get_install_manager

    items = get_install_manager().list()[:limit]
    return {"code": 0, "msg": "ok",
            "data": {"items": [j.to_dict() for j in items], "total": len(items)}}


@admin_router.post(
    "/models/install_release/{job_id}/cancel",
    summary="[gate] 取消下载任务",
    tags=["Admin / Models"],
)
def admin_models_install_release_cancel(job_id: str) -> Dict[str, Any]:
    """请求中止任务。已是终态的 job 返回原状态(幂等)。"""
    from chayuan.server.model_registry.install_job import get_install_manager

    try:
        job = get_install_manager().cancel(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id!r}")
    return {"code": 0, "msg": "ok", "data": job.to_dict()}


# ── 按 model_id 单个模型下载(本地模型服务行内"下载模型"入口) ────────
# 跟 install_release(下整包)对应,这里是给用户的"我在 modelscope 看到一个
# 模型想装到本地"的入口。详见 install_model.py 模块 docstring。


@admin_router.post(
    "/models/install_model",
    summary="[gate] 按 model_id 下载单个模型到 bundled/<cap>/",
    tags=["Admin / Models"],
)
def admin_models_install_model(
    payload: Dict[str, Any] = Body(
        ...,
        description=(
            '{"source": "modelscope|huggingface", "model_id": "owner/name", '
            '"capability": "chat|embedding|rerank|asr|image-embedding|...", '
            '"specific_file": "Qwen3-4B-Q4_K_M.gguf"(可选,GGUF 多 quant 仓挑一个)}'
        ),
    ),
) -> Dict[str, Any]:
    """启动后台线程跑 modelscope/huggingface SDK 下载到 ``<CHAYUAN_ROOT>/models/bundled/<cap>/``。

    完成后:
      1. ``scan_once`` 自动识别新模型
      2. 对应 cap 的本地 runtime 如果还没起,自动 ``start_capability`` 触发
      3. 前端轮询 ``GET /admin/models/install_model/{job_id}`` 看进度
    """
    from chayuan.server.model_registry.install_model import get_install_model_manager

    source = str(payload.get("source") or "modelscope").strip()
    model_id = str(payload.get("model_id") or "").strip()
    capability = str(payload.get("capability") or "").strip()
    specific_file = payload.get("specific_file")
    specific_file = str(specific_file).strip() if specific_file else None

    try:
        job = get_install_model_manager().start(
            source=source,
            model_id=model_id,
            capability=capability,
            specific_file=specific_file,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"code": 0, "msg": "ok", "data": job.to_dict()}


@admin_router.get(
    "/models/install_model/{job_id}",
    summary="[gate] 查询 install_model 任务状态",
    tags=["Admin / Models"],
)
def admin_models_install_model_status(job_id: str) -> Dict[str, Any]:
    from chayuan.server.model_registry.install_model import get_install_model_manager

    job = get_install_model_manager().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id!r}")
    return {"code": 0, "msg": "ok", "data": job.to_dict()}


@admin_router.post(
    "/models/install_model/{job_id}/cancel",
    summary="[gate] 取消 install_model 任务",
    tags=["Admin / Models"],
)
def admin_models_install_model_cancel(job_id: str) -> Dict[str, Any]:
    from chayuan.server.model_registry.install_model import get_install_model_manager

    try:
        job = get_install_model_manager().cancel(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown job_id {job_id!r}")
    return {"code": 0, "msg": "ok", "data": job.to_dict()}


@admin_router.post(
    "/models/scan_and_mount",
    summary="手动放好模型文件后:扫描 + 自动启动新发现的 cap",
    tags=["Admin / Models"],
)
def admin_models_scan_and_mount(
    payload: Optional[Dict[str, Any]] = Body(
        None,
        description=(
            '可选 {"capability": "chat|text-embedding|rerank|speech-to-text|'
            'image-embedding|..."}。传了就只扫该能力对应的 bundled 子目录,'
            'auto_started / summary 也只反映该能力;不传则全量扫描(兼容旧调用)。'
        ),
    ),
) -> Dict[str, Any]:
    """给"我已经把模型文件放到 bundled/<cap>/ 了"这条手动路径用。

    步骤:
      1. ``scan_once`` 扫 models/ 目录(传了 ``capability`` 则只扫该能力子目录),
         新文件入 local_index
      2. 找出"现在有 local candidate 但 runtime 未 ready"的 cap,逐个 trigger
         ``trigger_auto_start_for_cap`` 起 sidecar

    返回 ``{"scan_delta": {...}, "auto_started": [cap, ...]}`` — 让前端知道
    哪些新模型被发现 + 哪些 cap 被启动了,UI 可以据此刷新状态徽章。

    cap-scoped 行为
    ===============
    「下载模型」对话框是**按能力打开**的(rerank 对话框 / embedding 对话框 …),
    它的「扫描挂载」会带上当前 ``capability``。此时:
      * ``scan_once`` 只扫 ``bundled/<cap>/``,别的 cap 的本地条目原样保留;
      * ``auto_started`` 只尝试启动当前 cap;
      * ``summary`` 的 total / by_capability / problems 也只统计当前 cap。
    """
    from chayuan.server.model_registry.local_index import scan_once, get_local_index
    from chayuan.server.model_registry.install_model import (
        trigger_auto_start_for_cap,
        _CAP_TO_BUNDLED_DIR,
        _CAP_ALIAS,
        _CAP_TO_RUNTIME,
    )

    # ── 解析可选 capability:归一成 catalog 长名 + bundled 子目录名 ──────
    raw_cap = ""
    if isinstance(payload, dict):
        raw_cap = str(payload.get("capability") or "").strip()
    catalog_cap = _CAP_ALIAS.get(raw_cap, raw_cap) if raw_cap else ""
    bundled_cap_dir: Optional[str] = None
    if catalog_cap:
        bundled_cap_dir = _CAP_TO_BUNDLED_DIR.get(catalog_cap)
        if bundled_cap_dir is None:
            raise HTTPException(
                status_code=400,
                detail=f"未知 capability {raw_cap!r}",
            )

    delta = scan_once(bundled_cap_dir=bundled_cap_dir)

    idx = get_local_index()

    if bundled_cap_dir:
        # cap-scoped:只针对当前能力尝试 auto-start,别的 cap 不碰。
        auto_started: list[str] = []
        if catalog_cap in _CAP_TO_RUNTIME and trigger_auto_start_for_cap(catalog_cap):
            auto_started.append(catalog_cap)
    else:
        # 全量:统计扫到了哪些 cap 的本地候选 — 这些 cap 都尝试自动启动
        # (trigger_auto_start_for_cap 内部会判断 cap 是否已 ready/不在支持范围)
        caps_with_local = {e.capability for e in idx.list_entries()}
        auto_started = []
        for cap in caps_with_local:
            if trigger_auto_start_for_cap(cap):
                auto_started.append(cap)

    # bundled 路径提示 — 让前端 Dialog 能直接展示给用户"模型放到这里"
    from chayuan.settings import CHAYUAN_ROOT
    bundled_root = str((__import__("pathlib").Path(CHAYUAN_ROOT) / "models" / "bundled"))

    # ── 汇总:有没有模型 / 有几个 / 哪些 cap 加载失败 ────────────────
    # 前端 "发现 0 个新模型" 那条提示信息量太小:增量为 0 不等于没模型。
    # 这里额外回 total / by_capability / problems,让 UI 能讲清楚现状。
    # cap-scoped 时只统计当前 cap — 别把别的 cap 的也算进来。
    entries = idx.list_entries()
    if catalog_cap:
        entries = [e for e in entries if e.capability == catalog_cap]
    by_capability: Dict[str, int] = {}
    for e in entries:
        by_capability[e.capability] = by_capability.get(e.capability, 0) + 1

    # cap-scoped 时 problems 只保留当前 cap 的 runtime 短名。
    runtime_cap_filter = _CAP_TO_RUNTIME.get(catalog_cap) if catalog_cap else None
    problems: list[Dict[str, str]] = []
    try:
        from chayuan.server.model_registry.local_runtime_registry import get_registry
        for cap, st in get_registry().all_statuses().items():
            if runtime_cap_filter is not None and cap != runtime_cap_filter:
                continue
            sd = st.to_dict()
            if sd.get("state") == "failed":
                problems.append({
                    "capability": cap,
                    "reason": sd.get("last_error") or "未知错误",
                })
    except Exception as e:  # registry 未就绪不应让扫描接口整体失败
        logger.warning("[scan_and_mount] 收集 runtime 失败状态出错: %r", e)

    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "scan_delta": {
                "added":   len(delta.added),
                "updated": len(delta.updated),
                "removed": len(delta.removed),
            },
            "auto_started": auto_started,
            "bundled_root": bundled_root,
            "summary": {
                "total": len(entries),
                "by_capability": by_capability,
                "problems": problems,
            },
        },
    }


# ── 用户手册下载 ──────────────────────────────────────────────────


@admin_router.get(
    "/manuals/list",
    summary="[public] 列出已部署的用户手册(md + docx 路径)",
    tags=["Admin / Manuals"],
)
def admin_manuals_list() -> Dict[str, Any]:
    """前端"知识中心 / 帮助"入口拉这份清单显示"打开用户手册"链接。"""
    from chayuan.server.manuals.deploy import list_deployed_manuals
    return {"code": 0, "msg": "ok", "data": {"items": list_deployed_manuals()}}


@admin_router.get(
    "/manuals/{public_name}",
    summary="[public] 下载用户手册(?fmt=docx|md,默认 docx)",
    tags=["Admin / Manuals"],
)
def admin_manuals_download(
    public_name: str,
    fmt: str = Query("docx", pattern="^(docx|md)$"),
) -> Any:
    """返回手册文件流。404 = 该手册未部署或未生成 docx。"""
    from fastapi.responses import FileResponse
    from chayuan.server.manuals.deploy import get_manual_path

    path = get_manual_path(public_name, fmt=fmt)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"manual {public_name!r} (fmt={fmt}) 未部署",
        )
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if fmt == "docx" else "text/markdown; charset=utf-8"
    )
    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=path.name,
    )


@admin_router.get(
    "/models/process_args",
    summary="[gate] 推理引擎子进程当前会用哪些 args/env(解析快照)",
    tags=["Admin / Models"],
)
def admin_models_process_args() -> Dict[str, Any]:
    """返回 ``llamacpp`` / ``infinity`` / ``ollama`` 三个子进程的解析快照。

    每个进程的解析结果含 ``args`` / ``env`` / ``resolved_models`` / ``missing`` /
    ``reason`` / ``ok``。前端可以用这份数据预览"如果现在重启会按什么模型起",
    或在 ``missing`` 非空时引导用户去配 capability default。

    与 :func:`admin_models_bootstrap` 区别:bootstrap 关心"够不够",这里关心
    "具体怎么启动"。
    """
    from chayuan.server.model_registry.process_args import resolve_all

    snapshot = resolve_all()
    return {
        "code": 0,
        "msg": "ok",
        "data": {name: r.to_dict() for name, r in snapshot.items()},
    }


# ── 进程退出回收 ──────────────────────────────────────────────────


async def shutdown() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
