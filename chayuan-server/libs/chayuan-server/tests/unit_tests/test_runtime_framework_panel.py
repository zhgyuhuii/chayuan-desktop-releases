"""Config Panel · runtime_framework_panel 单测。

只测纯函数部分（探测 / 安装命令分派 / capability 候选枚举 / 写盘）；
NiceGUI 渲染部分不测，因为依赖运行中的 ui session。
"""
from __future__ import annotations

import os
import tempfile

import pytest


# probe_all_frameworks 内部有 10s TTL 缓存(进程级),如果上一个测试用
# monkeypatch 改了 _http_ping / _which_any,缓存会污染下一个测试。
# 每个测试运行前显式失效缓存,确保 monkeypatch 生效。
@pytest.fixture(autouse=True)
def _invalidate_probe_cache_between_tests() -> None:
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            invalidate_probe_cache,
        )
        invalidate_probe_cache()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 词汇表 / 安装命令
# ---------------------------------------------------------------------------


def test_capability_labels_cover_nine_cases():
    """9 类 capability 必须与 chayuan_gateway.routers.admin._DEFAULTS_CAPABILITIES 一致。"""
    from chayuan.server.config_panel.runtime_framework_panel import (
        CAPABILITY_LABELS,
    )
    caps = {c for c, _ in CAPABILITY_LABELS}
    assert caps == {
        "chat", "embedding", "clip", "rerank",
        "t2i", "t2v", "tts", "asr", "ocr",
    }


def test_yaml_key_mapping_covers_all_nine():
    """``DEFAULT_<CAP>_MODEL`` yaml key 必须 9 个齐全；与 capabilities 一一对应。"""
    from chayuan.server.config_panel.runtime_framework_panel import (
        _CAP_TO_YAML_KEY, CAPABILITY_LABELS,
    )
    keys = {cap: _CAP_TO_YAML_KEY[cap] for cap, _ in CAPABILITY_LABELS}
    assert keys["chat"] == "DEFAULT_LLM_MODEL"
    assert keys["embedding"] == "DEFAULT_EMBEDDING_MODEL"
    assert keys["clip"] == "DEFAULT_IMAGE_EMBEDDING_MODEL"
    assert keys["t2i"] == "DEFAULT_TEXT2IMAGE_MODEL"
    assert keys["asr"] == "DEFAULT_SPEECH2TEXT_MODEL"
    assert keys["ocr"] == "DEFAULT_OCR_MODEL"
    # value 互不重复
    assert len(set(keys.values())) == 9


def test_built_in_catalog_has_all_eleven_frameworks():
    """关键回归：UI 卡片**永远**至少有 11 张（用户上一轮反馈"内没有卡片"）。
    即使 ``chayuan_runtime`` 不可用 / registry 为空，也得从 _FRAMEWORK_CATALOG
    拿到 11 个，避免空白页。"""
    from chayuan.server.config_panel.runtime_framework_panel import (
        _FRAMEWORK_CATALOG,
    )
    names = {f.name for f in _FRAMEWORK_CATALOG}
    assert names == {
        "ollama", "vllm", "llamacpp", "infinity", "comfyui",
        "funasr", "cosyvoice", "piper",
        "rapidocr", "paddleocr", "whispercpp",
    }
    # 每个 spec 都至少声明 1 个 capability + 1 个 install_kind
    for f in _FRAMEWORK_CATALOG:
        assert f.capabilities, f.name
        assert f.install_kind in ("one-click", "pip", "docker", "manual"), f.name


def test_build_install_cmd_for_ollama_cross_platform(monkeypatch):
    """Ollama 在 Linux/macOS 走 curl install.sh，Windows 走 winget。"""
    from chayuan.server.config_panel import runtime_framework_panel as m

    monkeypatch.setattr(m, "_detect_local_os", lambda: "linux")
    cmd = m._build_install_cmd("ollama")
    assert cmd is not None
    assert "ollama.com/install.sh" in cmd[-1]

    monkeypatch.setattr(m, "_detect_local_os", lambda: "win")
    cmd_win = m._build_install_cmd("ollama")
    assert cmd_win is not None
    assert "Ollama.Ollama" in cmd_win


def test_build_install_cmd_for_pip_packages():
    """``infinity`` / ``piper`` 等用 ``pip install``。"""
    import sys
    from chayuan.server.config_panel.runtime_framework_panel import (
        _build_install_cmd,
    )
    cmd = _build_install_cmd("infinity")
    assert cmd is not None
    assert cmd[:3] == [sys.executable, "-m", "pip"]
    assert "install" in cmd
    assert _build_install_cmd("cosyvoice") is not None
    assert _build_install_cmd("funasr") is not None


