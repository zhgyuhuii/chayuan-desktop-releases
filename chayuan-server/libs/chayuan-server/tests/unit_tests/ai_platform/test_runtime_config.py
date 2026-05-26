"""``chayuan.server.ai_platform.runtime_config.apply_runtime_config`` 测试。

被测对象：根据 chayuan-server 的 ``runtime.json`` 重置 chayuan_runtime 11 个
adapter 的 ``base_url`` + ``mock`` 标志。

测试要点：
1. 没有 runtime.json 时 fallback 端口仍生效；
2. 有 runtime.json 时优先用其中的 host:port；
3. ``mock=False`` 时所有 adapter 的 mock 全 False；
4. 调用本函数前后 chayuan_runtime singleton 不应被泄漏到下一个测试（fixture 重置）。
"""
from __future__ import annotations

import pytest

from chayuan.server.ai_platform.runtime_config import apply_runtime_config


@pytest.fixture(autouse=True)
def _reset_runtime_singletons():
    """每个测试前/后重置 chayuan_runtime registry + chayuan-server runtime_info。"""
    import chayuan_runtime.registry as reg_mod
    import chayuan.server.runtime.runtime_info as ri_mod

    reg_mod._REGISTRY = None
    ri_mod._SINGLETON = None
    yield
    reg_mod._REGISTRY = None
    ri_mod._SINGLETON = None


def test_apply_runtime_config_no_runtime_json_uses_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    # 强制 chayuan.settings 重新解析 root
    import chayuan.settings as settings_mod
    monkeypatch.setattr(settings_mod, "CHAYUAN_ROOT", str(tmp_path), raising=False)

    out = apply_runtime_config(mock=False)
    assert "ollama" in out
    # fallback 端口（vendor/README 约定的高位端口）
    assert out["ollama"].endswith(":31434")
    assert out["vllm"].endswith(":38000")
    assert out["comfyui"].endswith(":38188")


def test_apply_runtime_config_uses_runtime_json_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    import chayuan.settings as settings_mod
    monkeypatch.setattr(settings_mod, "CHAYUAN_ROOT", str(tmp_path), raising=False)

    # 写一份带自定义端口的 runtime.json
    from chayuan.server.runtime.runtime_info import get_runtime_info
    ri = get_runtime_info()
    ri.set_endpoint("ollama", host="127.0.0.1", port=42424, scheme="http",
                    url="http://127.0.0.1:42424")
    ri.set_endpoint("comfyui", host="127.0.0.1", port=43000, scheme="http")

    out = apply_runtime_config(mock=False)
    # 优先用 runtime.json 中的 url
    assert out["ollama"] == "http://127.0.0.1:42424"
    # url 字段缺失时拼 host:port
    assert out["comfyui"] == "http://127.0.0.1:43000"


def test_apply_runtime_config_mock_off_propagates(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    import chayuan.settings as settings_mod
    monkeypatch.setattr(settings_mod, "CHAYUAN_ROOT", str(tmp_path), raising=False)

    apply_runtime_config(mock=False)
    from chayuan_runtime.registry import get_registry
    reg = get_registry()
    # 所有 adapter 都被切到非 mock 模式
    assert all(getattr(a, "mock", True) is False for a in reg.all())


def test_apply_runtime_config_returns_summary_dict_for_all_adapters(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    import chayuan.settings as settings_mod
    monkeypatch.setattr(settings_mod, "CHAYUAN_ROOT", str(tmp_path), raising=False)

    out = apply_runtime_config(mock=False)
    expected_subset = {
        "ollama", "vllm", "llamacpp", "infinity", "comfyui",
        "whispercpp", "funasr", "piper", "cosyvoice", "rapidocr", "paddleocr",
    }
    # 不强求一个不少（仓库未来可能改 chayuan_runtime adapter 列表），但当前 11 个全部覆盖
    assert expected_subset.issubset(set(out.keys()))
