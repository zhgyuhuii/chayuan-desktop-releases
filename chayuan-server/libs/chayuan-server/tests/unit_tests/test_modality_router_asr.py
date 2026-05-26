"""PR-8 ASR connector 注册 / 路由回归测试。

只验证 *路由层*:
  - capability 推断:whisper / gpt-4o-transcribe / qwen3-asr / paraformer → asr
  - vendor 推断 + pick_connector:在用户 platform_type=openai(bailian 配 compat-mode)
    场景下,qwen3-asr / paraformer 仍要走 DashScopeASRConnector
  - registry 落表:(ASR, openai) / (ASR, openrouter) / (ASR, dashscope) 都在表里

不打真实 ASR 接口 — 真实音频转写跑在 e2e / 手测里。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="module")
def _import_connectors():
    # 触发 @register 落表
    from chayuan.server.modality.router import connectors  # noqa: F401


def test_asr_capability_classify():
    from chayuan.server.modality.router.protocol import Capability, classify_capability

    for m in (
        "whisper-1",
        "whisper-large-v3",
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
        "qwen3-asr-flash",
        "qwen-asr-v1",
        "paraformer-v2",
        "paraformer-realtime-v2",
        "funasr-vad",
    ):
        assert classify_capability(m) == Capability.ASR, m


def test_asr_vendor_classify():
    from chayuan.server.modality.router.protocol import classify_vendor

    assert classify_vendor("whisper-1") == "openai"
    assert classify_vendor("whisper-large-v3") == "openai"
    assert classify_vendor("gpt-4o-transcribe") == "openai"
    assert classify_vendor("gpt-4o-mini-transcribe") == "openai"
    assert classify_vendor("qwen3-asr-flash") == "dashscope"
    assert classify_vendor("qwen-asr-v1") == "dashscope"
    assert classify_vendor("paraformer-v2") == "dashscope"
    # 未登记前缀 → None(让 router 看 platform_type)
    assert classify_vendor("some-unknown-asr") is None


def test_asr_pick_connector_dashscope_native_over_openai_compat():
    """bailian 配 platform_type='openai' 跑 chat compat-mode,
    但同平台的 qwen3-asr-flash 必须仍路由到 DashScopeASRConnector
    (上游只在 native ``/api/v1/services/aigc/multimodal-generation`` 发布)。"""
    from chayuan.server.modality.router.connectors.base import pick_connector
    from chayuan.server.modality.router.protocol import Capability

    cls = pick_connector(Capability.ASR, "qwen3-asr-flash", "openai")
    assert cls is not None and cls.__name__ == "DashScopeASRConnector"

    cls = pick_connector(Capability.ASR, "paraformer-v2", "openai")
    assert cls is not None and cls.__name__ == "DashScopeASRConnector"

    cls = pick_connector(Capability.ASR, "whisper-1", "openai")
    assert cls is not None and cls.__name__ == "OpenAIASRConnector"

    cls = pick_connector(Capability.ASR, "gpt-4o-transcribe", "openai")
    assert cls is not None and cls.__name__ == "OpenAIASRConnector"


def test_asr_connectors_registered():
    from chayuan.server.modality.router.connectors.base import list_registered
    from chayuan.server.modality.router.protocol import Capability

    reg = list_registered()
    assert (Capability.ASR, "openai") in reg
    assert (Capability.ASR, "openrouter") in reg
    assert (Capability.ASR, "dashscope") in reg


# ─────────────────────────────────────────────────────────────
# 顺手回归:qwen3-tts-* 必须分到 TTS(不是 CHAT)
# 历史 bug:capability 表只有 "qwen-tts" 前缀,"qwen3-tts-flash".startswith("qwen-tts")
# 是 False → 请求落到老 chat 路径 → DashScope 报 "Field required: input.text"
# ─────────────────────────────────────────────────────────────
def test_qwen3_tts_capability_routes_to_tts():
    from chayuan.server.modality.router.connectors.base import pick_connector
    from chayuan.server.modality.router.protocol import Capability, classify_capability

    for m in ("qwen3-tts-flash", "qwen3-tts-pro", "qwen3-tts-realtime"):
        assert classify_capability(m) == Capability.TTS, m
        cls = pick_connector(Capability.TTS, m, "openai")
        assert cls is not None and cls.__name__ == "DashScopeTTSConnector", m
