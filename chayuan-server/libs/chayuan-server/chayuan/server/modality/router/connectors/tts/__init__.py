"""TTS Connector 集合。

PR-6:
  - OpenAI 兼容 /v1/audio/speech(覆盖 tts-1 / tts-1-hd / gpt-4o-mini-tts /
    OpenRouter 代理的 TTS、字节豆包 Ark 兼容 TTS)
PR-7+:DashScope native(cosyvoice / qwen-tts / sambert,有自己的协议)
"""

from chayuan.server.modality.router.connectors.tts import openai_compat  # noqa: F401
from chayuan.server.modality.router.connectors.tts import dashscope     # noqa: F401

__all__: list[str] = []
