"""ASR session — **每 chunk 独立转写,实时输出,不做跨 chunk 校验**。

历史:本模块曾用 UFAL whisper-streaming 风格 LocalAgreement-2 跨 chunk 二次确认,
配合滑窗 + 软/硬 trim 控 whisper 输入大小。但在用户的 CPU 环境上:
  - 累积音频反复触发 sidecar ReadTimeout(whisper 跑 12-15s 累积音频 > 45s)
  - LocalAgreement 延迟 commit,体感"录了 5 秒还没字"
  - 复杂逻辑长尾 bug 多

2026-05-17 用户决定:**不再做验证,实时输出**。
新设计:每个 chunk 进来就跑独立 whisper,出什么就 commit 什么。
  - 每次 whisper 输入 = 单 chunk audio(~2.5s 切片)→ CPU latency 0.3-2s,永不 timeout
  - 输出直接累加到 committed_text,前端立刻看到
  - 没有 volatile/校准/滑窗/段切;最少认知负担

权衡:
  ✓ 实时(<1s 出字)
  ✓ 永不 ReadTimeout(whisper 永远只看 2.5s 音频)
  ✓ 代码量 1/4
  ✗ chunk 边界偶尔切词(2.5s 切片,中文按字识别,边界损失通常 ≤ 1 字)
  ✗ 无跨 chunk 上下文修正(whisper 自身的 prompt 也不带,因为每 chunk 独立调)

注:asr_session API 形状(返回字段、SessionRegistry)保持不变 — 兼容前端
useMicRecorder.committed_text-based 对账逻辑,不需要前端改。
"""
from __future__ import annotations

import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

# 单声道 16k 16bit PCM:每秒 32000 字节
PCM_SAMPLE_RATE = 16000
PCM_BYTES_PER_SECOND = PCM_SAMPLE_RATE * 2  # mono, 16-bit

SESSION_TTL_SEC = 300  # 5 分钟 idle 自动 evict


def make_wav_bytes(pcm_data: bytes, sample_rate: int = PCM_SAMPLE_RATE) -> bytes:
    """把裸 PCM 16-bit mono 拼上 WAV header,whisper 可解码。"""
    n = len(pcm_data)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + n, b"WAVE",
        b"fmt ", 16, 1, 1,  # PCM format, 1 channel
        sample_rate,
        sample_rate * 2,  # byte rate (1 channel * 16bit / 8)
        2, 16,            # block align, bits per sample
        b"data", n,
    )
    return header + pcm_data


# transcribe 回调签名:接 WAV bytes,返 (text, diagnostics_list)
TranscribeFn = Callable[[bytes], Tuple[str, list]]


@dataclass
class AsrSession:
    """实时 ASR session — 每 chunk 独立转写,append 到 committed_text。

    state 极简:
      committed_text  累计文本,前端拿来对账(committed_text - lastCommittedAll = 本次增量)
      chunk_count     用过的 chunk 数(call_log 用)
      last_active_at  evict 用

    不保留:audio_pcm / last_hyp / segment_committed_prefix。每个 chunk 处理完即释放。
    """
    session_id: str
    language: Optional[str] = None
    committed_text: str = ""
    last_active_at: float = field(default_factory=time.time)
    chunk_count: int = 0

    def append_chunk(
        self, pcm_chunk: bytes, transcribe_fn: TranscribeFn,
    ) -> Dict:
        """跑 whisper 在单 chunk 上,新增文本追加到 committed_text,实时返回。"""
        self.chunk_count += 1
        self.last_active_at = time.time()

        wav_bytes = make_wav_bytes(pcm_chunk)
        new_text, diag = transcribe_fn(wav_bytes)
        new_text = (new_text or "").strip()

        # 拼接策略:中文之间不加空格,跟英文/数字之间塞个空格防粘连。
        # 简单启发:committed_text 末尾是 ASCII 字母数字 + new_text 头也是 → 加空格。
        if new_text:
            if self.committed_text:
                last_ch = self.committed_text[-1]
                first_ch = new_text[0]
                if _is_word_char(last_ch) and _is_word_char(first_ch):
                    self.committed_text += " " + new_text
                else:
                    self.committed_text += new_text
            else:
                self.committed_text = new_text

        return {
            # 兼容字段:本次增量就是本 chunk 的全部文本(可能为空,whisper 返静音/marker)
            "stable_increment": new_text,
            # 实时模式无 volatile 概念 — 不用二次校验,出什么就是什么
            "volatile_suffix": "",
            # hypothesis 字段保留语义:本 chunk 的 whisper 原始输出
            "hypothesis": new_text,
            "committed_text": self.committed_text,
            # 实时模式不会"撤销"已 commit 的内容 — 每个 chunk 一次性出字
            "retroactive_change": False,
            "is_full": False,
            "chunk_count": self.chunk_count,
            # 单 chunk 音频长度,前端展示用
            "audio_seconds": len(pcm_chunk) / PCM_BYTES_PER_SECOND,
            "diagnostics": diag,
        }


def _is_word_char(ch: str) -> bool:
    """ASCII 字母数字判断 — 中文 / 全角标点 / emoji 等返 False(它们之间不需要空格)。"""
    return bool(ch) and (ch.isascii() and (ch.isalnum() or ch in "_-"))


class AsrSessionRegistry:
    """进程内 session registry,thread-safe,自动 evict 闲置 session。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, AsrSession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str, language: Optional[str] = None) -> AsrSession:
        with self._lock:
            self._evict_idle()
            sess = self._sessions.get(session_id)
            if sess is None:
                sess = AsrSession(session_id=session_id, language=language)
                self._sessions[session_id] = sess
            return sess

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def _evict_idle(self) -> None:
        now = time.time()
        stale = [
            sid for sid, s in self._sessions.items()
            if now - s.last_active_at > SESSION_TTL_SEC
        ]
        for sid in stale:
            self._sessions.pop(sid, None)

    def size(self) -> int:
        with self._lock:
            return len(self._sessions)


_REGISTRY: Optional[AsrSessionRegistry] = None


def get_registry() -> AsrSessionRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = AsrSessionRegistry()
    return _REGISTRY