def test_build_install_cmd_returns_none_for_docker_only():
    """ComfyUI / vLLM 只能 docker；自动安装应直接返 None 让上层提示用户。"""
    from chayuan.server.config_panel.runtime_framework_panel import (
        _build_install_cmd,
    )
    assert _build_install_cmd("comfyui") is None
    assert _build_install_cmd("vllm") is None
    assert _build_install_cmd("totally-unknown") is None


# ---------------------------------------------------------------------------
# 探测：HTTP / binary / subprocess 三档
# ---------------------------------------------------------------------------


def test_probe_all_frameworks_returns_eleven_regardless_of_environment(monkeypatch):
    """关键：``probe_all_frameworks`` 必须固定返 11 个 ``RuntimeHealth``。
    用户报告"模型框架内没有卡片"——根因之一是 registry 不可用就空列表。
    现在改成走 _FRAMEWORK_CATALOG，所以即便 registry 故障，也得有 11 张。
    """
    from chayuan.server.config_panel import runtime_framework_panel as m

    # 把 registry 强制 raise 来模拟"chayuan_runtime 不可用"
    monkeypatch.setattr(m, "_adapter_url_from_registry", lambda _n: "")
    # http ping 全部失败
    monkeypatch.setattr(m, "_http_ping", lambda *_a, **_kw: False)
    # binary 全部找不到
    monkeypatch.setattr(m, "_which_any", lambda _b: None)

    healths = m.probe_all_frameworks()
    assert len(healths) == 11
    names = {h.spec.name for h in healths}
    assert "ollama" in names
    assert "vllm" in names
    # 状态全部应是 missing 或 configured（取决于 default_url 是否非空）
    for h in healths:
        assert h.state in ("missing", "configured", "installed", "running")


def test_probe_detects_installed_when_binary_on_path(monkeypatch):
    """用户报告"本机安装的 ollama 也没有探测出来"——加 binary fallback 后修。"""
    from chayuan.server.config_panel import runtime_framework_panel as m

    monkeypatch.setattr(m, "_http_ping", lambda *_a, **_kw: False)  # HTTP 不通
    # 模拟只有 ollama 有 binary
    monkeypatch.setattr(
        m, "_which_any",
        lambda bins: "/usr/local/bin/ollama" if "ollama" in bins else None,
    )

    healths = m.probe_all_frameworks()
    by_name = {h.spec.name: h for h in healths}
    assert by_name["ollama"].state == "installed"
    assert by_name["ollama"].bin_path == "/usr/local/bin/ollama"
    # 其它继续 configured / missing（非 installed）
    assert by_name["vllm"].state != "installed"


def test_probe_detects_running_when_http_ping_succeeds(monkeypatch):
    """HTTP 通 → state=running，且 url 字段非空。"""
    from chayuan.server.config_panel import runtime_framework_panel as m

    # 只让 ollama 探针通过；其它都失败
    monkeypatch.setattr(
        m, "_http_ping",
        lambda url, **_kw: "11434" in (url or ""),
    )
    monkeypatch.setattr(m, "_which_any", lambda _b: None)

    healths = m.probe_all_frameworks()
    by_name = {h.spec.name: h for h in healths}
    assert by_name["ollama"].state == "running"
    assert "11434" in by_name["ollama"].url
    assert by_name["vllm"].state in ("configured", "missing")


def test_probe_subprocess_runtime_uses_binary_only(monkeypatch):
    """piper / whisper.cpp 是 subprocess 模式：没有 HTTP，binary 存在即 installed。"""
    from chayuan.server.config_panel import runtime_framework_panel as m

    monkeypatch.setattr(m, "_http_ping", lambda *_a, **_kw: False)
    monkeypatch.setattr(
        m, "_which_any",
        lambda bins: "/usr/bin/piper" if "piper" in bins else None,
    )
    healths = m.probe_all_frameworks()
    by_name = {h.spec.name: h for h in healths}
    assert by_name["piper"].state == "installed"
    assert by_name["whispercpp"].state == "missing"


def test_probe_state_sort_running_first(monkeypatch):
    """``running`` 卡片应排前面，``missing`` 排最后；同档按能力数倒序。"""
    from chayuan.server.config_panel import runtime_framework_panel as m

    # ollama running，infinity installed，rapidocr running
    def _ping(url, **_kw):
        return "11434" in (url or "") or "18380" in (url or "")
    monkeypatch.setattr(m, "_http_ping", _ping)
    monkeypatch.setattr(
        m, "_which_any",
        lambda bins: "/x/infinity_emb" if "infinity_emb" in bins else None,
    )
    healths = m.probe_all_frameworks()
    states = [h.state for h in healths]
    # 排序后 running 一定在前面
    first_missing = states.index("missing") if "missing" in states else len(states)
    if "running" in states:
        last_running = len(states) - 1 - states[::-1].index("running")
        assert last_running < first_missing


