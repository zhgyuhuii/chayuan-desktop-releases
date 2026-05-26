"""LocalRuntimeRegistry 单测。"""
from __future__ import annotations

import pytest


def test_registry_constructs_five_managers(tmp_path):
    """Plan 3D: registry 含 chat/embedding/rerank/asr/image-embedding 5 个 manager。"""
    from chayuan.server.model_registry.local_runtime_registry import LocalRuntimeRegistry
    reg = LocalRuntimeRegistry(chayuan_root=tmp_path)
    assert set(reg._managers.keys()) == {"chat", "embedding", "rerank", "asr", "image-embedding"}
    assert reg.get("chat").engine == "llama"
    assert reg.get("chat").port_offset == 0
    assert reg.get("embedding").engine == "llama"
    assert reg.get("embedding").port_offset == 1
    assert reg.get("rerank").engine == "llama"
    assert reg.get("rerank").port_offset == 2
    assert reg.get("asr").engine == "whisper"
    assert reg.get("asr").port_offset == 3
    assert reg.get("image-embedding").engine == "infinity"
    assert reg.get("image-embedding").port_offset == 4


def test_registry_get_unknown_raises(tmp_path):
    from chayuan.server.model_registry.local_runtime_registry import LocalRuntimeRegistry
    reg = LocalRuntimeRegistry(chayuan_root=tmp_path)
    with pytest.raises(ValueError, match="unknown capability"):
        reg.get("tts")


def test_registry_all_statuses_five_caps(tmp_path):
    """all_statuses() 返 5 项,image-embedding 初始 stopped。"""
    from chayuan.server.model_registry.local_runtime_registry import LocalRuntimeRegistry
    reg = LocalRuntimeRegistry(chayuan_root=tmp_path)
    sts = reg.all_statuses()
    assert set(sts.keys()) == {"chat", "embedding", "rerank", "asr", "image-embedding"}
    for cap, st in sts.items():
        assert st.state == "stopped"


@pytest.mark.asyncio
async def test_registry_stop_all_calls_each_stop(tmp_path, monkeypatch):
    from unittest import mock
    from chayuan.server.model_registry.local_runtime_registry import LocalRuntimeRegistry
    reg = LocalRuntimeRegistry(chayuan_root=tmp_path)
    for cap in ("chat", "embedding", "rerank"):
        reg._managers[cap].stop = mock.AsyncMock()
    await reg.stop_all()
    for cap in ("chat", "embedding", "rerank"):
        reg._managers[cap].stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_registry_stop_all_continues_when_one_raises(tmp_path):
    from unittest import mock
    from chayuan.server.model_registry.local_runtime_registry import LocalRuntimeRegistry
    reg = LocalRuntimeRegistry(chayuan_root=tmp_path)
    reg._managers["chat"].stop = mock.AsyncMock(side_effect=RuntimeError("boom"))
    reg._managers["embedding"].stop = mock.AsyncMock()
    reg._managers["rerank"].stop = mock.AsyncMock()
    # 不应该抛
    await reg.stop_all()
    reg._managers["embedding"].stop.assert_awaited_once()
    reg._managers["rerank"].stop.assert_awaited_once()


def test_get_registry_singleton(tmp_path, monkeypatch):
    from chayuan.server.model_registry import local_runtime_registry as lrr
    monkeypatch.setattr(lrr, "_registry_singleton", None)
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", str(tmp_path))
    r1 = lrr.get_registry()
    r2 = lrr.get_registry()
    assert r1 is r2
    assert isinstance(r1, lrr.LocalRuntimeRegistry)
