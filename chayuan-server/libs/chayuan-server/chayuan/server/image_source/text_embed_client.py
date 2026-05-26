"""文本向量化客户端 — 调"用户配置的默认文本向量模型"的 OpenAI 兼容端点。

模型选择真源:
    chayuan.server.utils.get_default_embedding()  → str (model name)
    chayuan.server.utils.get_model_info(name)     → {api_base_url, api_key, platform_name}

API 契约(OpenAI /v1/embeddings):
    POST {base_url}/v1/embeddings
    body: {"model": "<name>", "input": ["<text>"]}
    resp: {"data": [{"embedding": [float, ...]}, ...]}

base_url 可能带 /v1 也可能不带,本模块负责 normalize。
本模块不写死任何模型名 — 用户改"默认文本向量模型"后立即生效。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger("chayuan.image_source.text_embed_client")


@dataclass
class EmbedResult:
    vector: Optional[List[float]] = None
    error: Optional[str] = None
    model: str = ""


def _normalize_embeddings_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base + "/embeddings"
    if "/v1/" in base + "/":
        # 已经带 /v1/something,直接拼 /embeddings(罕见情况)
        return base + "/embeddings"
    return base + "/v1/embeddings"


async def embed_text(
    text: str, *, base_url: str, model: str,
    api_key: Optional[str] = None, timeout: float = 30.0,
) -> EmbedResult:
    """对一段文本拿向量,失败时返 .error 而非抛异常。

    base_url + model 由调用方解析好,本函数纯粹 HTTP。
    """
    if not text or not text.strip():
        return EmbedResult(error="empty input")
    if not base_url or not model:
        return EmbedResult(error="missing base_url or model")

    url = _normalize_embeddings_url(base_url)
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "input": [text]}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers,
                                       timeout=timeout)
        if resp.status_code >= 400:
            return EmbedResult(
                error=f"{resp.status_code} {resp.text[:200]}", model=model,
            )
        data = resp.json() or {}
    except httpx.TimeoutException as e:
        return EmbedResult(error=f"timeout: {e}", model=model)
    except Exception as e:  # noqa: BLE001
        return EmbedResult(error=f"http error: {e}", model=model)

    items = data.get("data") or []
    if not items or "embedding" not in items[0]:
        return EmbedResult(
            error=f"bad response shape: {str(data)[:200]}", model=model,
        )
    vec = items[0]["embedding"]
    if not isinstance(vec, list) or not vec:
        return EmbedResult(error="empty embedding", model=model)
    return EmbedResult(vector=vec, model=model)


def resolve_endpoint() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """读取用户配置的默认文本向量模型 → (base_url, model_name, api_key)。

    解析路径:
        1. chayuan.server.utils.get_default_embedding()  → 模型名
        2. chayuan.server.utils.get_model_info(name)     → 平台连接信息

    任一步失败或不存在配置 → (None, None, None),pipeline 自动软降级。
    """
    try:
        from chayuan.server.utils import (
            get_default_embedding, get_model_info,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("import utils failed: %r", e)
        return None, None, None
    try:
        model = get_default_embedding()
    except Exception as e:  # noqa: BLE001
        logger.debug("get_default_embedding failed: %r", e)
        return None, None, None
    if not model:
        return None, None, None
    try:
        info = get_model_info(model_name=model) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("get_model_info(%s) failed: %r", model, e)
        return None, None, None
    base_url = info.get("api_base_url") or ""
    api_key = info.get("api_key") or None
    if not base_url:
        return None, None, None
    return base_url, model, api_key
