"""piper-tts 离线 TTS 接入测试 — synthesize() 的第 0 档。

不实跑 piper 推理(单测不下载 60 MB 模型),只验证:
  - voice name 解析:edge-tts 风格 → 自动 fallback piper 默认 voice
  - 模型缺失 + auto-download 关 → 返 None,链路继续走 edge-tts
  - 模型存在(monkeypatch 文件)+ mock PiperVoice → 走 piper 出 wav bytes
  - WAV → mp3 通过 ffmpeg 转码(monkeypatch subprocess)
"""
from __future__ import annotations

import io
import os
import wave
from pathlib import Path

import pytest


@pytest.fixture
def piper_dir(tmp_path, monkeypatch):
    """让 chayuan.settings.CHAYUAN_ROOT 指向 tmp_path,piper_dir = tmp_path/models/piper。"""
    pdir = tmp_path / "models" / "piper"
    pdir.mkdir(parents=True)
    import chayuan.settings as _s
    monkeypatch.setattr(_s, "CHAYUAN_ROOT", tmp_path)
    # 关掉 auto-download,测试不走网
    monkeypatch.setenv("CHAYUAN_PIPER_AUTO_DOWNLOAD", "0")
    return pdir


def _make_fake_wav() -> bytes:
    """造一个最小合法 WAV(16kHz mono 0.1s 静音),给 mock PiperVoice 输出用。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)  # 0.1s
    return buf.getvalue()


def _touch_voice_files(piper_dir: Path, name: str = "zh_CN-huayan-medium") -> Path:
    """造空的 .onnx + .onnx.json,让 _resolve_piper_voice_path 通过"文件存在"检查。"""
    onnx = piper_dir / f"{name}.onnx"
    cfg = piper_dir / f"{name}.onnx.json"
    onnx.write_bytes(b"FAKE_ONNX")
    cfg.write_text('{"audio": {"sample_rate": 16000}}')
    return onnx


def test_resolve_voice_edge_style_falls_back_to_piper_default(piper_dir, monkeypatch):
    """voice='zh-CN-XiaoxiaoNeural'(edge-tts 风格)应映射到 piper 默认 huayan-medium。
    模型存在 → 返该模型路径;不存在 → 返 None。"""
    from chayuan.server.modality.audio import AudioPipeline
    pipe = AudioPipeline()
    # 不存在:返 None
    assert pipe._resolve_piper_voice_path("zh-CN-XiaoxiaoNeural") is None
    # 造默认 voice 文件
    onnx = _touch_voice_files(piper_dir)
    resolved = pipe._resolve_piper_voice_path("zh-CN-XiaoxiaoNeural")
    assert resolved == str(onnx), f"edge-tts 风格 voice 应转默认 piper,实际 {resolved}"


def test_resolve_voice_explicit_piper_prefix(piper_dir):
    """voice='piper:zh_CN-huayan-medium' 显式前缀,去前缀后查。"""
    from chayuan.server.modality.audio import AudioPipeline
    pipe = AudioPipeline()
    onnx = _touch_voice_files(piper_dir, "zh_CN-huayan-medium")
    resolved = pipe._resolve_piper_voice_path("piper:zh_CN-huayan-medium")
    assert resolved == str(onnx)


def test_resolve_voice_absolute_path(tmp_path):
    """voice 是绝对路径 .onnx → 直接用(不查 CHAYUAN_ROOT)。"""
    from chayuan.server.modality.audio import AudioPipeline
    pipe = AudioPipeline()
    bogus = tmp_path / "custom.onnx"
    bogus.write_bytes(b"X")
    resolved = pipe._resolve_piper_voice_path(str(bogus))
    assert resolved == str(bogus)
    # 不存在
    assert pipe._resolve_piper_voice_path(str(tmp_path / "missing.onnx")) is None


def test_synthesize_via_piper_returns_none_when_piper_missing(piper_dir, monkeypatch):
    """piper-tts 没装(import 报错)→ synthesize_via_piper 返 None,不抛。"""
    import sys
    from chayuan.server.modality.audio import AudioPipeline
    # 模拟 piper 没装:把 piper.voice 改成会 raise ImportError 的占位
    monkeypatch.setitem(sys.modules, "piper", None)
    monkeypatch.setitem(sys.modules, "piper.voice", None)
    pipe = AudioPipeline()
    out = pipe._synthesize_via_piper("你好", voice="zh-CN-XiaoxiaoNeural", fmt="mp3")
    assert out is None


def test_synthesize_via_piper_returns_none_when_model_missing(piper_dir):
    """piper 装了但模型文件不在 + auto-download 关 → 返 None。caller 应 fallthrough edge-tts。"""
    from chayuan.server.modality.audio import AudioPipeline
    pipe = AudioPipeline()
    out = pipe._synthesize_via_piper("你好", voice="zh-CN-XiaoxiaoNeural", fmt="mp3")
    assert out is None, "模型缺失时必须返 None,让 caller 继续 fallback"


def test_synthesize_via_piper_happy_wav(piper_dir, monkeypatch):
    """有模型 + mock PiperVoice → fmt='wav' 直接返 WAV bytes(不走 ffmpeg)。"""
    _touch_voice_files(piper_dir)
    fake_wav = _make_fake_wav()

    class _FakePV:
        @classmethod
        def load(cls, path, **kw):
            return cls()
        def synthesize_wav(self, text, wf):
            # PiperVoice.synthesize_wav 会写 wave.Wave_write — 这里写一段最小帧
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 1600)
    import sys
    import types
    fake_mod = types.ModuleType("piper.voice")
    fake_mod.PiperVoice = _FakePV
    monkeypatch.setitem(sys.modules, "piper.voice", fake_mod)
    parent = types.ModuleType("piper")
    parent.voice = fake_mod
    monkeypatch.setitem(sys.modules, "piper", parent)

    from chayuan.server.modality.audio import AudioPipeline
    pipe = AudioPipeline()
    out = pipe._synthesize_via_piper("你好", voice="zh-CN-XiaoxiaoNeural", fmt="wav")
    assert out is not None, "模型 + piper 都到位时应返 WAV bytes"
    assert out[:4] == b"RIFF", "应是合法 WAV(RIFF header)"


def test_synthesize_via_piper_wav_to_mp3_via_ffmpeg(piper_dir, monkeypatch):
    """fmt='mp3' 时应走 ffmpeg 把 WAV 转 mp3 — mock subprocess 验证。"""
    _touch_voice_files(piper_dir)

    class _FakePV:
        @classmethod
        def load(cls, path, **kw):
            return cls()
        def synthesize_wav(self, text, wf):
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 800)
    import sys
    import types
    fake_mod = types.ModuleType("piper.voice")
    fake_mod.PiperVoice = _FakePV
    monkeypatch.setitem(sys.modules, "piper.voice", fake_mod)
    parent = types.ModuleType("piper")
    parent.voice = fake_mod
    monkeypatch.setitem(sys.modules, "piper", parent)

    # mock ffmpeg call:把任何 stdin 都"转"成固定 MP3 magic 开头的 bytes
    import subprocess as _sp
    class _FakeProc:
        returncode = 0
        stdout = b"ID3\x04\x00\x00\x00\x00\x00\x00MP3FAKE"
        stderr = b""
    monkeypatch.setattr(_sp, "run", lambda *a, **kw: _FakeProc())
    # 让 _resolve_ffmpeg 返一个非空路径
    from chayuan.server.modality.audio import AudioPipeline
    pipe = AudioPipeline()
    monkeypatch.setattr(pipe, "_resolve_ffmpeg", lambda: "/fake/ffmpeg")

    out = pipe._synthesize_via_piper("你好", voice="zh-CN-XiaoxiaoNeural", fmt="mp3")
    assert out is not None
    assert out.startswith(b"ID3"), "ffmpeg 转 mp3 后应以 ID3 / MP3 magic 开头"


def test_synthesize_top_level_falls_back_to_edge_when_piper_returns_none(piper_dir, monkeypatch):
    """完整链:模型缺失 → piper 返 None → 链路走 edge-tts。
    mock edge-tts 返定值,验证整条 fallback。"""
    # piper 装了但模型不在 → _synthesize_via_piper 返 None
    # 然后应该尝试 edge-tts;我们 monkeypatch edge_tts.Communicate 返 fake stream
    import sys
    import types

    class _FakeCommunicate:
        def __init__(self, *, text, voice):
            self.text = text
        async def stream(self):
            yield {"type": "audio", "data": b"FAKE_EDGE_MP3"}

    fake_edge = types.ModuleType("edge_tts")
    fake_edge.Communicate = _FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge)

    from chayuan.server.modality.audio import AudioPipeline
    pipe = AudioPipeline()
    out = pipe.synthesize("你好", voice="zh-CN-XiaoxiaoNeural", fmt="mp3")
    assert out == b"FAKE_EDGE_MP3", (
        f"piper 模型缺失时应 fallback edge-tts(返 'FAKE_EDGE_MP3'),实际 {out!r}"
    )
