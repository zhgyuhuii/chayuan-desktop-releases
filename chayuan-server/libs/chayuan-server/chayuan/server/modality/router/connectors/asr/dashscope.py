"""DashScope native ASR Connector — qwen3-asr-flash / qwen-asr / paraformer。

为什么不复用 OpenAI 兼容
========================

DashScope 的 ``/compatible-mode/v1`` 没有 ``/audio/transcriptions`` 端点;ASR
跟 TTS / image 一样,只在 native ``/api/v1/services/aigc/multimodal-generation``
上发布,而且请求体跟 OpenAI Whisper 的 multipart 完全不同 —— DashScope 走
JSON,把音频以 ``data:<mime>;base64,...`` URL 形态塞进 ``input.messages``
里的 ``content`` 块。

::

    POST /api/v1/services/aigc/multimodal-generation/generation
    {
      "model": "qwen3-asr-flash",
      "input": {
        "messages": [
          {"role": "system", "content": [{"text": ""}]},
          {"role": "user",   "content": [{"audio": "data:audio/wav;base64,..."}]}
        ]
      },
      "parameters": {
        "asr_options": {
          "enable_lid":  true,    # language id 自动识别语种
          "enable_itn":  false,   # inverse text normalization,默认关
          "language":    "zh"     # 可选,强制指定语言 → 跳过 lid
        }
      }
    }

响应:
::

    {
      "output": {
        "choices": [
          {
            "finish_reason": "stop",
            "message": {
              "role": "assistant",
              "content": [{"text": "识别的文字"}]
            }
          }
        ]
      },
      "usage": {"audio_tokens": ..., "input_tokens": ..., "output_tokens": ...}
    }

音频大小限制
============
qwen3-asr-flash 单次最长 3 分钟 / 10MB,超长场景需要切片 — 本 Connector 不做切片,
让用户在前端切好或选 paraformer-realtime-v2 走流式(本 PR 暂不支持流式)。

paraformer 系列
===============
paraformer-realtime-v2 / paraformer-v2 是异步 + 流式的录音文件转写,有自己专属
的 async-task 端点(``/api/v1/services/audio/asr/transcription``)。本 Connector
当前只覆盖 qwen3-asr-flash / qwen-asr-* 的 multimodal-generation 同步路径;
paraformer 流式后续补单独的连接器。
"""

from __future__ import annotations

import base64
import logging
import re
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

logger = logging.getLogger("chayuan.modality.connectors.asr.dashscope")


def _build_endpoint(api_base: str) -> str:
    base = (api_base or "").rstrip("/")
    if base.endswith("/api/v1"):
        return f"{base}/services/aigc/multimodal-generation/generation"
    base = re.sub(r"/compatible-mode/v\d+$", "", base)
    base = re.sub(r"/v\d+$", "", base)
    return f"{base}/api/v1/services/aigc/multimodal-generation/generation"


def _resolve_local_audio(url: str) -> Optional[tuple[bytes, str]]:
    """从 ``/v1/artifacts/<sha>.<ext>`` URL 反查本地文件 → (bytes, mime)。"""
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
        logger.warning("[asr.dashscope] cannot read artifact %s: %r", local, e)
        return None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "wav"
    mime_map = {
        "mp3":  "audio/mpeg",
        "wav":  "audio/wav",
        "m4a":  "audio/mp4",
        "ogg":  "audio/ogg",
        "webm": "audio/webm",
        "flac": "audio/flac",
        "opus": "audio/ogg",
        "amr":  "audio/amr",
    }
    return raw, mime_map.get(ext, "audio/wav")


