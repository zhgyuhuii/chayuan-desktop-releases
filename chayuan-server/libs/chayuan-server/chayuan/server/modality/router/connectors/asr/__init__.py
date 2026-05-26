"""ASR(Automatic Speech Recognition)Connector 集合。

PR-8:
  - OpenAI 兼容 /v1/audio/transcriptions(multipart 上传,whisper-1 / gpt-4o-transcribe)
  - DashScope native qwen3-asr-flash / paraformer(multimodal-generation 端点,JSON + 音频 data URL)
"""

from chayuan.server.modality.router.connectors.asr import openai_compat  # noqa: F401
from chayuan.server.modality.router.connectors.asr import dashscope      # noqa: F401

__all__: list[str] = []
