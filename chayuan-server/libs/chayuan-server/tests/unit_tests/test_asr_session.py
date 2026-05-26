"""AsrSession 实时模式单测(2026-05-17 重写)。

老的 LocalAgreement-2 + 滑窗实现已被替换为"每 chunk 独立转写,实时输出"
简化版,本文件相应重写。LocalAgreement 工具函数(common_prefix_len /
local_agreement_2 / compute_increment)在 local_agreement.py 仍存在但已不被
生产代码引用 — 这里也不再测它们。
"""
from __future__ import annotations

import pytest


def test_session_append_chunk_independent_transcribe():
    """每 chunk 独立跑 whisper — fake_transcribe 应只收到当前 chunk 的 WAV,
    不应有累积音频。"""
    from chayuan.server.modality.asr_session import AsrSession, PCM_BYTES_PER_SECOND

    received_sizes: list[int] = []
    def fake(wav: bytes):
        # WAV header 44 字节 + PCM 数据
        received_sizes.append(len(wav) - 44)
        return "你好", []

    sess = AsrSession(session_id="t1")
    chunk_1s = b"\x00" * (1 * PCM_BYTES_PER_SECOND)
    chunk_2s = b"\x00" * (2 * PCM_BYTES_PER_SECOND)
    sess.append_chunk(chunk_1s, fake)
    sess.append_chunk(chunk_2s, fake)
    sess.append_chunk(chunk_1s, fake)

    assert received_sizes == [
        1 * PCM_BYTES_PER_SECOND,
        2 * PCM_BYTES_PER_SECOND,
        1 * PCM_BYTES_PER_SECOND,
    ], "每 chunk 应独立喂 whisper,不累积上一片"


def test_session_committed_text_grows_per_chunk():
    """committed_text 每 chunk 累加;中文之间不加空格,英文/数字之间加空格防粘连。"""
    from chayuan.server.modality.asr_session import AsrSession, PCM_BYTES_PER_SECOND

    hyps = iter(["你好", "今天", "hello", "world"])
    def fake(wav): return next(hyps), []

    sess = AsrSession(session_id="t2")
    chunk = b"\x00" * PCM_BYTES_PER_SECOND
    r1 = sess.append_chunk(chunk, fake)
    assert r1["committed_text"] == "你好"
    assert r1["stable_increment"] == "你好"

    r2 = sess.append_chunk(chunk, fake)
    # 中文邻接,不加空格
    assert r2["committed_text"] == "你好今天"
    assert r2["stable_increment"] == "今天"

    r3 = sess.append_chunk(chunk, fake)
    # 中文 → 英文邻接(尾 '天' 不是 ASCII word char)→ 不加空格
    assert r3["committed_text"] == "你好今天hello"

    r4 = sess.append_chunk(chunk, fake)
    # 英文 → 英文邻接,塞空格防粘连
    assert r4["committed_text"] == "你好今天hello world"


def test_session_empty_chunk_does_not_grow_committed():
    """whisper 返空(静音/marker 被 strip)时,committed_text 不变。"""
    from chayuan.server.modality.asr_session import AsrSession, PCM_BYTES_PER_SECOND

    sess = AsrSession(session_id="t3")
    sess.committed_text = "已有文字"
    chunk = b"\x00" * PCM_BYTES_PER_SECOND
    r = sess.append_chunk(chunk, lambda wav: ("", []))
    assert r["committed_text"] == "已有文字", "whisper 返空时 committed_text 应不变"
    assert r["stable_increment"] == ""
    assert r["volatile_suffix"] == ""


def test_session_no_volatile_no_retroactive_in_realtime_mode():
    """实时模式没有 volatile / retroactive 概念 — 这些字段永远空 / False。"""
    from chayuan.server.modality.asr_session import AsrSession, PCM_BYTES_PER_SECOND

    sess = AsrSession(session_id="t4")
    chunk = b"\x00" * PCM_BYTES_PER_SECOND
    r = sess.append_chunk(chunk, lambda wav: ("anything", []))
    assert r["volatile_suffix"] == ""
    assert r["retroactive_change"] is False
    assert r["is_full"] is False  # 不再有"满了换 session"


def test_session_chunk_count_and_audio_seconds():
    """chunk_count 单调递增;audio_seconds 反映**当前 chunk**长度(不是累积)。"""
    from chayuan.server.modality.asr_session import AsrSession, PCM_BYTES_PER_SECOND

    sess = AsrSession(session_id="t5")
    r1 = sess.append_chunk(b"\x00" * (1 * PCM_BYTES_PER_SECOND), lambda w: ("", []))
    r2 = sess.append_chunk(b"\x00" * (3 * PCM_BYTES_PER_SECOND), lambda w: ("", []))
    assert r1["chunk_count"] == 1
    assert r2["chunk_count"] == 2
    assert r1["audio_seconds"] == 1.0
    assert r2["audio_seconds"] == 3.0, "audio_seconds 是单 chunk 长度,不再累积"


def test_session_diagnostics_passthrough():
    """transcribe_fn 返回的 diag list 应原样进 result['diagnostics']。"""
    from chayuan.server.modality.asr_session import AsrSession, PCM_BYTES_PER_SECOND

    sess = AsrSession(session_id="t6")
    diag = ["[sidecar] HTTP 200", "[strip] 去除 [BLANK_AUDIO]"]
    r = sess.append_chunk(
        b"\x00" * PCM_BYTES_PER_SECOND,
        lambda w: ("ok", diag),
    )
    assert r["diagnostics"] == diag


def test_make_wav_bytes_header():
    """make_wav_bytes 生成 44 字节标准 WAV header。"""
    from chayuan.server.modality.asr_session import make_wav_bytes
    pcm = b"\x00\x01" * 100  # 200 bytes PCM
    wav = make_wav_bytes(pcm)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    assert len(wav) == 44 + 200


def test_registry_evict_idle(monkeypatch):
    """registry 自动 evict 闲置超 SESSION_TTL_SEC 的 session。"""
    import time as _time
    from chayuan.server.modality import asr_session as mod
    from chayuan.server.modality.asr_session import AsrSessionRegistry

    reg = AsrSessionRegistry()
    s1 = reg.get_or_create("alive")
    s2 = reg.get_or_create("stale")
    s2.last_active_at = _time.time() - mod.SESSION_TTL_SEC - 10
    # 再请求 alive 触发 _evict_idle
    reg.get_or_create("alive")
    assert reg.size() == 1
    reg.drop("alive")
    assert reg.size() == 0


def test_registry_get_or_create_singleton():
    """同 session_id 多次 get_or_create 返同一实例。"""
    from chayuan.server.modality.asr_session import AsrSessionRegistry
    reg = AsrSessionRegistry()
    a = reg.get_or_create("same-id")
    b = reg.get_or_create("same-id")
    assert a is b
