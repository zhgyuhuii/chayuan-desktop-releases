"""顶层 ModalityService（T10）。

对外三个方法：``asr`` / ``tts`` / ``understand_video``；路由 / dispatcher 只调这一个。

检测：``detect(raw_messages, extras) -> "text" | "vision" | "audio" | "video"``，
让 dispatcher 做模态路由，不再散落 ad-hoc 判断。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.modality.service")


class ModalityService:
    """进程单例；音频 / 视频 pipeline 懒加载。"""

    def __init__(self):
        self._audio = None
        self._video = None

    @property
    def audio(self):
        if self._audio is None:
            from chayuan.server.modality.audio import AudioPipeline
            self._audio = AudioPipeline()
        return self._audio

    @property
    def video(self):
        if self._video is None:
            from chayuan.server.modality.video import VideoPipeline
            self._video = VideoPipeline(audio=self.audio)
        return self._video

    # ------------------------------------------------------------------
    # 检测：供 dispatcher 做模态路由
    # ------------------------------------------------------------------

    @staticmethod
    def detect(
        raw_messages: Optional[List[Dict[str, Any]]] = None,
        extras: Optional[Dict[str, Any]] = None,
    ) -> str:
        """根据 OpenAI-style raw_messages + extras 判定模态。

        - vision：最后一条消息 content 是 list，且含 image_url / input_image
        - audio ：extras 含 ``audio_url``；或最后消息 content list 含 input_audio
        - video ：extras 含 ``video_url``；或最后消息 content list 含 video_url
        - 其它   → text
        """
        extras = extras or {}
        if extras.get("video_url"):
            return "video"
        if extras.get("audio_url"):
            return "audio"

        if raw_messages:
            last = raw_messages[-1] if isinstance(raw_messages, list) else None
            if isinstance(last, dict):
                content = last.get("content")
                if isinstance(content, list):
                    kinds = {p.get("type") for p in content if isinstance(p, dict)}
                    if {"video_url"} & kinds:
                        return "video"
                    if {"input_audio", "audio_url"} & kinds:
                        return "audio"
                    if {"image_url", "input_image", "image"} & kinds:
                        return "vision"
        return "text"

    # ------------------------------------------------------------------
    # 业务入口
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 埋点工具（P2-10）：成功 / 失败走同一条路径写 Prometheus
    # ------------------------------------------------------------------

    @staticmethod
    def _observe(op: str, status: str, started: float) -> None:
        try:
            import time as _t
            from chayuan.server.observability.metrics import record_modality_op
            record_modality_op(op, status, max(0.0, _t.perf_counter() - started))
        except Exception:  # noqa: BLE001
            pass

    def asr(self, audio: Any, *, language: Optional[str] = None,
             model: str = "base") -> str:
        import time as _t
        t0 = _t.perf_counter()
        try:
            out = self.audio.transcribe(audio, language=language, model=model)
            self._observe("asr", "success", t0)
            return out
        except Exception:
            self._observe("asr", "error", t0)
            raise

    def tts(self, text: str, *, voice: str = "zh-CN-XiaoxiaoNeural",
             fmt: str = "mp3") -> bytes:
        import time as _t
        t0 = _t.perf_counter()
        try:
            out = self.audio.synthesize(text, voice=voice, fmt=fmt)
            self._observe("tts", "success", t0)
            return out
        except Exception:
            self._observe("tts", "error", t0)
            raise

    def understand_video(
        self, video: Any, *, query: str = "",
        every_sec: int = 8, max_frames: int = 16,
        vision_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        import time as _t
        t0 = _t.perf_counter()
        try:
            out = self.video.understand(
                video, query=query, every_sec=every_sec,
                max_frames=max_frames, vision_model=vision_model,
            )
            self._observe("video_understand", "success", t0)
            return out
        except Exception:
            self._observe("video_understand", "error", t0)
            raise


# ---------------------------------------------------------------------------
# 进程单例
# ---------------------------------------------------------------------------

_SINGLETON: Optional[ModalityService] = None
_LOCK = threading.Lock()


def get_modality_service() -> ModalityService:
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    with _LOCK:
        if _SINGLETON is None:
            _SINGLETON = ModalityService()
    return _SINGLETON
