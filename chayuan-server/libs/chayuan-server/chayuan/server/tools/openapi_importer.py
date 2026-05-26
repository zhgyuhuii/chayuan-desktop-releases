"""OpenAPI 2.0(Swagger) / 3.x 解析器。

输入:OpenAPI spec 文档(URL 或已读 dict)
输出:``List[CustomToolDraft]`` —— 与 ``custom_tools_store.CustomToolSpec`` 一一对应的草稿,
       前端可直接渲染成"批量导入预览清单",用户勾选后再走 ``commit`` 落 yaml。

兼容范围(常见 Java Spring Doc / FastAPI 输出都覆盖):

  ``swagger: "2.0"`` —— host + basePath + schemes 拼 base url
  ``openapi: "3.x"`` —— servers[0].url(可缺,默认 ``/``)
  parameters: query / path / header(body 仅 OpenAPI 2.0)
  requestBody.content.application/json.schema(OpenAPI 3.x)
  $ref → components/schemas / definitions(浅展开:取顶层 properties)

不做的:
- 不解析 oneOf/anyOf/allOf 的复杂组合(P5+ 视需求加)
- 不做完整的 JSON Schema → Python type 推断,只取直观映射
- 不写盘——纯函数,可在前端预览失败时安全重试

代码复用:
  返回的 ``CustomToolDraft`` 与 ``custom_tools_store.CustomToolSpec`` 字段同构;
  ``commit`` 时(P4 路由层)直接 ``CustomToolSpec(**draft.model_dump())`` 即可。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, Field

from chayuan.server.tools.desc_synthesizer import synthesize

logger = logging.getLogger("chayuan.tools.openapi_importer")


# ---------------------------------------------------------------------------
# 输出 schema(对齐 CustomToolSpec)
# ---------------------------------------------------------------------------

class ParamDraft(BaseModel):
    name: str
    type: str = "string"   # string / integer / number / boolean
    description: str = ""
    required: bool = False
    default: Any = None
    enum: List[Any] = Field(default_factory=list)
    location: str = "query"  # query / path / header / body —— 仅做提示,运行时由 url + body_template 隐含


class CustomToolDraft(BaseModel):
    """单条草稿。对应 OpenAPI 一个 operation。"""

    name: str
    title: str = ""
    description: str = ""
    method: str = "GET"
    url: str = ""
    params: List[ParamDraft] = Field(default_factory=list)
    headers: Dict[str, str] = Field(default_factory=dict)
    body_template: Optional[str] = None
    timeout: float = 15.0
    enabled: bool = False
    # 元信息(仅给前端展示用,不入 custom_tools.yaml)
    operation_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source_path: Optional[str] = None  # 原始路径模板,如 ``/users/{id}``
    issues: List[str] = Field(default_factory=list)  # 解析时的告警(非致命)


class ImportPreview(BaseModel):
    """``POST /tools/import/openapi/preview`` 返回。"""

    base_url: str
    title: str = ""
    version: str = ""
    drafts: List[CustomToolDraft]
    skipped: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "bool": "boolean",
    "long": "integer",
    "int": "integer",
    "float": "number",
    "double": "number",
}


_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_]+")
_LEAD_DIGIT_RE = re.compile(r"^[0-9]+")


def parse_dict(spec: Dict[str, Any]) -> ImportPreview:
    """解析已加载的 spec dict。纯函数,主入口。"""
    if not isinstance(spec, dict):
        raise ValueError("OpenAPI spec 必须是 JSON 对象")

    if "openapi" in spec:
        return _parse_openapi3(spec)
    if "swagger" in spec:
        return _parse_swagger2(spec)
    raise ValueError("无法识别的 spec 格式:既不是 ``openapi`` 也不是 ``swagger``")


async def fetch_and_parse(url: str, *, timeout: float = 30.0) -> ImportPreview:
    """从 URL 拉取 + 解析。失败 raise(由路由层兜成 4xx)。

    用 httpx 直拉,不走 chayuan 的 net 抽象 —— 该 URL 是用户在 UI 输入的"
    一次性导入源",不是配置项。返回的 spec 不缓存(用户改了 swagger 重新导入)。
    """
    import httpx
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as cli:
        r = await cli.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        text = r.text
        if "yaml" in ctype.lower() or url.endswith((".yaml", ".yml")):
            from chayuan.pydantic_settings_file import import_yaml
            spec = import_yaml().load(text) or {}
            if not isinstance(spec, dict):
                raise ValueError("YAML 顶层不是 mapping")
        else:
            spec = json.loads(text)
    return parse_dict(spec)


# ---------------------------------------------------------------------------
# Swagger 2.0
# ---------------------------------------------------------------------------

def _parse_swagger2(spec: Dict[str, Any]) -> ImportPreview:
    info = spec.get("info") or {}
    title = str(info.get("title", "") or "")
    version = str(info.get("version", "") or "")

    schemes = spec.get("schemes") or ["https"]
    host = str(spec.get("host", "")).strip()
    base_path = str(spec.get("basePath", "")).strip() or "/"
    base_url = f"{schemes[0]}://{host}{base_path}".rstrip("/") if host else base_path

    definitions = spec.get("definitions") or {}

    drafts: List[CustomToolDraft] = []
    skipped: List[str] = []
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            method_l = str(method).lower()
            if method_l not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, dict):
                continue
            try:
                draft = _operation_to_draft(
                    method=method_l, path=path, op=op,
                    base_url=base_url, definitions=definitions, version=2,
                )
                drafts.append(draft)
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{method_l.upper()} {path}: {e}")
                logger.debug("跳过 swagger2 op", exc_info=True)

    return ImportPreview(
        base_url=base_url, title=title, version=version, drafts=drafts, skipped=skipped,
    )


# ---------------------------------------------------------------------------
# OpenAPI 3.x
# ---------------------------------------------------------------------------

def _parse_openapi3(spec: Dict[str, Any]) -> ImportPreview:
    info = spec.get("info") or {}
    title = str(info.get("title", "") or "")
    version = str(info.get("version", "") or "")

    servers = spec.get("servers") or []
    base_url = ""
    if servers and isinstance(servers, list):
        first = servers[0]
        if isinstance(first, dict):
            base_url = str(first.get("url", "") or "")
    base_url = base_url.rstrip("/") or ""

    schemas = ((spec.get("components") or {}).get("schemas")) or {}

    drafts: List[CustomToolDraft] = []
    skipped: List[str] = []
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            method_l = str(method).lower()
            if method_l not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, dict):
                continue
            try:
                draft = _operation_to_draft(
                    method=method_l, path=path, op=op,
                    base_url=base_url, definitions=schemas, version=3,
                )
                drafts.append(draft)
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{method_l.upper()} {path}: {e}")
                logger.debug("跳过 openapi3 op", exc_info=True)

    return ImportPreview(
        base_url=base_url, title=title, version=version, drafts=drafts, skipped=skipped,
    )


# ---------------------------------------------------------------------------
# operation → draft (两版本共用)
# ---------------------------------------------------------------------------

def _operation_to_draft(
    *,
    method: str, path: str, op: Dict[str, Any],
    base_url: str, definitions: Dict[str, Any], version: int,
) -> CustomToolDraft:
    summary = (op.get("summary") or "").strip() or None
    op_desc = (op.get("description") or "").strip() or None
    op_id = (op.get("operationId") or "").strip() or None
    tags = [str(t) for t in (op.get("tags") or []) if str(t).strip()]

    name = _normalize_name(op_id, method=method, path=path)
    full_url = _join_url(base_url, path)

    issues: List[str] = []
    params: List[ParamDraft] = []

    # parameters: query/path/header(都两版本共用),swagger2 还有 body/formData
    for raw in (op.get("parameters") or []):
        if isinstance(raw, dict) and "$ref" in raw:
            raw = _resolve_ref(raw["$ref"], definitions)
            if raw is None:
                continue
        if not isinstance(raw, dict):
            continue
        loc = str(raw.get("in", "query")).lower()
        if loc not in {"query", "path", "header", "body", "formData"}:
            continue
        pname = str(raw.get("name", "")).strip()
        if not pname:
            continue
        if loc in {"body", "formData"} and version == 2:
            # swagger2 body:展开 schema.properties 当顶层参数
            schema = raw.get("schema") or {}
            schema = _resolve_schema(schema, definitions)
            for sub in _flatten_object_schema(schema):
                params.append(sub)
            continue
        params.append(_param_from_raw(raw, definitions=definitions, default_loc=loc))

    # OpenAPI 3.x requestBody
    if version == 3:
        rb = op.get("requestBody") or {}
        if isinstance(rb, dict):
            content = rb.get("content") or {}
            json_schema = ((content.get("application/json") or {}).get("schema")) or {}
            json_schema = _resolve_schema(json_schema, definitions)
            for sub in _flatten_object_schema(json_schema):
                params.append(sub)

    # 自动生成中文描述
    description = synthesize(
        method=method.upper(), url=full_url,
        summary=summary, description=op_desc,
        params=[p.name for p in params],
    )

    body_template = _build_body_template(method, params)

    return CustomToolDraft(
        name=name,
        title=summary or op_id or f"{method.upper()} {path}",
        description=description,
        method=method.upper(),
        url=full_url,
        params=params,
        headers={},
        body_template=body_template,
        timeout=15.0,
        enabled=False,
        operation_id=op_id,
        tags=tags,
        source_path=path,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _normalize_name(operation_id: Optional[str], *, method: str, path: str) -> str:
    """生成合法的 ``CustomToolSpec.name``(Python 标识符)。"""
    src = operation_id or f"{method}_{path}"
    cleaned = _NAME_SAFE_RE.sub("_", src)
    cleaned = cleaned.strip("_") or "tool"
    if _LEAD_DIGIT_RE.match(cleaned):
        cleaned = f"op_{cleaned}"
    return cleaned[:80]  # custom tool 名字过长 LLM 不友好


def _join_url(base: str, path: str) -> str:
    """把 base_url 与 path 拼成可调用 URL。

    - base 完整(带 scheme)→ urljoin
    - base 仅是 path(``/api``)→ 直接拼,运行时用户加完整 host
    - path 已是绝对 url → 原样返回
    """
    if not path:
        return base
    if path.startswith(("http://", "https://")):
        return path
    if not base:
        return path
    if base.startswith(("http://", "https://")):
        return urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    # base 只是 path 片段
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _param_from_raw(raw: Dict[str, Any], *, definitions: Dict[str, Any], default_loc: str) -> ParamDraft:
    """两版本共用:把 OpenAPI parameter 转 ParamDraft。"""
    schema = raw.get("schema") or {}
    if isinstance(schema, dict) and "$ref" in schema:
        schema = _resolve_schema(schema, definitions)
    typ = (
        str(schema.get("type", raw.get("type", "string"))).lower().strip()
        if isinstance(schema, dict) else str(raw.get("type", "string"))
    )
    enum = schema.get("enum") if isinstance(schema, dict) else raw.get("enum")
    return ParamDraft(
        name=str(raw.get("name", "")).strip(),
        type=_TYPE_MAP.get(typ, "string"),
        description=str(raw.get("description", "") or "").strip(),
        required=bool(raw.get("required", False)),
        default=schema.get("default") if isinstance(schema, dict) else raw.get("default"),
        enum=list(enum) if isinstance(enum, list) else [],
        location=str(raw.get("in", default_loc)).lower(),
    )


def _resolve_schema(schema: Any, definitions: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    if "$ref" in schema:
        resolved = _resolve_ref(schema["$ref"], definitions)
        return resolved if isinstance(resolved, dict) else {}
    return schema


def _resolve_ref(ref: str, definitions: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``#/components/schemas/Foo`` / ``#/definitions/Foo`` → dict。

    只支持本地 ref;远程 ref 返回 None(由调用方落 issues)。
    """
    if not ref or not ref.startswith("#/"):
        return None
    parts = ref.lstrip("#/").split("/")
    cur: Any = {"components": {"schemas": definitions}, "definitions": definitions}
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur if isinstance(cur, dict) else None


