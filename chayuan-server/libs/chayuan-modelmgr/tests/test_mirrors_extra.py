"""mirrors.py 扩展能力测试: set_mirror / speedtest / modelscope_repo_id。"""
from __future__ import annotations

import pytest

from chayuan_modelmgr.mirrors import (
    MIRRORS,
    SpeedtestResult,
    modelscope_repo_id,
    resolve_mirror,
    set_mirror,
    speedtest_mirrors,
)


# ---------- set_mirror runtime override ----------

def test_set_mirror_runtime_override_takes_priority(monkeypatch):
    # 即使 env 指向 huggingface, set_mirror 后也走指定的
    monkeypatch.setenv("CHAYUAN_MIRROR", "huggingface")
    try:
        set_mirror("modelscope")
        m = resolve_mirror()
        assert m.name == "modelscope"
    finally:
        set_mirror(None)
        # env 还在,清掉
        monkeypatch.delenv("CHAYUAN_MIRROR", raising=False)


def test_set_mirror_clear_returns_to_env(monkeypatch):
    monkeypatch.setenv("CHAYUAN_MIRROR", "huggingface")
    set_mirror("modelscope")
    set_mirror(None)
    m = resolve_mirror()
    # 清空后 fallback 到 env
    assert m.name == "huggingface"


def test_set_mirror_custom_url():
    set_mirror("https://my-mirror.example.com")
    try:
        m = resolve_mirror()
        assert m.endpoint == "https://my-mirror.example.com"
        assert m.kind == "hf"
    finally:
        set_mirror(None)


def test_set_mirror_custom_url_with_modelscope_kind():
    set_mirror("https://internal.modelscope-relay.example.com")
    try:
        m = resolve_mirror()
        assert m.kind == "modelscope"  # URL 含 modelscope 关键字
    finally:
        set_mirror(None)


# ---------- modelscope_repo_id ----------

@pytest.mark.parametrize("hf_id,expected", [
    ("Qwen/Qwen2.5-7B-Instruct", "qwen/Qwen2.5-7B-Instruct"),
    ("BAAI/bge-large-zh-v1.5", "baai/bge-large-zh-v1.5"),
    ("LLM-Research/Llama-3.2-3B-Instruct", "LLM-Research/Llama-3.2-3B-Instruct"),  # 在 OVERRIDES 里
    ("ZhipuAI/GLM-4-9B", "ZhipuAI/GLM-4-9B"),  # OVERRIDES
    ("simple-name", "simple-name"),
    ("MiniMax-AI/MiniMax-Text-01", "MiniMax-AI/MiniMax-Text-01"),
])
def test_modelscope_repo_id_conversion(hf_id: str, expected: str) -> None:
    assert modelscope_repo_id(hf_id) == expected


# ---------- speedtest ----------

def test_speedtest_returns_a_result_per_known_mirror():
    # 实际网络;能跑就跑,不能就 skip
    try:
        results = speedtest_mirrors(timeout=2.0)
    except Exception:  # noqa: BLE001
        pytest.skip("network unavailable")
    assert len(results) == len(MIRRORS)
    assert all(isinstance(r, SpeedtestResult) for r in results)


def test_speedtest_with_injected_candidates():
    """传入子集候选,只测这些。"""
    try:
        results = speedtest_mirrors(timeout=2.0, candidates=["hf-mirror"])
    except Exception:  # noqa: BLE001
        pytest.skip("network unavailable")
    assert len(results) == 1
    assert results[0].name == "hf-mirror"


def test_speedtest_unknown_candidate_silently_skipped():
    try:
        results = speedtest_mirrors(timeout=1.0, candidates=["doesnt-exist", "hf-mirror"])
    except Exception:  # noqa: BLE001
        pytest.skip("network unavailable")
    assert len(results) == 1
    assert results[0].name == "hf-mirror"
