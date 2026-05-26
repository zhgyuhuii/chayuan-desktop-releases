"""OpenAI 兼容 ``/v1/audio/transcriptions`` ASR Connector(whisper / gpt-4o-transcribe)。

上游协议(不是 JSON,是 multipart/form-data)
==============================================

::

    POST {api_base}/audio/transcriptions
    Authorization: Bearer <api_key>
    Content-Type: multipart/form-data
    Form fields:
      file:            <binary>        # 音频文件,字段名固定 file
      model:           whisper-1 / gpt-4o-transcribe / ...
      language:        en / zh / ...   # 可选,ISO-639-1
      prompt:          ...             # 可选,提供专业术语帮助识别
      response_format: json|text|srt|vtt|verbose_json
      temperature:     0.0-1.0
      stream:          true            # 仅 gpt-4o-transcribe 支持

响应:
  - response_format=json(默认):``{"text": "..."}``
  - response_format=verbose_json:多字段 ``{task, language, duration, text, words, segments, usage}``
  - text/srt/vtt:纯文本,Content-Type 不是 JSON

参考图来源
==========
GenerateReq.attachments[0] 的 ``.url``(``/v1/artifacts/<sha>.<ext>``)反查本地文件。
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Dict, Optional
from urllib.parse import urlparse

import httpx

from chayuan.server.modality.router.artifacts import find_by_filename
from chayuan.server.modality.router.connectors.base import Connector, register
from chayuan.server.modality.router.protocol import Capability, GenerateReq
from chayuan.server.modality.router.sse_v5 import (
    data_part,
    error_event,
    text_delta,
    text_end,
    text_start,
)

logger = logging.getLogger("chayuan.modality.connectors.asr.openai_compat")


def _build_endpoint(api_base: str) -> str:
    return f"{(api_base or '').rstrip('/')}/audio/transcriptions"


def _resolve_local_audio(url: str) -> Optional[tuple[bytes, str, str]]:
    """从 ``/v1/artifacts/<sha>.<ext>`` URL 反查本地文件,返 (bytes, mime, filename)。"""
    try:
        path = urlparse(url).path
    except Exception:  # noqa: BLE001
        return None
    if not path.startswith("/v1/artifacts/"):
        return None
    filename = path.rsplit("/", 1)[-1]
    local = find_by_filename(filename)
    if not local or not local.is_file():
        return None
    try:
        raw = local.read_bytes()
    except OSError as e:
        logger.warning("[asr.openai_compat] cannot read artifact %s: %r", local, e)
        return None
    # MIME 推断
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "wav"
    mime_map = {
        "mp3":  "audio/mpeg",
        "wav":  "audio/wav",
        "m4a":  "audio/mp4",
        "ogg":  "audio/ogg",
        "webm": "audio/webm",
        "flac": "audio/flac",
        "opus": "audio/ogg",
    }
    mime = mime_map.get(ext, "audio/wav")
    return raw, mime, filename


class _OpenAICompatASR(Connector):
    async def generate(self, req: GenerateReq) -> AsyncIterator[Dict]:
        if not req.attachments:
            yield error_event("ASR 必须上传一段音频文件", code="missing_audio")
            return
        ref = req.attachments[0]
        if not (ref.mime or "").startswith("audio/"):
            yield error_event(
                f"附件必须是音频类型(当前 mime={ref.mime})",
                code="invalid_audio_mime",
            )
            return
        resolved = _resolve_local_audio(ref.url or "")
        if not resolved:
            yield error_event("音频文件已过期或读取失败,请重新上传", code="audio_unreadable")
            return
        audio_bytes, audio_mime, filename = resolved

        text_id = "t0"
        yield text_start(text_id)
        # 不能 stream 时,先给一行"识别中..."占位 — 实际文本来自 yield 完成 — 这条会被 text-end 关闭
        yield text_delta(text_id, "")  # noop,触发 UI 进入"流式"状态
        yield data_part("task-progress", {"percent": 20, "message": "上传音频"})

        # 参数
        language = req.params.get("language") or req.params.get("lang")
        prompt = req.params.get("prompt")
        response_format = req.params.get("response_format") or "json"
        temperature = req.params.get("temperature")

        endpoint = _build_endpoint(req.api_base)
        files = {"file": (filename, audio_bytes, audio_mime)}
        data = {
            "model": req.model,
            "response_format": str(response_format),
        }
        if language:
            data["language"] = str(language)
        if prompt:
            data["prompt"] = str(prompt)
        if temperature is not None:
            try:
                data["temperature"] = str(float(temperature))
            except (TypeError, ValueError):
                pass
        headers = {"Authorization": f"Bearer {req.api_key}"}

        # whisper-1 同步;最长 25MB / ~30 分钟 / latency 几秒到分钟
        timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, files=files, data=data, headers=headers)
        except httpx.RequestError as e:
            yield error_event(
                f"无法连接 {req.platform_name or 'OpenAI 兼容'} ASR 接口: {e}",
                code="upstream_unreachable",
            )
            return

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
                f"{req.platform_name or 'OpenAI'} ASR {resp.status_code}: {err_msg}",
                code=f"upstream_{resp.status_code}",
            )
            return

        # 解析响应:三种格式分叉
        text_out = ""
        usage_meta: Dict = {}
        ctype = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            data_json = resp.json()
            text_out = (data_json.get("text") or "").strip()
            # verbose_json 还会带 language / duration / segments / usage
            if isinstance(data_json, dict):
                for k in ("language", "duration", "task"):
                    if k in data_json:
                        usage_meta[k] = data_json[k]
                if isinstance(data_json.get("usage"), dict):
                    usage_meta.update(data_json["usage"])
        else:
            # text/srt/vtt 模式 — 整段 body 就是文本
            text_out = resp.text.strip()

        if not text_out:
            yield error_event(
                "ASR 返回空文本(音频可能太短 / 全静音 / 无语音)",
                code="empty_transcript",
            )
            return

        # 把识别结果作为 text-delta 流式发(单次完整段,前端拼接即可)
        yield data_part("task-progress", {"percent": 90, "message": "解析转写结果"})
        yield text_delta(text_id, text_out)
        yield text_end(text_id)
        yield data_part(
            "usage",
            {
                "audio_chars": len(text_out),
                "model": req.model,
                **{k: v for k, v in usage_meta.items() if isinstance(v, (int, float, str))},
            },
        )
        yield data_part("task-progress", {"percent": 100, "message": "完成"})


@register(Capability.ASR, "openai")
class OpenAIASRConnector(_OpenAICompatASR):
    """OpenAI 官方 whisper-1 / gpt-4o-transcribe。"""


@register(Capability.ASR, "openrouter")
class OpenRouterASRConnector(_OpenAICompatASR):
    """OpenRouter 代理。"""
