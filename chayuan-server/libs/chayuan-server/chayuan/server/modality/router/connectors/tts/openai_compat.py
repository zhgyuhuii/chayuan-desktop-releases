"""OpenAI 兼容 ``/v1/audio/speech`` TTS Connector。

适用:
    - OpenAI 官方:tts-1 / tts-1-hd / gpt-4o-mini-tts
    - OpenRouter / Together / Fireworks 等代理
    - 字节豆包 Ark(OpenAI 兼容模式下的 TTS)

上游协议(完全照搬 OpenAI):
::

    POST {api_base}/audio/speech
    Authorization: Bearer <api_key>
    Content-Type: application/json
    {
      "model": "tts-1",
      "input": "要朗读的文本",
      "voice": "alloy"|"echo"|"fable"|"onyx"|"nova"|"shimmer",
      "response_format": "mp3"|"opus"|"aac"|"flac"|"wav"|"pcm",
      "speed": 0.25-4.0  (默认 1.0)
    }

响应:**二进制音频字节**(不是 JSON)。Content-Type 给出 MIME。

实现要点
========
- 文本上限:OpenAI 文档 4096 字符;我们 4000 字软限,超出截断 + 提示用户
- 默认 voice = alloy(最中性);format = mp3(浏览器原生最优,体积小)
- speed 严格 [0.25, 4.0],超界 clamp
- 上游 4xx 时 body 是 JSON,200 时 body 是二进制,**处理路径要分叉**
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger("chayuan.modality.connectors.tts.openai_compat")


# ──────────────────────────────────────────────────────────────
# 参数规范化
# ──────────────────────────────────────────────────────────────

_VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
_VALID_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}
_FORMAT_MIME: Dict[str, str] = {
    "mp3":  "audio/mpeg",
    "opus": "audio/ogg",
    "aac":  "audio/aac",
    "flac": "audio/flac",
    "wav":  "audio/wav",
    "pcm":  "audio/L16",
}

_TEXT_SOFT_LIMIT = 4000  # OpenAI 硬限 4096


def _normalize_voice(raw: Optional[str]) -> str:
    if raw and raw.lower() in _VALID_VOICES:
        return raw.lower()
    return "alloy"


def _normalize_format(raw: Optional[str]) -> str:
    if raw and raw.lower() in _VALID_FORMATS:
        return raw.lower()
    return "mp3"


def _clamp_speed(raw) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return max(0.25, min(4.0, v))


def _build_endpoint(api_base: str) -> str:
    return f"{(api_base or '').rstrip('/')}/audio/speech"


# ──────────────────────────────────────────────────────────────
# Connector
# ──────────────────────────────────────────────────────────────


class _OpenAICompatTTS(Connector):
    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        text = (req.prompt or "").strip()
        if not text:
            yield error_event("TTS 需要输入要朗读的文本", code="empty_text")
            return

        # 文本截断 + 提示(老老实实告诉用户截掉了)
        if len(text) > _TEXT_SOFT_LIMIT:
            yield text_start("warn")
            yield text_delta(
                "warn",
                f"(文本过长,已截断到 {_TEXT_SOFT_LIMIT} 字符;"
                f"原 {len(text)} 字符 — 建议分段调用)",
            )
            yield text_end("warn")
            text = text[:_TEXT_SOFT_LIMIT]

        voice = _normalize_voice(req.params.get("voice"))
        fmt = _normalize_format(req.params.get("response_format") or req.params.get("format"))
        speed = _clamp_speed(req.params.get("speed", 1.0))

        text_id = "t0"
        yield text_start(text_id)
        yield text_delta(text_id, f"语音合成中(voice={voice}, format={fmt}, speed={speed:.2f})…")
        yield text_end(text_id)
        yield data_part("task-progress", {"percent": 10, "message": "提交合成请求"})

        endpoint = _build_endpoint(req.api_base)
        body: Dict = {
            "model": req.model,
            "input": text,
            "voice": voice,
            "response_format": fmt,
        }
        if abs(speed - 1.0) > 1e-3:
            body["speed"] = speed
        headers = {
            "Authorization": f"Bearer {req.api_key}",
            "Content-Type": "application/json",
            "Accept": _FORMAT_MIME.get(fmt, "audio/mpeg"),
        }

        # TTS 文本 ≤ 4k 字符 latency 一般 2-8s;timeout 给 60s 兜底
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=body, headers=headers)
        except httpx.RequestError as e:
            yield error_event(
                f"无法连接 {req.platform_name or 'OpenAI 兼容'} TTS 接口: {e}",
                code="upstream_unreachable",
            )
            return

        # 4xx/5xx:body 是 JSON;2xx:body 是二进制
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_msg = (
                    err_body.get("error", {}).get("message")
                    if isinstance(err_body.get("error"), dict)
                    else err_body.get("message") or err_body
                )
            except ValueError:
                err_msg = resp.text[:500]
            yield error_event(
                f"{req.platform_name or 'OpenAI'} TTS {resp.status_code}: {err_msg}",
                code=f"upstream_{resp.status_code}",
            )
            return

        audio_bytes = resp.content
        if not audio_bytes:
            yield error_event("上游返回 200 但音频字节为空", code="empty_audio")
            return

        mime = (resp.headers.get("content-type") or _FORMAT_MIME.get(fmt, "audio/mpeg")).split(";")[0].strip()
        if not mime.startswith("audio/"):
            mime = _FORMAT_MIME.get(fmt, "audio/mpeg")

        yield data_part("task-progress", {"percent": 90, "message": "保存音频"})
        saved = save_bytes(audio_bytes, mime, ext=fmt)
        yield file_part(
            media_type=mime,
            url=saved["url"],
            metadata={
                "sha256": saved["sha256"],
                "size_bytes": saved["size"],
                "voice": voice,
                "speed": speed,
                "format": fmt,
                "text_chars": len(text),
                "model": req.model,
                "platform": req.platform_name,
            },
        )
        yield data_part(
            "usage",
            {
                "audio_chars": len(text),
                "voice": voice,
                "model": req.model,
                "size_bytes": saved["size"],
            },
        )
        yield data_part("task-progress", {"percent": 100, "message": "完成"})


# ──────────────────────────────────────────────────────────────
# 注册
# ──────────────────────────────────────────────────────────────


@register(Capability.TTS, "openai")
class OpenAITTSConnector(_OpenAICompatTTS):
    """OpenAI 官方 / OpenAI-compatible TTS。"""


@register(Capability.TTS, "openrouter")
class OpenRouterTTSConnector(_OpenAICompatTTS):
    """OpenRouter 代理 — 同 OpenAI 协议。"""


@register(Capability.TTS, "volcengine")
class VolcengineTTSConnector(_OpenAICompatTTS):
    """字节豆包 Ark — 用 OpenAI 兼容 TTS 端点。"""