def _to_data_url(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


@register(Capability.ASR, "dashscope")
class DashScopeASRConnector(Connector):
    """qwen3-asr-flash / qwen-asr-* — DashScope native multimodal-generation。"""

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
        audio_bytes, audio_mime = resolved
        # qwen3-asr-flash 上限 10MB / 3 分钟 — 本地先粗校验大小,避免无谓上传
        if len(audio_bytes) > 10 * 1024 * 1024:
            yield error_event(
                f"音频文件过大({len(audio_bytes)/1024/1024:.1f}MB),qwen3-asr-flash 限 10MB / 3 分钟",
                code="audio_too_large",
            )
            return

        text_id = "t0"
        yield text_start(text_id)
        yield text_delta(text_id, "")  # 触发 UI 进入流式态
        yield data_part("task-progress", {"percent": 20, "message": "提交识别请求"})

        # 参数
        # language:用户显式指定 → 关 lid;否则开 lid 让模型自动判断
        # enable_itn:是否做数字/单位标准化("二零二六" → "2026")
        language = req.params.get("language") or req.params.get("lang")
        enable_itn = bool(req.params.get("enable_itn", False))

        asr_options: Dict = {"enable_itn": enable_itn}
        if language:
            asr_options["language"] = str(language)
            asr_options["enable_lid"] = False
        else:
            asr_options["enable_lid"] = True

        body: Dict = {
            "model": req.model,
            "input": {
                "messages": [
                    {"role": "system", "content": [{"text": ""}]},
                    {
                        "role": "user",
                        "content": [{"audio": _to_data_url(audio_bytes, audio_mime)}],
                    },
                ]
            },
            "parameters": {"asr_options": asr_options},
        }
        headers = {
            "Authorization": f"Bearer {req.api_key}",
            "Content-Type": "application/json",
        }

        endpoint = _build_endpoint(req.api_base)
        # qwen3-asr-flash 同步;3 分钟音频典型延迟 5-30 秒
        timeout = httpx.Timeout(connect=10.0, read=180.0, write=60.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=body, headers=headers)
        except httpx.RequestError as e:
            yield error_event(
                f"无法连接 DashScope ASR 接口: {e}",
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
                f"DashScope ASR {resp.status_code}: {msg}",
                code=f"upstream_{resp.status_code}",
            )
            return

        data = resp.json()
        output = data.get("output") or {}
        # DashScope ASR 响应形态在不同模型间有差异,按优先级试:
        #   1. qwen3-asr-flash / qwen-asr 走 chat-shape:
        #        output.choices[0].message.content[].text
        #   2. paraformer / 老式平铺:output.text
        #   3. paraformer 按句返回:output.sentences[].text
        #   4. cosyvoice 等少数变种:output.transcript / output.recognition
        # 都拿不到才视为真空响应。
        text_out = ""
        detected_lang: Optional[str] = None

        def _collect_text_from_content(c) -> str:
            """递归在 content list / dict / str 里捞 'text' 字段(忽略 audio block)。"""
            buf = ""
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                for blk in c:
                    buf += _collect_text_from_content(blk)
                return buf
            if isinstance(c, dict):
                # 优先 text 字段;某些 block 是 {"type":"text","text":"..."},仍然命中
                if isinstance(c.get("text"), str):
                    return c["text"]
                # 嵌套 content(极少数响应包了一层)
                if "content" in c:
                    return _collect_text_from_content(c["content"])
            return ""

        # 1. chat-shape choices
        for ch in output.get("choices") or []:
            msg = (ch or {}).get("message") or {}
            text_out += _collect_text_from_content(msg.get("content"))
        # 2. 平铺 output.text
        if not text_out and isinstance(output.get("text"), str):
            text_out = output["text"]
        # 3. paraformer 按句:output.sentences = [{"text":"...","begin_time":..,"end_time":..}]
        if not text_out and isinstance(output.get("sentences"), list):
            parts: list[str] = []
            for s in output["sentences"]:
                if isinstance(s, dict) and isinstance(s.get("text"), str):
                    parts.append(s["text"])
            text_out = "".join(parts)
        # 4. 兜底 — transcript / recognition 字段
        for key in ("transcript", "recognition"):
            if not text_out and isinstance(output.get(key), str):
                text_out = output[key]
                break

        # detected language(qwen3-asr-flash 在 lid_result block;paraformer 在 output.language)
        if isinstance(output.get("language"), str):
            detected_lang = output["language"]
        else:
            for ch in output.get("choices") or []:
                msg = (ch or {}).get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and isinstance(blk.get("lid_result"), str):
                            detected_lang = blk["lid_result"]
                            break

        text_out = text_out.strip()
        if not text_out:
            # 把上游响应(裁短)塞进诊断,免得用户一脸懵 — 看 raw 能定位是
            # 真空音频 / 上游变结构 / 鉴权但 hidden 错误
            import json as _json
            raw_dump = _json.dumps(data, ensure_ascii=False)[:600]
            logger.warning(
                "[asr.dashscope] empty transcript task=%s model=%s output_keys=%s raw=%s",
                req.message_id, req.model, list(output.keys()), raw_dump,
            )
            yield error_event(
                "ASR 返回空文本 — 可能是上游音频识别为空 / 响应结构与连接器不匹配。"
                f"上游 output 字段: {list(output.keys())};原始(截断): {raw_dump}",
                code="empty_transcript",
            )
            return

        yield data_part("task-progress", {"percent": 90, "message": "解析转写结果"})
        yield text_delta(text_id, text_out)
        yield text_end(text_id)

        usage = data.get("usage") or {}
        meta: Dict = {
            "audio_chars": len(text_out),
            "audio_size_bytes": len(audio_bytes),
            "model": req.model,
        }
        if detected_lang:
            meta["language"] = detected_lang
        for k, v in usage.items():
            if isinstance(v, (int, float, str)):
                meta[k] = v
        yield data_part("usage", meta)
        yield data_part("task-progress", {"percent": 100, "message": "完成"})
