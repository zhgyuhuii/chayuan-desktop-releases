from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Path as PathParam, Query, Request
from pydantic import BaseModel, Field

from chayuan.server.utils import BaseResponse, get_tool, get_tool_config
from chayuan.utils import build_logger


logger = build_logger()

tool_router = APIRouter(prefix="/tools", tags=["Toolkits"])


@lru_cache(maxsize=1)
def _umbrella_map() -> Dict[str, str]:
    """返回 ``运行时工具名 -> yaml 里的伞型 key`` 映射。

    读取内置 ``chayuan/data/tools_catalog.json``：当某条目里声明 ``register_as``
    列表时，里面每个工具都 gate 到该卡片的 ``key`` 上。常见例子：
    ``amap`` 卡片管理 ``amap_poi_search / amap_weather`` 两个运行时工具。

    找不到文件或 key 时返回空 dict；缓存一次即可。
    """
    try:
        import chayuan as _c

        path = Path(_c.__file__).resolve().parent / "data" / "tools_catalog.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return {}

    out: Dict[str, str] = {}
    for t in data.get("tools") or []:
        umbrella = t.get("key")
        for child in (t.get("register_as") or []) or []:
            if umbrella and child:
                out[str(child)] = str(umbrella)
    return out


def _effective_config_key(tool_name: str) -> str:
    """给定运行时工具名，返回它在 ``tool_settings.yaml`` 里的配置 key。"""
    return _umbrella_map().get(tool_name, tool_name)


@tool_router.get("", response_model=BaseResponse)
async def list_tools(
    enabled: bool = Query(
        False,
        description="只返回 tool_settings.yaml 里 use=true 的工具；对话界面默认传 true。",
    ),
):
    """返回可用工具清单。

    - ``enabled=false``（默认）：沿用旧行为，列出所有已注册工具，适合运维排查；
    - ``enabled=true``：只返回 ``tool_settings.yaml`` 中 ``use: true`` 的工具，
      供对话界面「选择工具」下拉使用——关闭的工具不再展示给终端用户。

    伞型工具（如 ``amap_poi_search / amap_weather`` 共用 ``amap`` 配置）通过
    目录 JSON 里的 ``register_as`` 字段映射到伞型 key，再读 ``use``。
    """
    tools = get_tool()
    data = {}
    for t in tools.values():
        cfg_key = _effective_config_key(t.name)
        cfg = get_tool_config(cfg_key) or {}
        if enabled and not bool(cfg.get("use", False)):
            continue
        data[t.name] = {
            "name": t.name,
            "title": getattr(t, "title", t.name),
            "description": t.description,
            "args": t.args,
            "config": cfg,
        }
    return {"data": data}


@tool_router.post("/call", response_model=BaseResponse)
async def call_tool(
    name: str = Body(examples=["calculate"]),
    tool_input: dict = Body({}, examples=[{"text": "3+5/2"}]),
):
    import time
    from chayuan.server.observability.metrics import record_tool_call

    t0 = time.perf_counter()
    status = "error"
    try:
        if tool := get_tool(name):
            try:
                result = await tool.ainvoke(tool_input)
                status = "success"
                return {"data": result}
            except Exception:
                msg = f"failed to call tool '{name}'"
                logger.exception(msg)
                return {"code": 500, "msg": msg}
        else:
            status = "not_found"
            return {"code": 500, "msg": f"no tool named '{name}'"}
    finally:
        record_tool_call(name, status, time.perf_counter() - t0)


# =============================================================================
# P1: 工具配置 / 自定义工具 / OpenAPI 批量导入
#
# 设计原则:
#   - 路由层只做"协议适配 + 参数校验",业务在 ``chayuan.server.tools.*``
#   - 写后立即调 ``custom_tools_runtime.load_and_register`` 让 LLM 即刻可用
#   - 错误一律返回 BaseResponse {code, msg};只有 commit 接口有结构化 data
# =============================================================================


def _reload_custom_tools_safe() -> None:
    """触发自定义工具运行时重载。失败只记 log,不抛(yaml 已落盘)。"""
    try:
        from chayuan.server.agent.tools_factory.custom_tools_runtime import (
            load_and_register,
        )
        load_and_register()
    except Exception:  # noqa: BLE001
        logger.exception("自定义工具重载失败,已落盘的 yaml 仍生效,下次重启会加载")


# ---------------------------------------------------------------------------
# 内置工具:配置 CRUD
# ---------------------------------------------------------------------------


