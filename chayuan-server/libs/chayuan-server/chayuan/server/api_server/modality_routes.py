"""ModalityService HTTP 路由（T10）。

暴露三端口：
- ``POST /modality/asr``           上传音频 → 文本
- ``POST /modality/tts``           文本 → 音频 bytes
- ``POST /modality/video/understand`` 视频 URL → {asr, frames, summary}

扩展端点（Multimodal UX M0）：
- ``POST /modality/ocr``           图片 → OCR 文字（复用 image_source.ocr_client）
- ``POST /modality/transcribe``    音频 → ASR 文字（复用 modality.audio.AudioPipeline）
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from chayuan.server.auth.deps import require_auth_enabled
from chayuan.server.modality import get_modality_service
from chayuan.server.modality.call_log import get_log as get_call_log
from chayuan.server.modality.call_log import record as record_call
from chayuan.server.image_source.ocr_client import resolve_port as resolve_ocr_port
from chayuan.server.image_source.ocr_client import run_ocr
from chayuan.server.image_source.pipeline import _OCR_SEMAPHORE

logger = logging.getLogger("chayuan.api.modality")

modality_router = APIRouter(prefix="/modality", tags=["Modality"])


@modality_router.post("/asr", summary="音频转文本（Whisper / faster-whisper / OpenAI）")
async def asr_endpoint(
    audio: UploadFile = File(..., description="音频文件；mp3 / wav / m4a 均可"),
    language: Optional[str] = Form(None, description="提示语言；如 zh / en；留空自动检测"),
    model: str = Form("base", description="Whisper 模型大小：tiny/base/small/medium/large"),
    user=Depends(require_auth_enabled()),
):
    data = await audio.read()
    svc = get_modality_service()
    text = svc.asr(data, language=language, model=model)
    return {"code": 0, "data": {"text": text, "length": len(text)}}


@modality_router.post("/tts", summary="文本转音频（edge-tts / OpenAI）")
async def tts_endpoint(
    text: str = Body(..., embed=True),
    voice: str = Body("zh-CN-XiaoxiaoNeural"),
    fmt: str = Body("mp3"),
    user=Depends(require_auth_enabled()),
):
    svc = get_modality_service()
    audio = svc.tts(text, voice=voice, fmt=fmt)
    if not audio:
        return {"code": 1, "msg": "TTS 未产出音频；请检查 edge-tts / OpenAI 可用性"}
    media = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg"}.get(fmt, "application/octet-stream")
    return Response(content=audio, media_type=media)


@modality_router.post("/video/understand", summary="视频理解：抽帧 + ASR + Vision")
async def video_understand_endpoint(
    video_url: str = Body(..., embed=True, description="本地路径或 http(s) URL"),
    query: str = Body("", description="针对视频的提问（可选）"),
    every_sec: int = Body(8),
    max_frames: int = Body(16),
    model: Optional[str] = Body(None),
    user=Depends(require_auth_enabled()),
):
    svc = get_modality_service()
    out = svc.understand_video(video_url, query=query,
                                every_sec=int(every_sec), max_frames=int(max_frames),
                                vision_model=model)
    return {"code": 0, "data": out}


# ──────────────────────────────────────────────────────────────────────────────
# Multimodal UX M0: /ocr + /transcribe 通用端点
# ──────────────────────────────────────────────────────────────────────────────

# 串行 ASR 调用:whisper.cpp 单次 /inference 内部是单线程 CPU 推理,两个
# 并发请求会**相互抢 CPU**导致单次 latency 翻倍,反而触发 sidecar timeout。
# 用 Semaphore(1) 强制串行,单次更快 → 整体队列吞吐反而更高 + 更稳。
# (历史上 =2 是猜的"留一个 buffer",实测得不偿失。)
_ASR_SEMAPHORE = asyncio.Semaphore(1)
_AUDIO_PIPE = None


def _get_audio_pipeline():
    """单例 AudioPipeline；monkeypatch seam。"""
    global _AUDIO_PIPE
    if _AUDIO_PIPE is None:
        from chayuan.server.modality.audio import AudioPipeline
        _AUDIO_PIPE = AudioPipeline()
    return _AUDIO_PIPE


@modality_router.post("/ocr", summary="对一张图做 OCR 并返回文字")
async def ocr_endpoint(
    file: UploadFile = File(...),
    user=Depends(require_auth_enabled()),
):
    """POST multipart 'file' (image)。

    Resp: {code: 0, data: {text, lang, confidence, box_count, elapsed_ms}}
    503 if OCR sidecar 未就绪；502 if 转写异常。
    每次调用 record 到 call_log("ocr"),前端"调用日志"面板可见。
    """
    import time as _time
    t0 = _time.perf_counter()
    data = await file.read()
    bytes_in = len(data) if data else 0
    fname = file.filename or ""
    if not data:
        record_call("ocr", success=False, duration_ms=(_time.perf_counter() - t0) * 1000,
                    bytes_in=0, error="empty file", extra={"filename": fname})
        raise HTTPException(400, "empty file")
    port = resolve_ocr_port()
    if not port:
        record_call("ocr", success=False, duration_ms=(_time.perf_counter() - t0) * 1000,
                    bytes_in=bytes_in, error="OCR sidecar not ready",
                    extra={"filename": fname})
        raise HTTPException(503, "OCR sidecar not ready")
    async with _OCR_SEMAPHORE:
        result = await run_ocr(data, port=port)
    dur = (_time.perf_counter() - t0) * 1000
    if result.error:
        record_call("ocr", success=False, duration_ms=dur, bytes_in=bytes_in,
                    error=result.error, extra={"filename": fname, "port": port})
        raise HTTPException(502, f"OCR failed: {result.error}")
    record_call(
        "ocr", success=True, duration_ms=dur, bytes_in=bytes_in,
        preview=result.text or "",
        extra={
            "filename": fname,
            "lang": result.lang,
            "box_count": result.box_count,
            "port": port,
        },
    )
    return {
        "code": 0,
        "data": {
            "text": result.text,
            "lang": result.lang,
            "confidence": result.confidence,
            "box_count": result.box_count,
            "elapsed_ms": result.elapsed_ms,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# RapidOCR sidecar 生命周期 — 给前端「设置 → 本地模型服务」用
# 后端 startup hook 已经自动拉起一次;这里的 start/stop 让用户手动启停 / 重试。
# ──────────────────────────────────────────────────────────────────────────────

@modality_router.get("/sidecar/ocr/status", summary="RapidOCR sidecar 状态")
async def ocr_sidecar_status_endpoint(user=Depends(require_auth_enabled())):
    from chayuan.server.modality.rapidocr_lifecycle import ocr_sidecar_status
    return {"code": 0, "data": ocr_sidecar_status()}


@modality_router.post("/sidecar/ocr/start", summary="启动 RapidOCR sidecar")
async def ocr_sidecar_start_endpoint(user=Depends(require_auth_enabled())):
    from chayuan.server.modality.rapidocr_lifecycle import start_ocr_sidecar
    data = await asyncio.to_thread(start_ocr_sidecar)
    return {"code": 0, "data": data}


@modality_router.post("/sidecar/ocr/stop", summary="停止 RapidOCR sidecar")
async def ocr_sidecar_stop_endpoint(user=Depends(require_auth_enabled())):
    from chayuan.server.modality.rapidocr_lifecycle import stop_ocr_sidecar
    data = await asyncio.to_thread(stop_ocr_sidecar)
    return {"code": 0, "data": data}


@modality_router.post(
    "/sidecar/ocr/auto-start",
    summary="设置 chayuan-server 启动时是否自动拉起 RapidOCR sidecar",
)
async def ocr_sidecar_set_auto_start_endpoint(
    enabled: bool = Body(..., embed=True, description="true=开机自启,false=不自动起"),
    user=Depends(require_auth_enabled()),
):
    from chayuan.server.modality.rapidocr_lifecycle import (
        ocr_sidecar_status, set_auto_start,
    )
    set_auto_start(bool(enabled))
    return {"code": 0, "data": ocr_sidecar_status()}


def _guess_audio_ext(content_type: str, filename: str) -> str:
    """根据 mime 或 filename 推断音频文件后缀。

    whisper-server / faster-whisper 都依赖文件后缀(或 magic bytes)识别格式;
    AudioPipeline._materialize 默认存为 input.audio(无扩展名)→ 无法解码。
    本函数保证下游拿到 .webm / .wav / .mp3 等正确后缀。
    """
    ct = (content_type or "").lower()
    if "webm" in ct:
        return ".webm"
    if "ogg" in ct or "opus" in ct:
        return ".ogg"
    if "wav" in ct:
        return ".wav"
    if "mp3" in ct or "mpeg" in ct:
        return ".mp3"
    if "mp4" in ct or "m4a" in ct or "aac" in ct:
        return ".m4a"
    if "flac" in ct:
        return ".flac"
    lower = (filename or "").lower()
    for ext in (".webm", ".ogg", ".wav", ".mp3", ".m4a", ".flac", ".opus"):
        if lower.endswith(ext):
            return ext
    return ".webm"  # 浏览器 MediaRecorder 默认


@modality_router.get("/asr/diag", summary="ASR 4 层后端实时可用性诊断")
async def asr_diag_endpoint(user=Depends(require_auth_enabled())):
    """对 sidecar / faster-whisper / openai-whisper / OpenAI API 各跑一次实时探测。

    专治"前端显示 ASR 已就绪、实际录音无文字"— sidecar 假 ready / Python 没装 /
    云端没配,全部能在这里看清楚。返回结构稳定,前端 onFinal=0 时自动调。
    """
    import httpx  # type: ignore

    # 1) 本地 sidecar — registry status + 实时 HTTP probe(不信缓存)
    sidecar = {
        "state": "unknown",
        "endpoint": "",
        "reachable": False,
        "http_status": 0,
        "last_error": "",
        "probe_error": "",
    }
    try:
        from chayuan.server.model_registry.local_runtime_registry import get_registry
        mgr = get_registry().get("asr")
        sidecar["state"] = mgr.status.state
        sidecar["endpoint"] = mgr.status.endpoint or ""
        sidecar["last_error"] = mgr.status.last_error or ""
        if sidecar["endpoint"]:
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    r = await c.get(sidecar["endpoint"].rstrip("/") + "/")
                    sidecar["http_status"] = r.status_code
                    sidecar["reachable"] = 200 <= r.status_code < 500
            except Exception as e:  # noqa: BLE001
                sidecar["probe_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        sidecar["probe_error"] = f"registry: {e}"

    # 2) Python: faster-whisper
    def _imp(name: str) -> dict:
        try:
            __import__(name)
            return {"installed": True, "error": ""}
        except Exception as e:  # noqa: BLE001
            return {"installed": False, "error": str(e)}

    faster_whisper = _imp("faster_whisper")
    openai_whisper = _imp("whisper")

    # 3) OpenAI API
    openai_api = {"configured": False, "error": ""}
    try:
        from chayuan.server.utils import get_OpenAI
        client = get_OpenAI()
        openai_api["configured"] = client is not None
    except Exception as e:  # noqa: BLE001
        openai_api["error"] = str(e)

    # 4) 默认模型
    try:
        from chayuan.server.capability_router import resolve_model
        default_model = resolve_model("asr") or "(未配置)"
    except Exception:  # noqa: BLE001
        default_model = "(未配置)"

    # 5) 推荐
    if sidecar["reachable"]:
        recommend = "ASR sidecar 实时可达,语音应能工作。如仍无文字,看后端 transcribe 日志。"
    elif sidecar["state"] == "ready" and not sidecar["reachable"]:
        recommend = (
            f"⚠ ASR sidecar 显示 ready @ {sidecar['endpoint']} 但实时不可达 "
            f"(probe: {sidecar['probe_error'] or 'http ' + str(sidecar['http_status'])})。"
            f"进程可能已死,请到「设置 → AI 平台 → 本地服务」停止并重新启动 ASR。"
        )
    elif faster_whisper["installed"] or openai_whisper["installed"]:
        recommend = "Python whisper 后端可用,但你的请求可能没触达 — 检查后端 transcribe 日志。"
    elif openai_api["configured"]:
        recommend = "OpenAI API 已配,可作云端 fallback;否则建议本地启 sidecar 或 pip install faster-whisper。"
    else:
        recommend = (
            "所有 ASR 后端都不可用,任选其一启用:\n"
            "1) 设置 → AI 平台 → 本地服务,启动 ASR sidecar(whisper-server)\n"
            "2) pip install faster-whisper(轻量、CPU 友好)\n"
            "3) pip install openai-whisper\n"
            "4) 配置 OpenAI API key(云端)"
        )

    return {
        "code": 0,
        "data": {
            "sidecar": sidecar,
            "faster_whisper": faster_whisper,
            "openai_whisper": openai_whisper,
            "openai_api": openai_api,
            "default_model": default_model,
            "recommend": recommend,
        },
    }


@modality_router.post("/transcribe", summary="对一段音频做 ASR 转写")
async def transcribe_endpoint(
    file: UploadFile = File(...),
    language: str = Form(""),
    session_id: str = Form(""),
    user=Depends(require_auth_enabled()),
):
    """POST multipart 'file' (audio: webm/opus/wav/mp3/ogg)。

    Form fields:
        - file: audio 字节
        - language: 语言提示("zh"/"en"/""为自动)
        - session_id: 可选;给了就走流式 LocalAgreement-2 session(每次返
          stable_increment + volatile_suffix,前端只 commit 稳定前缀;比
          chunk-by-chunk 转写准确率高,因为有跨 chunk 上下文)

    Resp 不带 session_id(向后兼容):{code: 0, data: {text, language, audio_format, audio_bytes}}
    Resp 带 session_id:                {code: 0, data: {
        text,                # = stable_increment(本次新 commit 的字符)
        hypothesis,          # 当前完整 whisper 猜测(整段 audio_pcm)
        committed_text,      # 截至本次的全部 stable 文本
        volatile_suffix,     # 当前未确认的尾部
        retroactive_change,  # whisper 改了之前 commit(罕见,仅诊断用)
        is_full,             # session 累积 audio 已达 30s 上限,前端 should restart
        chunk_count,         # session 收到的 chunk 累计
        audio_seconds,       # session 累积音频时长
        language, audio_format, audio_bytes,
    }}
    永不抛 ASR exception;失败时 text 为 "" + 顶层 code 仍 0。
    """
    import time as _time
    t0 = _time.perf_counter()
    data = await file.read()
    fname = file.filename or ""
    sid = (session_id or "").strip()
    if not data:
        record_call("asr", success=False,
                    duration_ms=(_time.perf_counter() - t0) * 1000,
                    bytes_in=0, error="empty file",
                    extra={"filename": fname, "language": language or "auto",
                           "session_id": sid})
        empty_resp = {"text": "", "language": language or "auto",
                       "audio_format": "", "audio_bytes": 0}
        if sid:
            empty_resp.update({
                "hypothesis": "", "committed_text": "", "volatile_suffix": "",
                "retroactive_change": False, "is_full": False,
                "chunk_count": 0, "audio_seconds": 0.0,
            })
        return {"code": 0, "data": empty_resp}

    import os as _os
    import tempfile as _tempfile
    ext = _guess_audio_ext(file.content_type or "", file.filename or "")
    audio_bytes = len(data)
    logger.info(
        "transcribe in: %d bytes, ct=%s, fn=%s → ext=%s, lang=%s, sid=%s",
        audio_bytes, file.content_type, file.filename, ext, language or "auto",
        sid or "(none)",
    )
    pipe = _get_audio_pipeline()

    # ─────────── 走 session(LocalAgreement-2)分支 ───────────
    if sid:
        result_data, success, err_text, preview = await _transcribe_in_session(
            sid=sid, raw_audio=data, audio_ext=ext, language=language, pipe=pipe,
        )
        result_data.update({
            "language": language or "auto",
            "audio_format": ext,
            "audio_bytes": audio_bytes,
        })
        record_call(
            "asr", success=success,
            duration_ms=(_time.perf_counter() - t0) * 1000,
            bytes_in=audio_bytes, preview=preview, error=err_text,
            extra={
                "filename": fname, "language": language or "auto",
                "audio_format": ext, "session_id": sid,
                "chunk_count": result_data.get("chunk_count", 0),
                "audio_seconds": result_data.get("audio_seconds", 0.0),
                "is_full": result_data.get("is_full", False),
            },
        )
        return {"code": 0, "data": result_data}

    # ─────────── 旧的单 chunk 分支(向后兼容,无 session_id 时走) ───────────
    tmp_path: Optional[str] = None
    text = ""
    transcribe_err = ""
    pipe_diag: list = []
    async with _ASR_SEMAPHORE:
        try:
            with _tempfile.NamedTemporaryFile(suffix=ext, delete=False,
                                                prefix="chayuan_asr_") as tf:
                tf.write(data)
                tmp_path = tf.name
            text, pipe_diag = await asyncio.to_thread(
                pipe.transcribe_with_diag, tmp_path,
                language=(language or None),
            )
            logger.info("transcribe out: %d chars, %d diag lines",
                         len(text or ""), len(pipe_diag))
        except Exception as e:  # noqa: BLE001
            logger.warning("transcribe failed: %r", e)
            text = ""
            transcribe_err = f"{type(e).__name__}: {e}"
        finally:
            if tmp_path:
                try:
                    _os.unlink(tmp_path)
                except Exception:  # noqa: BLE001
                    pass
    dur = (_time.perf_counter() - t0) * 1000
    success = bool(text)
    if not success:
        parts = []
        if transcribe_err:
            parts.append(transcribe_err)
        parts.extend(pipe_diag)
        if not parts:
            parts.append("所有 ASR 后端返空 / 见 /modality/asr/diag")
        err_text = "\n".join(parts)
    else:
        err_text = ""
    record_call(
        "asr", success=success, duration_ms=dur, bytes_in=audio_bytes,
        preview=text or "", error=err_text,
        extra={"filename": fname, "language": language or "auto",
               "audio_format": ext},
    )
    return {
        "code": 0,
        "data": {
            "text": text or "",
            "language": language or "auto",
            "audio_format": ext,
            "audio_bytes": audio_bytes,
        },
    }


async def _transcribe_in_session(
    *, sid: str, raw_audio: bytes, audio_ext: str,
    language: str, pipe,
) -> tuple:
    """session 分支主体 — ffmpeg 转 wav → 抽 PCM → AsrSession.append_chunk → 返
    (data_dict, success, err_text, preview)。

    实时模式(2026-05-17 重构后):每个 chunk **独立**送 whisper,不再累积音频、
    不再 LocalAgreement-2 二次校验。whisper 输入永远只是单 chunk(~2.5s)→
    CPU latency 0.3-2s,永不 ReadTimeout;前端拿到 stable_increment 直接 commit。
    """
    import os as _os
    import tempfile as _tempfile
    from chayuan.server.modality.asr_session import (
        PCM_BYTES_PER_SECOND, get_registry, make_wav_bytes,
    )

    err_text = ""
    preview = ""
    success = False

    # 1) 先把上传的 webm(或别的格式)转 16kHz mono wav,抽 PCM 部分
    in_tmp: Optional[str] = None
    pcm_chunk: bytes = b""
    try:
        with _tempfile.NamedTemporaryFile(
            suffix=audio_ext, delete=False, prefix="chayuan_asr_in_",
        ) as tf:
            tf.write(raw_audio)
            in_tmp = tf.name
        wav_path, wav_cleanup, transcode_err = await asyncio.to_thread(
            pipe._transcode_to_wav, in_tmp,
        )
        if wav_path is None:
            err_text = f"[session] ffmpeg 转 wav 失败:{transcode_err}"
        else:
            try:
                with open(wav_path, "rb") as f:
                    wav_full = f.read()
                # WAV header 44 字节(我们 _transcode_to_wav 出来的是 pcm_s16le,RIFF
                # header 固定 44 字节)。data chunk 之后才是 PCM。
                pcm_chunk = _extract_pcm_from_wav(wav_full)
            finally:
                if wav_cleanup is not None:
                    try: wav_cleanup.cleanup()
                    except Exception: pass  # noqa: BLE001
    except Exception as e:  # noqa: BLE001
        err_text = f"[session] 转码异常:{type(e).__name__}: {e}"
    finally:
        if in_tmp:
            try: _os.unlink(in_tmp)
            except Exception: pass  # noqa: BLE001

    if not pcm_chunk:
        return (
            {"text": "", "hypothesis": "", "committed_text": "",
             "volatile_suffix": "", "retroactive_change": False,
             "is_full": False, "chunk_count": 0, "audio_seconds": 0.0},
            False, err_text or "[session] 没有可识别的音频", "",
        )

    # 2) transcribe_fn 闭包:接 wav_bytes,写 tempfile,调 sidecar
    def transcribe_fn(wav_bytes: bytes) -> tuple:
        wav_tmp: Optional[str] = None
        try:
            with _tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False, prefix="chayuan_asr_sess_",
            ) as tf:
                tf.write(wav_bytes)
                wav_tmp = tf.name
            # _transcribe_via_sidecar_with_diag 已对 .wav 跳过转码,直接送 sidecar
            return pipe._transcribe_via_sidecar_with_diag(
                wav_tmp, language=(language or None),
            )
        except Exception as e:  # noqa: BLE001
            return "", [f"[session-transcribe] {type(e).__name__}: {e}"]
        finally:
            if wav_tmp:
                try: _os.unlink(wav_tmp)
                except Exception: pass  # noqa: BLE001

    # 3) session.append_chunk(LocalAgreement-2 应用)
    sess = get_registry().get_or_create(sid, language=(language or None))
    async with _ASR_SEMAPHORE:
        try:
            result = await asyncio.to_thread(
                sess.append_chunk, pcm_chunk, transcribe_fn,
            )
        except Exception as e:  # noqa: BLE001
            err_text = f"[session] append_chunk 异常:{type(e).__name__}: {e}"
            return (
                {"text": "", "hypothesis": "", "committed_text": sess.committed_text,
                 "volatile_suffix": "", "retroactive_change": False,
                 "is_full": False, "chunk_count": sess.chunk_count,
                 "audio_seconds": len(sess.audio_pcm) / PCM_BYTES_PER_SECOND},
                False, err_text, "",
            )

    stable_inc = result["stable_increment"]
    success = bool(stable_inc or result["volatile_suffix"] or result["hypothesis"])
    preview = stable_inc or result["hypothesis"] or ""
    if not success and result.get("diagnostics"):
        err_text = "\n".join(result["diagnostics"])

    # 满了就 drop session,下一个 chunk 客户端应该用新 session_id 重启
    if result["is_full"]:
        get_registry().drop(sid)

    return (
        {
            "text": stable_inc,  # 兼容 TranscribeResult.text 语义:本次 commit 的字符
            "hypothesis": result["hypothesis"],
            "committed_text": result["committed_text"],
            "volatile_suffix": result["volatile_suffix"],
            "retroactive_change": result["retroactive_change"],
            "is_full": result["is_full"],
            "chunk_count": result["chunk_count"],
            "audio_seconds": result["audio_seconds"],
        },
        success, err_text, preview,
    )


def _extract_pcm_from_wav(wav_bytes: bytes) -> bytes:
    """ffmpeg 出来的 wav 通常是 44 字节 header + PCM。
    严格起见 parse 一下 chunk 找 'data' 段。失败 fallback 44 字节假设。
    """
    if len(wav_bytes) < 44:
        return b""
    # RIFF header check
    if wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        return wav_bytes  # 不是 wav,原样返(让 caller 自己处理)
    # parse chunks 找 'data'
    pos = 12
    while pos + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[pos:pos + 4]
        chunk_size = int.from_bytes(wav_bytes[pos + 4:pos + 8], "little")
        if chunk_id == b"data":
            return wav_bytes[pos + 8:pos + 8 + chunk_size]
        pos += 8 + chunk_size
    # fallback
    return wav_bytes[44:]


async def _try_funasr_diarize(
    data: bytes, fname: str, ext: str, language: str,
) -> Optional[Dict[str, Any]]:
    """优先调本地 FunASR sidecar(18180)做"带说话人分离的"转写。

    FunASR + spk_model="cam++"(funasr_server.py 默认开启)返回 sentence_info
    里带 spk(int),CAM++ 聚类分话人。模型首次会自动下载(~200MB ModelScope)。

    返回:成功 → {text, segments[{start,end,text,speaker}], speaker_count,
                speaker_diarization_available}
         FunASR 没起 / 没装 / 不带 spk_model → None,由 caller 回退到 whisper
    """
    import os as _os
    import tempfile as _tempfile
    import httpx

    # FunASR sidecar 固定 127.0.0.1:18180(funasr_server.py default_port);
    # 0.3s 超时探活,不卡总路径
    base = "http://127.0.0.1:18180"
    try:
        async with httpx.AsyncClient(timeout=0.3) as cli:
            r = await cli.get(f"{base}/runtime/health")
            if r.status_code != 200:
                return None
    except Exception:  # noqa: BLE001
        return None

    in_tmp: Optional[str] = None
    try:
        with _tempfile.NamedTemporaryFile(suffix=ext, delete=False,
                                            prefix="chayuan_funasr_") as tf:
            tf.write(data)
            in_tmp = tf.name
        # FunASR 转写可能很慢(整段 + 模型懒加载首次几十秒),给 5 分钟上限
        async with httpx.AsyncClient(timeout=300.0) as cli:
            with open(in_tmp, "rb") as fp:
                files = {"file": (fname, fp, "application/octet-stream")}
                r = await cli.post(f"{base}/v1/audio/transcriptions", files=files)
        if r.status_code != 200:
            logger.warning("FunASR sidecar 返回 %d: %s", r.status_code, r.text[:300])
            return None
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not isinstance(body, dict):
            return None
        # 中文语言:对 text + segments[].text 都走 zhconv 简化
        # —— FunASR paraformer-zh 输出**可能含繁体字**(模型训练语料混杂),用户在
        # 「设置 → 系统语言」选了简体中文,期望看到简体输出。跟 whisper 路径 zhconv
        # 主路对齐(modality/audio.py:_to_simplified_chinese)。
        from chayuan.server.modality.audio import _to_simplified_chinese
        is_chinese = (language or "").lower().startswith("zh") or (language or "") == "" or language == "auto"
        text = body.get("text") or ""
        segments = body.get("segments") or []
        if is_chinese:
            text = _to_simplified_chinese(text)
            segments = [
                {**s, "text": _to_simplified_chinese(s.get("text", "") or "")}
                for s in segments if isinstance(s, dict)
            ]
        return {
            "text": text,
            "language": language or "auto",
            "audio_seconds": 0.0,
            "segments": segments,
            "speaker_diarization_available": bool(body.get("speaker_diarization_available")),
            "speaker_count": int(body.get("speaker_count") or 0),
        }
    except Exception as e:  # noqa: BLE001
        logger.info("FunASR diarize 转写失败,回退 whisper: %r", e)
        return None
    finally:
        if in_tmp:
            try: _os.unlink(in_tmp)
            except Exception: pass  # noqa: BLE001


@modality_router.post(
    "/transcribe-file",
    summary="上传整段音频文件做转写(非流式,非实时)— 拖拽 / 选文件用",
)
async def transcribe_file_endpoint(
    file: UploadFile = File(..., description="音频文件:webm/wav/mp3/m4a/flac/ogg 等"),
    language: str = Form("zh"),
    diarize: bool = Form(False, description="True=尝试说话人分离(需 FunASR sidecar + spk_model)"),
    user=Depends(require_auth_enabled()),
):
    """跟 /transcribe 的区别:
      - /transcribe:为流式录音设计,每个 chunk(~2.5s)送一次,带 session_id
      - /transcribe-file:整段音频一次性转写,适合用户从磁盘上传录音/会议录音

    流程:
      1. 接 multipart 'file'(任意 audio 格式)
      2. ffmpeg 转 16kHz mono wav
      3. 整段送 whisper.cpp /inference
      4. 繁体 → 简体,strip whisper 静音/语言名 marker
      5. 返回 {text, language, audio_seconds, segments: [{start, end, text, speaker}]}
         (当前 speaker 都标 "speaker_0";后续接入 pyannote 拿真实 diarization 时填)

    audio 大文件:
      - 后端 ASR_SEMAPHORE(2)限制并发
      - whisper.cpp /inference 内部 30s chunked,长录音它自己分段跑
      - 单文件 >5 min 时延迟可能几十秒,前端要给 loading 反馈
    """
    import os as _os
    import tempfile as _tempfile
    import time as _time

    t0 = _time.perf_counter()
    data = await file.read()
    audio_bytes = len(data) if data else 0
    fname = file.filename or "upload.audio"
    if not data:
        return {
            "code": 0,
            "data": {"text": "", "language": language or "auto", "audio_seconds": 0.0,
                     "segments": [], "speaker_diarization_available": False},
        }
    ext = _guess_audio_ext(file.content_type or "", fname)
    logger.info(
        "transcribe-file in: %d bytes, ct=%s, fn=%s → ext=%s, lang=%s, diarize=%s",
        audio_bytes, file.content_type, fname, ext, language or "auto", diarize,
    )

    # 用户请求 diarize=true 时,先尝试 FunASR sidecar(带 CAM++ 说话人分离);
    # FunASR 没装 / 没起 / 不带 spk_model → 静默回退到 whisper.cpp 单 speaker
    if diarize:
        funasr_resp = await _try_funasr_diarize(data, fname, ext, language)
        if funasr_resp and funasr_resp.get("text"):
            record_call(
                "asr", success=True,
                duration_ms=(_time.perf_counter() - t0) * 1000,
                bytes_in=audio_bytes, preview=funasr_resp["text"][:200],
                extra={
                    "filename": fname, "language": language or "auto",
                    "audio_format": ext, "endpoint": "transcribe-file",
                    "backend": "funasr+cam++",
                    "speaker_count": funasr_resp.get("speaker_count"),
                },
            )
            return {"code": 0, "data": funasr_resp}
        # 走到这里:FunASR 不可用 / 失败 / 没识别出文本 — 回退 whisper
        logger.info("FunASR diarize 不可用或失败,回退 whisper 单 speaker 路径")

    pipe = _get_audio_pipeline()

    in_tmp: Optional[str] = None
    text = ""
    pipe_diag: list = []
    transcribe_err = ""
    async with _ASR_SEMAPHORE:
        try:
            with _tempfile.NamedTemporaryFile(suffix=ext, delete=False,
                                                prefix="chayuan_asr_upload_") as tf:
                tf.write(data)
                in_tmp = tf.name
            # 复用 transcribe_with_diag:它会 ffmpeg 转 wav + sidecar /inference
            # + 繁简转换 + strip marker
            text, pipe_diag = await asyncio.to_thread(
                pipe.transcribe_with_diag, in_tmp,
                language=(language or None),
            )
            logger.info("transcribe-file out: %d chars, %d diag lines",
                         len(text or ""), len(pipe_diag))
        except Exception as e:  # noqa: BLE001
            logger.warning("transcribe-file failed: %r", e)
            transcribe_err = f"{type(e).__name__}: {e}"
        finally:
            if in_tmp:
                try: _os.unlink(in_tmp)
                except Exception: pass  # noqa: BLE001

    success = bool(text)
    err_text = "" if success else "\n".join([transcribe_err, *pipe_diag] if transcribe_err else pipe_diag)
    record_call(
        "asr", success=success,
        duration_ms=(_time.perf_counter() - t0) * 1000,
        bytes_in=audio_bytes, preview=text or "", error=err_text,
        extra={
            "filename": fname, "language": language or "auto",
            "audio_format": ext, "endpoint": "transcribe-file",
        },
    )

    # 当前不带 diarization:只返一个整段 segment 占位,speaker 都标 speaker_0。
    # 接入 pyannote.audio 后,这里会变成多个 segment + 真实 speaker label。
    segments = [{
        "start": 0.0, "end": 0.0,  # whisper.cpp plain text 没时间戳
        "text": text,
        "speaker": "speaker_0",
    }] if text else []

    # 成功时也带 zhconv 缺失提示 — 用户看到繁体不会再以为是 bug,知道按右上角「简」转
    from chayuan.server.modality.audio import zhconv_missing_diag as _zhconv_diag
    diag_lines: list = []
    if not success and err_text:
        diag_lines.append(err_text)
    zh_diag = _zhconv_diag()
    if zh_diag:
        diag_lines.append(zh_diag)

    return {
        "code": 0,
        "data": {
            "text": text or "",
            "language": language or "auto",
            "audio_seconds": 0.0,  # 没解析 wav 头,留 0.0 占位
            "segments": segments,
            # 客户端据此判断要不要把"Speaker N:"前缀加上
            "speaker_diarization_available": False,
            "diagnostics": "\n".join(diag_lines),
        },
    }


@modality_router.get("/call-log/{key}", summary="读取某 capability 最近的调用日志")
async def call_log_endpoint(
    key: str,
    limit: int = 200,
    user=Depends(require_auth_enabled()),
):
    """key 当前支持:asr / ocr(其他 capability 暂不 record)。返回时间升序数组。"""
    limit = max(1, min(int(limit or 200), 200))
    entries = get_call_log(key, limit=limit)
    return {"code": 0, "data": {"key": key, "limit": limit, "entries": entries}}
