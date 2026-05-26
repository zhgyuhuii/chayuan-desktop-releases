"""AudioPipeline.transcribe 的 sidecar 路径 + fallback 测试 (Plan 3C)。"""
from __future__ import annotations

from unittest import mock

import pytest


def _make_audio_bytes() -> bytes:
    """随便一段 16kHz mono PCM-like bytes(只是占位,不真解码)。"""
    return b"\x00" * 1024


def test_transcribe_via_sidecar_success(tmp_path, monkeypatch):
    """sidecar ready → POST /inference → 200 text → 返 text。"""
    from chayuan.server.modality.audio import AudioPipeline
    from chayuan.server.model_registry import local_runtime as lr

    # mock registry.get('asr') 返 ready manager
    fake_mgr = mock.MagicMock()
    fake_mgr.status = lr.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62585", pid=99)

    fake_registry = mock.MagicMock()
    fake_registry.get = mock.MagicMock(return_value=fake_mgr)
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime_registry.get_registry",
        lambda: fake_registry,
    )

    # mock httpx.Client
    fake_resp = mock.MagicMock()
    fake_resp.json.return_value = {"text": "今天天气真好"}
    fake_resp.raise_for_status = mock.MagicMock()

    fake_client_instance = mock.MagicMock()
    fake_client_instance.__enter__ = mock.MagicMock(return_value=fake_client_instance)
    fake_client_instance.__exit__ = mock.MagicMock(return_value=None)
    fake_client_instance.post = mock.MagicMock(return_value=fake_resp)

    monkeypatch.setattr("httpx.Client", mock.MagicMock(return_value=fake_client_instance))

    pipe = AudioPipeline()
    result = pipe.transcribe(_make_audio_bytes(), language="zh")
    assert result == "今天天气真好"


def test_transcribe_sidecar_not_ready_falls_back_to_python(tmp_path, monkeypatch):
    """sidecar state=failed + start 也失败 → fallback Python(全 mock 掉返空)。"""
    from chayuan.server.modality.audio import AudioPipeline
    from chayuan.server.model_registry import local_runtime as lr

    fake_mgr = mock.MagicMock()
    fake_mgr.status = lr.RuntimeStatus(state="failed", last_error="binary 缺失")

    # start 也失败: AsyncMock 返回 failed status
    async def fake_start():
        return lr.RuntimeStatus(state="failed", last_error="binary 缺失")

    fake_mgr.start = mock.AsyncMock(side_effect=fake_start)

    fake_registry = mock.MagicMock()
    fake_registry.get = mock.MagicMock(return_value=fake_mgr)
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime_registry.get_registry",
        lambda: fake_registry,
    )

    # mock faster_whisper / whisper 不可用
    import sys
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setitem(sys.modules, "whisper", None)
    # OpenAI client 也抛
    monkeypatch.setattr(
        "chayuan.server.utils.get_OpenAI",
        mock.MagicMock(side_effect=RuntimeError("no openai")),
    )

    pipe = AudioPipeline()
    result = pipe.transcribe(_make_audio_bytes())
    # 全失败时 transcribe 返空字符串
    assert result == ""


def test_transcribe_sidecar_5xx_falls_back(tmp_path, monkeypatch):
    """sidecar HTTP 500 → raise_for_status 抛 → fallback Python(也 mock 掉返空)。"""
    from chayuan.server.modality.audio import AudioPipeline
    from chayuan.server.model_registry import local_runtime as lr
    import httpx

    fake_mgr = mock.MagicMock()
    fake_mgr.status = lr.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62585")

    fake_registry = mock.MagicMock()
    fake_registry.get = mock.MagicMock(return_value=fake_mgr)
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime_registry.get_registry",
        lambda: fake_registry,
    )

    fake_resp = mock.MagicMock()
    fake_resp.raise_for_status = mock.MagicMock(
        side_effect=httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=mock.MagicMock(),
            response=mock.MagicMock(status_code=500),
        )
    )

    fake_client_instance = mock.MagicMock()
    fake_client_instance.__enter__ = mock.MagicMock(return_value=fake_client_instance)
    fake_client_instance.__exit__ = mock.MagicMock(return_value=None)
    fake_client_instance.post = mock.MagicMock(return_value=fake_resp)
    monkeypatch.setattr("httpx.Client", mock.MagicMock(return_value=fake_client_instance))

    # fallback Python 也不可用 → 全空
    import sys
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    monkeypatch.setitem(sys.modules, "whisper", None)
    monkeypatch.setattr(
        "chayuan.server.utils.get_OpenAI",
        mock.MagicMock(side_effect=RuntimeError("no openai")),
    )

    pipe = AudioPipeline()
    result = pipe.transcribe(_make_audio_bytes())
    assert result == ""


def test_transcribe_sidecar_language_passed(tmp_path, monkeypatch):
    """sidecar 走通时 language 字段透传到 multipart data。"""
    from chayuan.server.modality.audio import AudioPipeline
    from chayuan.server.model_registry import local_runtime as lr

    fake_mgr = mock.MagicMock()
    fake_mgr.status = lr.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62585")

    fake_registry = mock.MagicMock()
    fake_registry.get = mock.MagicMock(return_value=fake_mgr)
    monkeypatch.setattr(
        "chayuan.server.model_registry.local_runtime_registry.get_registry",
        lambda: fake_registry,
    )

    captured: dict = {}

    fake_resp = mock.MagicMock()
    fake_resp.json.return_value = {"text": "hi"}
    fake_resp.raise_for_status = mock.MagicMock()

    def fake_post(url, *, data=None, files=None, **kwargs):
        captured["data"] = data
        captured["url"] = url
        return fake_resp

    fake_client_instance = mock.MagicMock()
    fake_client_instance.__enter__ = mock.MagicMock(return_value=fake_client_instance)
    fake_client_instance.__exit__ = mock.MagicMock(return_value=None)
    fake_client_instance.post = mock.MagicMock(side_effect=fake_post)
    monkeypatch.setattr("httpx.Client", mock.MagicMock(return_value=fake_client_instance))

    pipe = AudioPipeline()
    pipe.transcribe(_make_audio_bytes(), language="ja")
    assert captured["data"] == {"language": "ja"}
    assert captured["url"].endswith("/inference")