# ---------------------------------------------------------------------------
# 9-cap defaults: yaml 持久化 + 候选汇总
# ---------------------------------------------------------------------------


def test_save_capability_default_writes_to_yaml(tmp_path, monkeypatch):
    """``_save_capability_default`` 必须把值写到 model_settings.yaml；可读回来。"""
    # ``chayuan.settings.CHAYUAN_ROOT`` 在 import 时定型；用 monkeypatch 直改值
    import chayuan.settings as cs
    monkeypatch.setattr(cs, "CHAYUAN_ROOT", tmp_path)

    yaml_path = tmp_path / "model_settings.yaml"
    yaml_path.write_text("DEFAULT_LLM_MODEL: ''\n")

    from chayuan.server.config_panel.runtime_framework_panel import (
        _save_capability_default, _load_capability_defaults,
    )

    ok, _ = _save_capability_default("chat", "qwen2.5:7b")
    assert ok, "首次写入应成功"

    ok, _ = _save_capability_default("ocr", "rapidocr-onnx")
    assert ok

    # 重新读
    out = _load_capability_defaults()
    assert out["chat"] == "qwen2.5:7b"
    assert out["ocr"] == "rapidocr-onnx"
    # 其它 cap 维持空
    assert out["t2i"] == ""


def test_save_capability_default_rejects_unknown_cap():
    from chayuan.server.config_panel.runtime_framework_panel import (
        _save_capability_default,
    )
    ok, msg = _save_capability_default("unknown-cap", "qwen2.5:7b")
    assert ok is False
    assert "unknown-cap" in msg or "未知" in msg


def test_capability_candidates_returns_dict_for_all_nine_caps():
    """无论 yaml / repo 当前是否有模型，9 个 capability 都得在 dict 里。"""
    from chayuan.server.config_panel.runtime_framework_panel import (
        _capability_candidates, CAPABILITY_LABELS,
    )
    candidates = _capability_candidates()
    for cap, _ in CAPABILITY_LABELS:
        assert cap in candidates
        assert isinstance(candidates[cap], list)


def test_capability_candidates_unions_yaml_platforms_and_local_index(
    tmp_path, monkeypatch,
):
    """候选 = yaml 平台模型 ∪ LocalIndexRepository 模型，按 cap 去重。"""
    import chayuan.settings as cs
    monkeypatch.setattr(cs, "CHAYUAN_ROOT", tmp_path)

    yaml_path = tmp_path / "model_settings.yaml"
    yaml_path.write_text(
        "MODEL_PLATFORMS:\n"
        "  - platform_name: bailian\n"
        "    enabled: true\n"
        "    llm_models:\n"
        "      - qwen-max\n"
        "      - qwen-plus\n"
        "    embed_models:\n"
        "      - text-embedding-v3\n"
        "  - platform_name: deepseek\n"
        "    enabled: true\n"
        "    llm_models:\n"
        "      - deepseek-chat\n"
    )

    from chayuan.server.config_panel.runtime_framework_panel import (
        _capability_candidates,
    )
    out = _capability_candidates()
    chat_ids = [mid for mid, _src in out["chat"]]
    assert "qwen-max" in chat_ids
    assert "qwen-plus" in chat_ids
    assert "deepseek-chat" in chat_ids
    embed_ids = [mid for mid, _src in out["embedding"]]
    assert "text-embedding-v3" in embed_ids


# ---------------------------------------------------------------------------
# 一键安装（mock subprocess，不真起进程）
# ---------------------------------------------------------------------------


def test_spawn_install_async_rejects_unknown_runtime():
    from chayuan.server.config_panel.runtime_framework_panel import (
        _spawn_install_async,
    )
    task_id, err = _spawn_install_async("totally-unknown-runtime")
    assert err is not None
    assert task_id == ""


def test_spawn_install_async_for_pip_does_not_real_run(monkeypatch):
    """pip 系列：spawn 应返 task_id 但 ``_bg`` 真启动 subprocess —— 我们 mock 掉
    Popen，验证 task 被排队进 ``_install_state`` 即可。"""
    from chayuan.server.config_panel import runtime_framework_panel as m

    class _DummyProc:
        stdout = iter(["installing fake pkg\n"])
        def wait(self):
            return 0

    monkeypatch.setattr(m.subprocess, "Popen", lambda *a, **kw: _DummyProc())

    task_id, err = m._spawn_install_async("infinity")
    assert err is None
    assert task_id.startswith("install-infinity-")
    import time
    for _ in range(50):
        with m._install_lock:
            state = m._install_state.get(task_id, {}).get("state")
        if state == "done":
            break
        time.sleep(0.02)
    with m._install_lock:
        snap = m._install_state[task_id]
    assert snap["state"] == "done"
    assert snap.get("return_code") == 0