@tool_router.get("/catalog", response_model=BaseResponse)
async def get_tools_catalog():
    """返回工具目录(catalog + 当前 yaml 值合并视图)。

    前端 ``/tools`` 页面读这个接口渲染卡片网格,每张卡片自带:
      - 元数据(title/icon/category/summary/description/tags/docs_url)
      - 字段 schema(渲染配置表单)
      - 当前 yaml 值(回填表单)
      - enabled/description_override(开关与覆盖描述)
    """
    from chayuan.server.tools.config_service import list_cards, list_categories
    cards = [c.model_dump() for c in list_cards()]
    return {"data": {"cards": cards, "categories": list_categories()}}


@tool_router.get("/{key}/config", response_model=BaseResponse)
async def get_tool_config_doc(key: str = PathParam(..., min_length=1)):
    from chayuan.server.tools.config_service import get_card
    card = get_card(key)
    if card is None:
        raise HTTPException(status_code=404, detail=f"工具不存在:{key}")
    return {"data": card.model_dump()}


@tool_router.put("/{key}/config", response_model=BaseResponse)
async def update_tool_config_doc(
    key: str = PathParam(..., min_length=1),
    payload: Dict[str, Any] = Body(..., examples=[{
        "enabled": True,
        "description_override": None,
        "values": {"top_k": 5},
    }]),
):
    from chayuan.server.tools.config_service import ToolUpdate, update_config
    try:
        update = ToolUpdate(**payload)
        card = update_config(key, update)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"data": card.model_dump()}


# ---------------------------------------------------------------------------
# 自定义 HTTP 工具:CRUD
# ---------------------------------------------------------------------------


class CustomToolPayload(BaseModel):
    """``POST /tools/custom`` / ``PUT /tools/custom/{name}`` 请求体。

    与 ``custom_tools_store.CustomToolSpec`` 一一对应,但用 pydantic 让 FastAPI
    生成 OpenAPI schema,前端 codegen 友好。
    """

    name: str = Field(..., min_length=1, max_length=80)
    title: str = ""
    description: str = ""
    method: str = "GET"
    url: str = ""
    params: List[Dict[str, Any]] = Field(default_factory=list)
    headers: Dict[str, str] = Field(default_factory=dict)
    body_template: Optional[str] = None
    timeout: float = 15.0
    enabled: bool = False


def _payload_to_spec(payload: CustomToolPayload):
    from chayuan.server.config_panel.custom_tools_store import CustomToolSpec, ParamSpec
    params = []
    for p in payload.params or []:
        params.append(ParamSpec(
            name=str(p.get("name", "")).strip(),
            type=str(p.get("type", "string")).lower().strip() or "string",
            description=str(p.get("description", "")),
            required=bool(p.get("required", False)),
            default=p.get("default"),
            enum=list(p.get("enum") or []),
        ))
    return CustomToolSpec(
        name=payload.name.strip(),
        title=payload.title or "",
        description=payload.description or "",
        method=(payload.method or "GET").upper(),
        url=payload.url or "",
        params=params,
        headers=dict(payload.headers or {}),
        body_template=payload.body_template,
        timeout=float(payload.timeout or 15.0),
        enabled=bool(payload.enabled),
    )


@tool_router.get("/custom", response_model=BaseResponse)
async def list_custom_tools():
    from chayuan.server.config_panel.custom_tools_store import list_tools as _list
    items = [t.to_dict() for t in _list()]
    return {"data": items}


