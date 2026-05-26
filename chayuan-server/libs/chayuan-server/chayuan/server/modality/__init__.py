"""ModalityService（T10）：视觉 / 音频 / 视频 / PDF 图像 统一出入口。

分层：
- ``service.ModalityService``：顶层门面，dispatcher 只需调它
- ``audio.AudioPipeline``    ：ASR（Whisper）+ TTS（Edge/OpenAI）
- ``video.VideoPipeline``    ：抽帧 + Vision + ASR 组合理解
- 视觉（image）已由 ``api_server.openai_routes`` 原生 OpenAI content-list 透传，
  ModalityService 只负责 **检测 + 路由**，不再重复实现

任何耗时重加载（whisper/transformers）都懒加载 + fail-soft；Ollama / OpenAI 云端
路径可通过 ``basic_settings.MODALITY_*`` 切换。
"""
from chayuan.server.modality.service import ModalityService, get_modality_service

__all__ = ["ModalityService", "get_modality_service"]
