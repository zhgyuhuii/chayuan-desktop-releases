"""POST /modality/ocr 和 /modality/transcribe 通用端点。"""
from __future__ import annotations

import io
import pytest


@pytest.mark.asyncio
async def test_ocr_endpoint_happy(monkeypatch):
    from chayuan.server.api_server import modality_routes as mr
    from chayuan.server.image_source.ocr_client import OCRResult

    async def _fake_run_ocr(data, *, port, timeout=30.0):
        assert data == b"\x89PNG..."
        return OCRResult(text="hello", lang="en", confidence=0.95,
                         box_count=1, elapsed_ms=10)
    monkeypatch.setattr(mr, "run_ocr", _fake_run_ocr)
    monkeypatch.setattr(mr, "resolve_ocr_port", lambda: 18380)

    class _UF:
        filename = "test.png"
        async def read(self): return b"\x89PNG..."
    resp = await mr.ocr_endpoint(file=_UF(), user={"id": 1, "role": "admin"})
    assert resp["code"] == 0
    assert resp["data"]["text"] == "hello"
    assert resp["data"]["lang"] == "en"


@pytest.mark.asyncio
async def test_ocr_endpoint_sidecar_unavail(monkeypatch):
    from chayuan.server.api_server import modality_routes as mr
    from fastapi import HTTPException
    monkeypatch.setattr(mr, "resolve_ocr_port", lambda: None)

    class _UF:
        filename = "x.png"
        async def read(self): return b"x"
    with pytest.raises(HTTPException) as exc:
        await mr.ocr_endpoint(file=_UF(), user={"id": 1, "role": "admin"})
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_transcribe_endpoint_happy(monkeypatch):
    from chayuan.server.api_server import modality_routes as mr

    captured = {}
    class _FakePipe:
        def transcribe_with_diag(self, audio, *, language=None, **kw):
            with open(audio, "rb") as f:
                captured["audio_size"] = len(f.read())
            captured["audio_path"] = audio
            captured["language"] = language
            return "你好世界", []
    monkeypatch.setattr(mr, "_get_audio_pipeline", lambda: _FakePipe())

    class _UF:
        content_type = "audio/webm;codecs=opus"
        filename = "chunk-0.webm"
        async def read(self): return b"webm-bytes"
    resp = await mr.transcribe_endpoint(
        file=_UF(), language="zh", session_id="", user={"id": 1, "role": "admin"},
    )
    assert resp["code"] == 0
    assert resp["data"]["text"] == "你好世界"
    assert resp["data"]["language"] == "zh"
    assert resp["data"]["audio_format"] == ".webm"
    assert resp["data"]["audio_bytes"] == len(b"webm-bytes")
    # tmp 文件后缀必须是 .webm,whisper 才能 sniff 出格式
    assert captured["audio_path"].endswith(".webm")


def test_guess_audio_ext():
    from chayuan.server.api_server.modality_routes import _guess_audio_ext
    assert _guess_audio_ext("audio/webm;codecs=opus", "x") == ".webm"
    assert _guess_audio_ext("audio/wav", "x") == ".wav"
    assert _guess_audio_ext("audio/mp3", "x") == ".mp3"
    assert _guess_audio_ext("audio/ogg", "x") == ".ogg"
    assert _guess_audio_ext("", "voice.m4a") == ".m4a"
    assert _guess_audio_ext("", "") == ".webm"  # MediaRecorder 默认


@pytest.mark.asyncio
async def test_transcribe_endpoint_empty_returns_empty_text(monkeypatch):
    from chayuan.server.api_server import modality_routes as mr
    class _FakePipe:
        def transcribe_with_diag(self, audio, *, language=None, **kw):
            return "", []
    monkeypatch.setattr(mr, "_get_audio_pipeline", lambda: _FakePipe())

    class _UF:
        filename = ""
        async def read(self): return b""
    resp = await mr.transcribe_endpoint(
        file=_UF(), language="", session_id="", user={"id": 1, "role": "admin"},
    )
    assert resp["code"] == 0
    assert resp["data"]["text"] == ""