@tool_router.post("/custom", response_model=BaseResponse)
async def create_custom_tool(payload: CustomToolPayload):
    from chayuan.server.config_panel.custom_tools_store import (
        get_tool as _get, save_tool as _save,
    )
    if _get(payload.name):
        raise HTTPException(status_code=409, detail=f"自定义工具已存在:{payload.name}")
    spec = _payload_to_spec(payload)
    try:
        _save(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _reload_custom_tools_safe()
    return {"data": spec.to_dict()}


@tool_router.put("/custom/{name}", response_model=BaseResponse)
async def update_custom_tool(
    name: str = PathParam(..., min_length=1),
    payload: CustomToolPayload = Body(...),
):
    if name != payload.name:
        # name 一旦改动 ≈ 删旧建新,以免运行时 _TOOLS_REGISTRY 残留旧名
        raise HTTPException(
            status_code=400,
            detail=f"路径 name({name}) 与 payload.name({payload.name}) 不一致;改名请用 DELETE + POST",
        )
    from chayuan.server.config_panel.custom_tools_store import (
        get_tool as _get, save_tool as _save,
    )
    if not _get(name):
        raise HTTPException(status_code=404, detail=f"自定义工具不存在:{name}")
    spec = _payload_to_spec(payload)
    try:
        _save(spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _reload_custom_tools_safe()
    return {"data": spec.to_dict()}


@tool_router.delete("/custom/{name}", response_model=BaseResponse)
async def delete_custom_tool(name: str = PathParam(..., min_length=1)):
    from chayuan.server.config_panel.custom_tools_store import delete_tool as _del
    ok = _del(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"自定义工具不存在:{name}")
    _reload_custom_tools_safe()
    return {"data": {"deleted": name}}


# ---------------------------------------------------------------------------
# OpenAPI 批量导入
# ---------------------------------------------------------------------------


class OpenApiPreviewRequest(BaseModel):
    """``POST /tools/import/openapi/preview``。

    二选一:``spec_url``(让后端拉)或 ``spec_text``(前端粘 JSON/YAML 文本)。
    都给则优先 spec_text。
    """

    spec_url: Optional[str] = None
    spec_text: Optional[str] = None


@tool_router.post("/import/openapi/preview", response_model=BaseResponse)
async def import_openapi_preview(req: OpenApiPreviewRequest):
    from chayuan.server.tools.openapi_importer import fetch_and_parse, parse_dict
    if req.spec_text and req.spec_text.strip():
        try:
            text = req.spec_text.strip()
            try:
                spec = json.loads(text)
            except json.JSONDecodeError:
                # 兼容 YAML 粘贴
                from chayuan.pydantic_settings_file import import_yaml
                spec = import_yaml().load(text) or {}
            preview = parse_dict(spec)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"spec 解析失败:{e}")
    elif req.spec_url and req.spec_url.strip():
        try:
            preview = await fetch_and_parse(req.spec_url.strip())
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"拉取/解析 spec 失败:{e}")
    else:
        raise HTTPException(status_code=400, detail="spec_url 与 spec_text 必须提供其一")
    return {"data": preview.model_dump()}


class OpenApiCommitRequest(BaseModel):
    """``POST /tools/import/openapi/commit``。

    前端把 preview 返回的 drafts 选中/编辑后回传。后端直接落 ``custom_tools.yaml``,
    重名规则:``overwrite=False`` 时跳过同名(返回 skipped);``True`` 时覆盖。
    """

    drafts: List[Dict[str, Any]]
    overwrite: bool = False


@tool_router.post("/import/openapi/commit", response_model=BaseResponse)
async def import_openapi_commit(req: OpenApiCommitRequest):
    from chayuan.server.config_panel.custom_tools_store import (
        CustomToolSpec, ParamSpec,
        get_tool as _get, save_tool as _save,
    )

    if not req.drafts:
        raise HTTPException(status_code=400, detail="drafts 不能为空")

    created: List[str] = []
    updated: List[str] = []
    skipped: List[Dict[str, str]] = []

    for raw in req.drafts:
        try:
            params: List[ParamSpec] = []
            for p in raw.get("params") or []:
                params.append(ParamSpec(
                    name=str(p.get("name", "")).strip(),
                    type=str(p.get("type", "string")).lower().strip() or "string",
                    description=str(p.get("description", "")),
                    required=bool(p.get("required", False)),
                    default=p.get("default"),
                    enum=list(p.get("enum") or []),
                ))
            spec = CustomToolSpec(
                name=str(raw.get("name", "")).strip(),
                title=str(raw.get("title", "")),
                description=str(raw.get("description", "")),
                method=str(raw.get("method", "GET")).upper(),
                url=str(raw.get("url", "")),
                params=params,
                headers=dict(raw.get("headers") or {}),
                body_template=raw.get("body_template"),
                timeout=float(raw.get("timeout", 15.0) or 15.0),
                enabled=bool(raw.get("enabled", False)),
            )
            existed = _get(spec.name) is not None
            if existed and not req.overwrite:
                skipped.append({"name": spec.name, "reason": "duplicate"})
                continue
            _save(spec)
            (updated if existed else created).append(spec.name)
        except Exception as e:  # noqa: BLE001
            skipped.append({"name": str(raw.get("name", "")), "reason": str(e)})

    if created or updated:
        _reload_custom_tools_safe()

    return {"data": {
        "created": created, "updated": updated, "skipped": skipped,
        "total_created": len(created),
        "total_updated": len(updated),
        "total_skipped": len(skipped),
    }}