def _flatten_object_schema(schema: Dict[str, Any]) -> List[ParamDraft]:
    """body/requestBody schema → 顶层 properties 当扁平参数。

    嵌套对象目前不展开(避免 100 个嵌套字段冲爆 LLM 上下文),保留为 string 类型,
    用户可手动改 body_template。
    """
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties") or {}
    if not isinstance(props, dict):
        return []
    required = set(schema.get("required") or [])
    out: List[ParamDraft] = []
    for name, sub in props.items():
        if not isinstance(sub, dict):
            continue
        typ = str(sub.get("type", "string")).lower().strip()
        out.append(ParamDraft(
            name=str(name),
            type=_TYPE_MAP.get(typ, "string"),
            description=str(sub.get("description", "") or "").strip(),
            required=name in required,
            default=sub.get("default"),
            enum=list(sub.get("enum") or []) if isinstance(sub.get("enum"), list) else [],
            location="body",
        ))
    return out


def _build_body_template(method: str, params: List[ParamDraft]) -> Optional[str]:
    """POST/PUT/PATCH 时,把 body 类参数拼成 ``{"k": "{k}"}`` 占位 JSON,运行时填值。"""
    if method.upper() not in {"POST", "PUT", "PATCH"}:
        return None
    body_params = [p for p in params if p.location == "body"]
    if not body_params:
        return None
    body = {p.name: f"{{{p.name}}}" for p in body_params}
    return json.dumps(body, ensure_ascii=False)
