from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from chayuan_gateway.deps import get_repo
from chayuan_modelmgr import get_default_for_capability
from chayuan_registry import ModelRepository
from chayuan_runtime import AdapterRequest, pick_adapter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["embeddings"])


def _looks_like_image_input(payload: dict[str, Any]) -> bool:
    """非常宽松的"是不是图片输入"判定。

    OpenAI 的 ``/v1/embeddings`` 标准没有 image 字段；社区扩展用 ``input``
    传 base64 / URL / dict({"image": ...})。我们对几种常见形态都检测一下，
    用来在没有 image-embedding 模型时给前端发更精确的引导。
    """
    if "image" in payload or "images" in payload or "image_input" in payload:
        return True
    inp = payload.get("input")
    if isinstance(inp, dict) and ("image" in inp or "image_url" in inp):
        return True
    if isinstance(inp, list) and inp:
        first = inp[0]
        if isinstance(first, dict) and ("image" in first or "image_url" in first):
            return True
        if isinstance(first, str) and first.startswith(("data:image/", "http://", "https://")):
            return True
    return False


def _missing_model_response(*, capability: str, hint: str) -> JSONResponse:
    """统一的"模型缺失 → 一键安装"结构化错误。

    HTTP 412 Precondition Failed：调用方可以拿 ``setup`` 字段引导用户去设
    置面板装模型。前端 Composer 收到 412 时把"上传图片"按钮换成
    "需要安装图像嵌入模型 → 一键安装"卡片。
    """
    rec = get_default_for_capability(capability)
    body = {
        "error": {
            "code": "model_not_configured",
            "capability": capability,
            "message": hint,
            "setup": {
                "endpoint": "/v1/admin/recommended_models",
                "query": {"capability": capability},
                "panel": "settings.aiPlatform.capability",
                "default": rec.to_dict() if rec else None,
            },
        },
    }
    return JSONResponse(status_code=412, content=body)


@router.get("/v1/embeddings/preflight")
def embeddings_preflight(
    modality: str = "text",
    repo: ModelRepository = Depends(get_repo),
):
    """前端在 "上传图片 / 把图片塞进 KB" 之前调一次。

    * ``modality=text``：检查有没有 text-embedding；没装 → 引导默认 bge-small。
    * ``modality=image``：检查有没有 image-embedding（category=clip）；没装
      → 引导默认 jina-clip-v1。

    ``ok=false`` 时返回的 ``setup`` 结构和 ``_missing_model_response`` 一致；
    前端可以直接拿来 渲染 "需要安装 X 模型 → 一键安装" 卡片。
    """
    cap = "image-embedding" if modality == "image" else "text-embedding"
    needs_clip = (modality == "image")
    candidates = list(repo.list(category="clip" if needs_clip else "embedding"))
    if needs_clip:
        # text 嵌入也接受 None/空（图像必须 clip）
        candidates = [m for m in candidates if getattr(m, "category", None) == "clip"]

    if candidates:
        # 有可用模型 —— 哪一个会被自动选？默认 > 其他
        chosen = next((m for m in candidates if getattr(m, "is_default", False)), candidates[0])
        return {
            "ok": True, "modality": modality, "capability": cap,
            "model": getattr(chosen, "id", None) or getattr(chosen, "public_id", None),
            "runtime": getattr(chosen, "runtime", None),
        }

    rec = get_default_for_capability(cap)
    return {
        "ok": False,
        "modality": modality,
        "capability": cap,
        "setup": {
            "endpoint": "/v1/admin/recommended_models",
            "query": {"capability": cap},
            "panel": "settings.aiPlatform.capability",
            "default": rec.to_dict() if rec else None,
        },
    }


@router.post("/v1/embeddings")
def embeddings(payload: dict[str, Any] = Body(...), repo: ModelRepository = Depends(get_repo)):
    image_like = _looks_like_image_input(payload)
    model_id = payload.get("model")

    # 没传 model：尝试自动选默认；选不到就给结构化引导，而不是 400 了事
    if not model_id:
        cap = "image-embedding" if image_like else "text-embedding"
        # 找已安装的同类默认；没有再看是不是有未安装但推荐的
        all_models = list(repo.list(category="embedding"))
        chosen = next((m for m in all_models if getattr(m, "is_default", False)), None) or (
            all_models[0] if all_models else None
        )
        if chosen is None:
            return _missing_model_response(
                capability=cap,
                hint=(
                    f"未安装 {cap} 模型；图片向量化无法进行。"
                    if image_like else
                    f"未配置 {cap} 模型；请到设置面板 → AI 平台 → 推荐 安装一个嵌入模型。"
                ),
            )
        m = chosen
    else:
        m = repo.get(model_id)
        if m is None:
            return _missing_model_response(
                capability="image-embedding" if image_like else "text-embedding",
                hint=f"未找到模型 {model_id}；请到设置面板安装或选择一个已有的嵌入模型。",
            )

    if m.category not in ("embedding", "clip"):
        raise HTTPException(409, f"model {m.id} is not an embedding model (category={m.category})")

    if image_like and m.category != "clip":
        # 文本嵌入模型不能做图像；明确告诉调用方"请装个跨模态模型"
        return _missing_model_response(
            capability="image-embedding",
            hint=(
                f"模型 {m.id} 是文本嵌入，不接受图像输入；"
                f"请在设置面板装一个跨模态模型（推荐 jina-clip-v1）。"
            ),
        )

    adapter = pick_adapter(m)
    if adapter is None:
        return _missing_model_response(
            capability="image-embedding" if image_like else "text-embedding",
            hint=f"模型 {m.id} 没匹配到运行时（runtime={getattr(m, 'runtime', '?')}）；可能是适配器未安装或服务未启动。",
        )
    return adapter.call(AdapterRequest(op="embedding", model=m, payload=payload)).body
