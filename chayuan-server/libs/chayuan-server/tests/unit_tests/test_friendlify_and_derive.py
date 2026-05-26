"""新加的 4 项 UX 修复对应单测.

* _friendlify_model_id —— 默认模型下拉不再乱码
* _derive_start_recipe / _derive_stop_recipe —— "暂无启动配方"兜底
* _DAEMON_PROBES —— Docker daemon 状态探针
* hero strip _bucket 排序 —— 优先已配置 + 启用
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# friendlify
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mid,expected_substr", [
    # overrides 命中
    ("rapidocr-onnx-zh", "RapidOCR · 中文 (ONNX)"),
    ("paddleocr-zh-fast", "PaddleOCR · 中文 (快速)"),
    ("BAAI/bge-m3", "BGE-M3"),
    # owner/name 形式 — 自动取 name 段
    ("Qwen/Qwen2.5-7B-Instruct", "Qwen2.5"),
    # hash 后缀 - 去掉
    ("paddleocr-zh-fast-9c2d4f6e", "Paddleocr"),
    ("rapidocr-zh-deadbeef12", "Rapidocr"),
    # 短句保留版本号 (v1.5 / 7B 不破坏)
    ("BAAI/bge-large-zh-v1.5", "BGE-large 中文 v1.5"),
    # 兜底:无 owner、无 hash 也能转
    ("simple-model", "Simple Model"),
])
def test_friendlify_model_id(mid: str, expected_substr: str):
    from chayuan.server.config_panel.runtime_framework_panel import (
        _friendlify_model_id,
    )
    out = _friendlify_model_id(mid)
    assert expected_substr in out, f"{mid} → {out!r} (期望含 {expected_substr!r})"


def test_friendlify_truncates_extreme_length():
    from chayuan.server.config_panel.runtime_framework_panel import (
        _friendlify_model_id,
    )
    long_name = "x-" * 100
    out = _friendlify_model_id(long_name)
    assert len(out) <= 80
    assert out.endswith("...") or out == long_name[:80]


# ---------------------------------------------------------------------------
# _derive_start_recipe / _derive_stop_recipe — 兜底配方
# ---------------------------------------------------------------------------

def test_derive_start_recipe_for_docker_kind():
    """install_kind=docker 的框架应自动派生 docker start <name>。"""
    from chayuan.server.config_panel.install_task_manager import (
        _derive_start_recipe, _START_RECIPES,
    )
    # comfyui 在 _START_RECIPES 已有, 测一个**没有**显式条目的:
    # 找一个 install_kind=docker 但 _START_RECIPES 没记录的
    from chayuan.server.config_panel.runtime_framework_panel import (
        _FRAMEWORKS_BY_NAME,
    )
    candidates = [
        name for name, spec in _FRAMEWORKS_BY_NAME.items()
        if spec.install_kind == "docker" and name not in _START_RECIPES
    ]
    if not candidates:
        pytest.skip("所有 docker 框架都在 _START_RECIPES 里;无法测兜底路径")
    name = candidates[0]
    rec = _derive_start_recipe(name)
    assert rec is not None
    assert rec.cmd[:2] == ["docker", "start"]
    assert name in rec.cmd


def test_derive_start_recipe_for_pip_with_bin():
    """install_kind=pip 且有 bin_names 的框架走 nohup <bin>。"""
    from chayuan.server.config_panel.install_task_manager import (
        _derive_start_recipe, _START_RECIPES,
    )
    from chayuan.server.config_panel.runtime_framework_panel import (
        _FRAMEWORKS_BY_NAME,
    )
    candidates = [
        name for name, spec in _FRAMEWORKS_BY_NAME.items()
        if spec.install_kind == "pip" and spec.bin_names and name not in _START_RECIPES
    ]
    if not candidates:
        pytest.skip("所有 pip 框架都在 _START_RECIPES 里;无法测兜底路径")
    name = candidates[0]
    rec = _derive_start_recipe(name)
    assert rec is not None
    assert "nohup" in rec.cmd[2]


def test_derive_start_recipe_for_unknown_returns_none():
    from chayuan.server.config_panel.install_task_manager import (
        _derive_start_recipe,
    )
    assert _derive_start_recipe("not-a-framework") is None


def test_derive_stop_recipe_mirrors_start():
    from chayuan.server.config_panel.install_task_manager import (
        _derive_stop_recipe,
    )
    from chayuan.server.config_panel.runtime_framework_panel import (
        _FRAMEWORKS_BY_NAME,
    )
    # docker kind → docker stop
    docker_fw = next(
        (name for name, spec in _FRAMEWORKS_BY_NAME.items()
         if spec.install_kind == "docker"),
        None,
    )
    if docker_fw is None:
        pytest.skip("无 docker kind 框架可测")
    rec = _derive_stop_recipe(docker_fw)
    assert rec is not None
    assert rec.cmd[:2] == ["docker", "stop"]


def test_stop_service_via_manager():
    from chayuan.server.config_panel.install_task_manager import InstallTaskManager
    mgr = InstallTaskManager()
    # 任意已知框架 (先有 _STOP_RECIPES) 测试 task 创建
    task = mgr.stop_service(framework="ollama")
    assert task is not None
    # 框架名包装为 stop-ollama 以避免与 install/start task 冲突
    assert task.framework == "stop-ollama"


def test_stop_service_unknown_returns_none():
    from chayuan.server.config_panel.install_task_manager import InstallTaskManager
    mgr = InstallTaskManager()
    assert mgr.stop_service(framework="not-a-framework") is None


# ---------------------------------------------------------------------------
# Docker daemon probe
# ---------------------------------------------------------------------------

def test_daemon_probes_table_includes_docker():
    from chayuan.server.config_panel.runtime_framework_panel import _DAEMON_PROBES
    assert "docker" in _DAEMON_PROBES
    assert "docker-compose" in _DAEMON_PROBES


def test_daemon_running_handles_missing_binary():
    """没装 docker 时不应崩,返 False。"""
    from chayuan.server.config_panel.runtime_framework_panel import _daemon_running
    # 一定不存在的 framework name → 无 probe → False
    assert _daemon_running("not-a-framework") is False


def test_daemon_running_first_probe_failure_falls_through(monkeypatch):
    """前 N 个探针失败、第 N+1 个成功 → 仍认 running。"""
    import subprocess as _sp
    from chayuan.server.config_panel import runtime_framework_panel as rf

    calls = {"n": 0}

    class _FakeRun:
        def __init__(self, returncode, stderr=""):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = ""

    def _fake_run(cmd, *, capture_output, text, timeout, check):
        calls["n"] += 1
        # 第 1 次返非 0,第 2 次仍非 0,第 3 次返 0 (模拟 docker version 失败、
        # info 失败、ps 成功)
        if calls["n"] < 3:
            return _FakeRun(returncode=1, stderr="connection refused")
        return _FakeRun(returncode=0)

    monkeypatch.setattr(_sp, "run", _fake_run)
    assert rf._daemon_running("docker") is True
    assert calls["n"] == 3, "应当尝试到第 3 个 fallback 才成功"


def test_daemon_running_all_probes_fail(monkeypatch):
    """所有探针都失败 → False。"""
    import subprocess as _sp
    from chayuan.server.config_panel import runtime_framework_panel as rf

    class _FakeRun:
        returncode = 99
        stderr = "boom"
        stdout = ""

    monkeypatch.setattr(_sp, "run", lambda *a, **kw: _FakeRun())
    assert rf._daemon_running("docker") is False


def test_daemon_running_handles_filenotfound(monkeypatch):
    """docker 二进制不在 PATH (FileNotFoundError) → False, 不抛。"""
    import subprocess as _sp
    from chayuan.server.config_panel import runtime_framework_panel as rf

    def _raise_fnf(*a, **kw):
        raise FileNotFoundError("docker not in PATH")

    monkeypatch.setattr(_sp, "run", _raise_fnf)
    assert rf._daemon_running("docker") is False


def test_daemon_running_handles_timeout(monkeypatch):
    """子进程 hung → TimeoutExpired → 跳过该探针, 试下一个。"""
    import subprocess as _sp
    from chayuan.server.config_panel import runtime_framework_panel as rf

    calls = {"n": 0}

    class _FakeRun:
        def __init__(self, rc=0):
            self.returncode = rc
            self.stderr = ""
            self.stdout = ""

    def _fake_run(cmd, *, capture_output, text, timeout, check):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _sp.TimeoutExpired(cmd=cmd, timeout=timeout)
        return _FakeRun(rc=0)

    monkeypatch.setattr(_sp, "run", _fake_run)
    assert rf._daemon_running("docker") is True
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Hero strip 排序: enabled+configured > configured > 推荐 > 国外 > 其余
# ---------------------------------------------------------------------------

class _MockProvider:
    def __init__(self, pid: str, tags: tuple = ()):
        self.pid = pid
        self.display_name = pid.upper()
        self.color = "#000"
        self.tags = tags
        self.apply_key_url = ""


def test_hero_strip_prefers_enabled_and_configured():
    from chayuan.server.config_panel.provider_hero_strip import select_top_providers

    providers = [
        _MockProvider("a"),  # 未配置, 普通
        _MockProvider("b", ("推荐",)),  # 推荐
        _MockProvider("c"),  # 已配置已启用
        _MockProvider("d"),  # 已配置但未启用
    ]
    state_table = {
        "a": (False, False, 0),
        "b": (False, False, 0),
        "c": (True, True, 5),
        "d": (False, True, 3),
    }
    rows = select_top_providers(providers, lambda pid: state_table[pid], limit=4)
    # 期望顺序: c (启用+配置) → d (仅配置) → b (推荐) → a (其它)
    pids = [r.pid for r in rows]
    assert pids == ["c", "d", "b", "a"], f"实际: {pids}"


def test_hero_strip_skips_local_providers():
    from chayuan.server.config_panel.provider_hero_strip import select_top_providers

    providers = [
        _MockProvider("ollama-local", ("本地",)),  # 本地, 跳
        _MockProvider("openai"),                   # 云
    ]
    rows = select_top_providers(providers, lambda pid: (False, False, 0), limit=4)
    pids = [r.pid for r in rows]
    assert "ollama-local" not in pids
    assert "openai" in pids


def test_hero_strip_fills_with_recommended_when_few_configured():
    """只有 2 家已配置时,8 个槽位剩 6 个用 推荐/国外/其余 填。"""
    from chayuan.server.config_panel.provider_hero_strip import select_top_providers

    providers = [
        _MockProvider("p1"),
        _MockProvider("p2"),
        _MockProvider("p3", ("推荐",)),
        _MockProvider("p4", ("推荐",)),
        _MockProvider("p5", ("国外",)),
    ]
    state = {
        "p1": (True, True, 1),
        "p2": (True, True, 1),
        "p3": (False, False, 0),
        "p4": (False, False, 0),
        "p5": (False, False, 0),
    }
    rows = select_top_providers(providers, lambda pid: state[pid], limit=8)
    pids = [r.pid for r in rows]
    # 前两位是已配置启用的, 接着是推荐, 最后国外
    assert pids[:2] == ["p1", "p2"]
    assert "p3" in pids and "p4" in pids and "p5" in pids
