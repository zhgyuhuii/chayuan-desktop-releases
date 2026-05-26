"""DashScope native TTS Connector — qwen-tts / qwen3-tts / cosyvoice / sambert。

为什么不复用 OpenAI 兼容
========================

DashScope 的 ``/compatible-mode/v1`` 只覆盖 chat + embedding;TTS / image / ASR
都只在 native ``/api/v1/services/aigc/...`` 上发布。同时 native TTS 的请求
body 结构跟 OpenAI 的 ``/audio/speech`` 完全不同:

OpenAI:
::

    POST /audio/speech
    {"model": "tts-1", "input": "text", "voice": "alloy", "speed": 1.0}
    → response body 是二进制音频

DashScope(qwen3-tts):
::

    POST /api/v1/services/aigc/multimodal-generation/generation
    {
      "model": "qwen3-tts-flash",
      "input": {
        "text": "文本",
        "voice": "Cherry",               # 不是 alloy
        "language_type": "Chinese"       # 可选
      }
    }
    → response JSON,音频在 output.audio.url(24h 过期,要立即下载落 artifacts)

可用音色(qwen-tts / qwen3-tts):
    Cherry, Serena, Ethan, Chelsie, Nofish, Jennifer, Ryan, Katerina, Elias,
    Jada, Dylan, Sunny, Li, Marcus, Roy, Peter, Rocky, Kiki, Eric, ...
    部分还有方言版:Dylan(京片儿)/ Jada(上海话)/ Sunny(四川话)

cosyvoice / sambert 的音色名是另一套(longxiaochun / longxiaobai 等);本
Connector 不强校验 voice 字符串,允许任意值透传,无效时上游返 400 让用户改。
"""

from __future__ import annotations

import logging
import re
from typing import AsyncIterator, Dict, Optional

import httpx

from chayuan.server.modality.router.artifacts import save_bytes
from chayuan.server.modality.router.connectors.base import Connector, register
from chayuan.server.modality.router.protocol import Capability, GenerateReq
from chayuan.server.modality.router.sse_v5 import (
    data_part,
    error_event,
    file_part,
    text_delta,
    text_end,
    text_start,
)

logger = logging.getLogger("chayuan.modality.connectors.tts.dashscope")


# ──────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────

DEFAULT_VOICE = "Cherry"
DEFAULT_LANGUAGE = "Auto"


def _build_endpoint(api_base: str) -> str:
    base = (api_base or "").rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}/services/aigc/multimodal-generation/generation"
    base = re.sub(r"/compatible-mode/v\d+$", "", base)
    base = re.sub(r"/v\d+$", "", base)
    return f"{base}/api/v1/services/aigc/multimodal-generation/generation"


# ──────────────────────────────────────────────────────────────
# Connector
# ──────────────────────────────────────────────────────────────


@register(Capability.TTS, "dashscope")
class DashScopeTTSConnector(Connector):
    """qwen-tts / qwen3-tts / cosyvoice / sambert — DashScope native multimodal-generation。"""

    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        text = (req.prompt or "").strip()
        if not text:
            yield error_event("TTS 需要输入要朗读的文本", code="empty_text")
            return

        # 参数:voice / language_type
        # voice 不强校验:不同模型族(qwen-tts / cosyvoice)的音色名集合不一样,
        # 让上游校验比硬编一份白名单稳。
        voice = (req.params.get("voice") or DEFAULT_VOICE)
        language_type = req.params.get("language_type") or req.params.get("language") or DEFAULT_LANGUAGE

        text_id = "t0"
        yield text_start(text_id)
        yield text_delta(text_id, f"语音合成中(voice={voice}, lang={language_type})…")
        yield text_end(text_id)
        yield data_part("task-progress", {"percent": 10, "message": "提交合成请求"})

        endpoint = _build_endpoint(req.api_base)
        body: Dict = {
            "model": req.model,
            "input": {
                "text": text,
                "voice": voice,
            },
        }
        if language_type and language_type.lower() != "auto":
            body["input"]["language_type"] = language_type
        # 可选:speed / format / sample_rate(若用户填了)— 上游忽略未识别字段
        if "speed" in req.params:
            try:
                body["input"]["speed"] = float(req.params["speed"])
            except (TypeError, ValueError):
                pass
        if "format" in req.params:
            body.setdefault("parameters", {})["format"] = str(req.params["format"])

        headers = {
            "Authorization": f"Bearer {req.api_key}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=body, headers=headers)
        except httpx.RequestError as e:
            yield error_event(
                f"无法连接 DashScope TTS 接口: {e}",
                code="upstream_unreachable",
            )
            return

        if resp.status_code != 200:
            try:
                err_body = resp.json()
            except ValueError:
                err_body = {"raw": resp.text[:500]}
            msg = err_body.get("message") or err_body
            yield error_event(
                f"DashScope TTS {resp.status_code}: {msg}",
                code=f"upstream_{resp.status_code}",
            )
            return

        data = resp.json()
        output = data.get("output") or {}
        # qwen3-tts 路径:output.audio.url(24h 过期)
        # cosyvoice 旧路径:可能 output.audio_url 直接平铺 — 兼容下
        audio_url = None
        audio_info = output.get("audio")
        if isinstance(audio_info, dict):
            audio_url = audio_info.get("url")
        if not audio_url:
            audio_url = output.get("audio_url")

        if not audio_url:
            yield error_event(
                f"上游未返回音频 URL:{data}",
                code="no_audio_url",
            )
            return

        yield data_part("task-progress", {"percent": 70, "message": "下载音频"})

        # 立即下载(URL 24h 过期)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            try:
                ar = await client.get(audio_url)
                ar.raise_for_status()
            except Exception as e:  # noqa: BLE001
                yield error_event(f"下载合成音频失败: {e}", code="download_failed")
                return
            audio_bytes = ar.content
            mime = (ar.headers.get("content-type") or "audio/wav").split(";")[0].strip()

        # 扩展名按 mime 推
        ext = None
        if mime in ("audio/wav", "audio/x-wav"):
            ext = "wav"
        elif mime == "audio/mpeg":
            ext = "mp3"
        elif mime == "audio/ogg":
            ext = "ogg"

        saved = save_bytes(audio_bytes, mime, ext=ext)
        yield file_part(
            media_type=mime,
            url=saved["url"],
            metadata={
                "sha256": saved["sha256"],
                "size_bytes": saved["size"],
                "voice": voice,
                "language_type": language_type,
                "text_chars": len(text),
                "model": req.model,
                "platform": req.platform_name,
            },
        )

        usage = data.get("usage") or {}
        yield data_part(
            "usage",
            {
                "audio_chars": len(text),
                "voice": voice,
                "model": req.model,
                "size_bytes": saved["size"],
                **{k: v for k, v in usage.items() if isinstance(v, (int, float, str))},
            },
        )
        yield data_part("task-progress", {"percent": 100, "message": "完成"})