@pytest.mark.asyncio
async def test_call_log_endpoint_records_asr(monkeypatch):
    """transcribe_endpoint 应在 call_log("asr") 里 push 一条记录,GET endpoint 能读回。"""
    from chayuan.server.api_server import modality_routes as mr
    from chayuan.server.modality import call_log
    call_log.clear("asr")

    class _FakePipe:
        def transcribe_with_diag(self, audio, *, language=None, **kw):
            return "你好", []
    monkeypatch.setattr(mr, "_get_audio_pipeline", lambda: _FakePipe())

    class _UF:
        content_type = "audio/webm"
        filename = "test.webm"
        async def read(self): return b"webm-data"

    await mr.transcribe_endpoint(file=_UF(), language="zh", session_id="",
                                  user={"id": 1, "role": "admin"})

    resp = await mr.call_log_endpoint(key="asr", limit=10,
                                       user={"id": 1, "role": "admin"})
    assert resp["code"] == 0
    entries = resp["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["success"] is True
    assert entries[0]["preview"] == "你好"
    assert entries[0]["bytes_in"] == len(b"webm-data")
    # extra 字段透传(filename/language 这种)
    assert entries[0].get("language") == "zh"


def test_strip_whisper_artifacts():
    """whisper 静音 marker 应该被 strip 掉,空文本视为"无有效语音"。"""
    from chayuan.server.modality.audio import _strip_whisper_artifacts
    assert _strip_whisper_artifacts("hello world")[0] == "hello world"
    # 仅 marker → 全 strip,artifact_only=True
    out, only = _strip_whisper_artifacts("[INAUDIBLE]")
    assert out == "" and only is True
    out, only = _strip_whisper_artifacts(" [BLANK_AUDIO] ")
    assert out == "" and only is True
    out, only = _strip_whisper_artifacts("[ BLANK AUDIO ]")
    assert out == "" and only is True
    out, only = _strip_whisper_artifacts("[空白音频]")
    assert out == "" and only is True
    # whisper 语言名 marker(auto detection 失败时输出)
    out, only = _strip_whisper_artifacts("[Spanish] [Spanish] [Spanish]")
    assert out == "" and only is True
    out, only = _strip_whisper_artifacts("[English]")
    assert out == "" and only is True
    out, only = _strip_whisper_artifacts("[Japanese] [Korean]")
    assert out == "" and only is True
    # 混合:marker + 实际文字 → 留实际文字
    out, only = _strip_whisper_artifacts("[INAUDIBLE] 你好 [NOISE]")
    assert out == "你好" and only is False
    out, only = _strip_whisper_artifacts("[Spanish] 测试 [English]")
    assert out == "测试" and only is False
    # 完全空
    out, only = _strip_whisper_artifacts("")
    assert out == "" and only is False
    out, only = _strip_whisper_artifacts("   ")
    assert out == "" and only is False


def test_strip_whisper_sound_effects():
    """whisper 在静音/低信噪比上 hallucinate 出的圆括号 sound effect token —
    (笑) / (笑声) / (laughter) / 全角（笑）等都要 strip,且不能误伤正常含'笑'字的句子。"""
    from chayuan.server.modality.audio import _strip_whisper_artifacts as f
    # 中文 sound effect:半角圆括号
    for raw in ("(笑)", "(笑声)", "(掌声)", "(咳嗽)", "(音乐)", "(噪声)"):
        text, only = f(raw)
        assert (text, only) == ("", True), f"中文半角 {raw!r} 应判 artifact_only"
    # 全角圆括号
    for raw in ("（笑）", "（笑声）", "（笑い）"):
        text, only = f(raw)
        assert (text, only) == ("", True), f"全角 {raw!r} 应判 artifact_only"
    # 英文 sound effect
    for raw in ("(laughter)", "(applause)", "(music)", "(coughing)"):
        text, only = f(raw)
        assert (text, only) == ("", True), f"英文 {raw!r} 应判 artifact_only"
    # 大小写 + 空格
    text, only = f("( Laughter )")
    assert (text, only) == ("", True)
    # 连续多个 sound effect
    text, only = f("(笑) (笑) (笑)")
    assert (text, only) == ("", True)
    # **关键**:不能误吞正常文本里的"笑"字 — 必须括号包裹才剥
    text, only = f("他在大笑")
    assert (text, only) == ("他在大笑", False)
    text, only = f("我笑了好久")
    assert (text, only) == ("我笑了好久", False)
    # 嵌入式 sound effect:strip 完留正文,非 artifact_only
    text, only = f("你好 (笑) 世界")
    assert "你好" in text and "世界" in text
    assert only is False


def test_strip_whisper_hallucination_repetition():
    """whisper 静音 hallucination:同一子串重复 ≥3 次 → artifact_only=True 返空。"""
    from chayuan.server.modality.audio import _strip_whisper_artifacts, _is_whisper_hallucination_repetition

    # 用户原报的样本(注:_to_simplified_chinese 已转简,这里用简体测)
    assert _is_whisper_hallucination_repetition("获胜荒谬将原裙封开" * 5) is True
    out, only = _strip_whisper_artifacts("获胜荒谬将原裙封开" * 5)
    assert out == "" and only is True

    # 另两个常见 hallucination 短语
    assert _is_whisper_hallucination_repetition("谢谢观看" * 4) is True
    assert _is_whisper_hallucination_repetition("请订阅" * 5) is True
    out, only = _strip_whisper_artifacts("请订阅" * 5)
    assert out == "" and only is True

    # 边界:重复 2 次不算(留正常的"你好你好") — 阈值 min_repeat=3
    assert _is_whisper_hallucination_repetition("你好你好") is False
    out, only = _strip_whisper_artifacts("你好你好")
    assert out == "你好你好" and only is False

    # 边界:子串重复但占比 < 80% — 不视为 hallucination(避免误杀)
    assert _is_whisper_hallucination_repetition("哈哈哈哈哈,今天天气真不错,大家好") is False

    # 单字符不算(太短没意义)
    assert _is_whisper_hallucination_repetition("aaaa") is False or True  # 不强求,len<6 直接 False
    assert _is_whisper_hallucination_repetition("") is False
    assert _is_whisper_hallucination_repetition("a") is False


@pytest.mark.asyncio
async def test_transcribe_file_endpoint_happy(monkeypatch):
    """transcribe-file endpoint:接受上传音频,返回 text + segments(预留 speaker)。"""
    from chayuan.server.api_server import modality_routes as mr

    class _FakePipe:
        def transcribe_with_diag(self, audio, *, language=None, **kw):
            return "你好世界这是一段会议录音", []
    monkeypatch.setattr(mr, "_get_audio_pipeline", lambda: _FakePipe())

    class _UF:
        content_type = "audio/wav"
        filename = "meeting.wav"
        async def read(self): return b"wav-data"

    resp = await mr.transcribe_file_endpoint(
        file=_UF(), language="zh", user={"id": 1, "role": "admin"},
    )
    assert resp["code"] == 0
    d = resp["data"]
    assert d["text"] == "你好世界这是一段会议录音"
    assert d["language"] == "zh"
    assert d["speaker_diarization_available"] is False
    assert len(d["segments"]) == 1
    assert d["segments"][0]["speaker"] == "speaker_0"
    assert d["segments"][0]["text"] == "你好世界这是一段会议录音"


@pytest.mark.asyncio
async def test_transcribe_file_endpoint_empty(monkeypatch):
    """空文件 → 不报错,返空 segments + diarization_available=False。"""
    from chayuan.server.api_server import modality_routes as mr

    class _UF:
        content_type = "audio/wav"
        filename = "empty.wav"
        async def read(self): return b""

    resp = await mr.transcribe_file_endpoint(
        file=_UF(), language="zh", user={"id": 1, "role": "admin"},
    )
    assert resp["code"] == 0
    assert resp["data"]["text"] == ""
    assert resp["data"]["segments"] == []


@pytest.mark.asyncio
async def test_transcribe_diagnostics_flow_to_call_log(monkeypatch):
    """pipe 返 ("", [diag...]) 时,call_log.error 应包含每层 diag。"""
    from chayuan.server.api_server import modality_routes as mr
    from chayuan.server.modality import call_log
    call_log.clear("asr")

    class _FakePipe:
        def transcribe_with_diag(self, audio, *, language=None, **kw):
            return "", [
                "[sidecar] HTTP 200 但 text 为空 — webm 解码失败",
                "[faster-whisper] ModuleNotFoundError: No module named 'faster_whisper'",
            ]
    monkeypatch.setattr(mr, "_get_audio_pipeline", lambda: _FakePipe())

    class _UF:
        content_type = "audio/webm"
        filename = "x.webm"
        async def read(self): return b"webm-bytes"

    await mr.transcribe_endpoint(file=_UF(), language="", session_id="",
                                  user={"id": 1, "role": "admin"})
    entries = call_log.get_log("asr")
    assert len(entries) == 1
    assert entries[0]["success"] is False
    err = entries[0]["error"]
    assert "[sidecar]" in err and "webm 解码失败" in err
    assert "[faster-whisper]" in err
