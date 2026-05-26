"""LlamaRuntimeManager 单元测试。

mock subprocess.Popen + httpx 不真起 llama-server,只验证状态机 + yaml 持久化。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from chayuan.server.model_registry.local_runtime import (
    LocalRuntimeSettings,
    RuntimeStatus,
)


def test_local_runtime_settings_defaults():
    s = LocalRuntimeSettings()
    assert s.preload_on_startup is True
    assert s.host == "127.0.0.1"
    assert s.port == 62582
    assert s.api_key == ""
    assert s.expose_lan is False
    assert s.default_chat_model == ""


def test_local_runtime_settings_load_save(tmp_path):
    """yaml round-trip:写 → 读 → 值一致"""
    yaml_path = tmp_path / "local_runtime.yaml"
    s = LocalRuntimeSettings(
        preload_on_startup=False,
        host="0.0.0.0",
        port=62590,
        api_key="secret123",
        expose_lan=True,
        default_chat_model="Qwen3-4B-Instruct-2507-Q3_K_S",
    )
    s.save(yaml_path)
    assert yaml_path.is_file()

    s2 = LocalRuntimeSettings.load(yaml_path)
    assert s2.preload_on_startup is False
    assert s2.host == "0.0.0.0"
    assert s2.port == 62590
    assert s2.api_key == "secret123"
    assert s2.expose_lan is True
    assert s2.default_chat_model == "Qwen3-4B-Instruct-2507-Q3_K_S"


def test_local_runtime_settings_load_missing_returns_default(tmp_path):
    """yaml 文件不存在时,load 返回 default 配置而非 raise"""
    yaml_path = tmp_path / "nope.yaml"
    s = LocalRuntimeSettings.load(yaml_path)
    assert s.preload_on_startup is True
    assert s.port == 62582


def test_runtime_status_default():
    st = RuntimeStatus(state="stopped")
    assert st.state == "stopped"
    assert st.endpoint is None
    assert st.pid is None
    assert st.last_error is None


def test_manager_init_paths(tmp_path):
    """manager 构造时定位 runtime_yaml / vendor exe / runtime.json 路径"""
    from chayuan.server.model_registry.local_runtime import LlamaRuntimeManager
    m = LlamaRuntimeManager(chayuan_root=tmp_path)
    assert m.settings_path == tmp_path / "model_registry" / "local_runtime.yaml"
    assert m.status_path == tmp_path / "runtime.json"


def test_manager_find_llama_server_exe_missing(tmp_path, monkeypatch):
    """vendor 二进制找不到时,find_llama_server_exe 返回 None"""
    from chayuan.server.model_registry import local_runtime
    # 隔离开发机仓库自带的 vendor/services/ 预编译 binary,只看 tmp 空目录
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "empty-services"])
    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    assert m.find_llama_server_exe() is None


def test_manager_find_llama_server_exe_present(tmp_path, monkeypatch):
    """vendor/services/llama-server/llama-server.exe 存在时,返回该路径"""
    from chayuan.server.model_registry import local_runtime
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    exe = services / "llama-server.exe"
    exe.write_bytes(b"stub")

    # mock 装机后的搜索路径
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    assert m.find_llama_server_exe() == exe


def test_manager_find_server_exe_prefers_platform_subdir(tmp_path, monkeypatch):
    """优先 platform 子目录(预编译 binary 提交进 git 的布局),fallback 才看扁平。"""
    from chayuan.server.model_registry import local_runtime

    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    # 扁平兜底
    flat = services / "llama-server.exe"
    flat.write_bytes(b"flat")
    # platform 子目录
    monkeypatch.setattr(local_runtime, "_platform_subdir_candidates", lambda: ["win-x64"])
    plat_dir = services / "win-x64"
    plat_dir.mkdir()
    plat_bin = plat_dir / "llama-server.exe"
    plat_bin.write_bytes(b"plat")

    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    assert m.find_server_exe() == plat_bin


def test_manager_find_server_exe_falls_back_to_flat(tmp_path, monkeypatch):
    """platform 子目录里没 binary 时,回到扁平 layout。"""
    from chayuan.server.model_registry import local_runtime

    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    flat = services / "llama-server.exe"
    flat.write_bytes(b"flat")
    # 子目录存在但空
    monkeypatch.setattr(local_runtime, "_platform_subdir_candidates", lambda: ["win-x64"])
    (services / "win-x64").mkdir()

    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    assert m.find_server_exe() == flat


def test_manager_find_server_exe_walks_candidate_list(tmp_path, monkeypatch):
    """Win x64 默认 candidate = [win-x64, win-x64-noavx];第一个空,选第二个。"""
    from chayuan.server.model_registry import local_runtime

    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    monkeypatch.setattr(
        local_runtime, "_platform_subdir_candidates",
        lambda: ["win-x64", "win-x64-noavx"],
    )
    # 主候选目录空
    (services / "win-x64").mkdir()
    # 备选有 binary
    (services / "win-x64-noavx").mkdir()
    fallback_bin = services / "win-x64-noavx" / "llama-server.exe"
    fallback_bin.write_bytes(b"noavx")

    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    assert m.find_server_exe() == fallback_bin


def test_platform_subdir_candidates_env_override(monkeypatch):
    """CHAYUAN_VENDOR_PLATFORM env 覆盖自动检测,返回单值列表。"""
    from chayuan.server.model_registry import local_runtime

    monkeypatch.setenv("CHAYUAN_VENDOR_PLATFORM", "win-x64-noavx")
    assert local_runtime._platform_subdir_candidates() == ["win-x64-noavx"]
    monkeypatch.delenv("CHAYUAN_VENDOR_PLATFORM")
    # 没 env 时返回 OS 自动推断 — 至少有一个候选
    assert local_runtime._platform_subdir_candidates()


def test_manager_allocate_port_default_free(tmp_path):
    """端口默认 62582 没被占用时,_allocate_port 返回 62582"""
    from chayuan.server.model_registry.local_runtime import LlamaRuntimeManager
    m = LlamaRuntimeManager(chayuan_root=tmp_path)
    # 假定测试机 62582 没占(很大概率)
    port = m._allocate_port(preferred=62582)
    assert 62582 <= port <= 62600


def test_manager_allocate_port_bumps_on_conflict(tmp_path, monkeypatch):
    """端口被占时往上 bump,直到找到空闲"""
    import socket
    from chayuan.server.model_registry.local_runtime import LlamaRuntimeManager
    m = LlamaRuntimeManager(chayuan_root=tmp_path)

    # 占住 62582-62584
    occupied = []
    for p in (62582, 62583, 62584):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.listen(1)
            occupied.append(s)
        except OSError:
            s.close()

    try:
        port = m._allocate_port(preferred=62582)
        # 应该是 62585 或后面 (受是否占成功影响)
        assert port not in (s.getsockname()[1] for s in occupied)
    finally:
        for s in occupied:
            s.close()


@pytest.mark.asyncio
async def test_manager_start_spawns_subprocess(tmp_path, monkeypatch):
    """start() 成功 spawn 时,状态变 starting → ready (mock health 200)"""
    from chayuan.server.model_registry import local_runtime
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    exe = services / "llama-server.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    # mock resolve_llamacpp_args 返回 fake args
    fake_resolution = mock.MagicMock(
        missing=[],
        args=["--model", "/tmp/fake.gguf", "--ctx-size", "8192"],
        resolved_models={"chat": "fake-chat-model"},
        reason="",
    )
    monkeypatch.setattr(
        local_runtime, "_resolve_args_for",
        lambda *a, **kw: (fake_resolution, "/tmp/fake.gguf"),
    )

    # mock Popen + httpx
    fake_proc = mock.MagicMock(pid=12345, poll=mock.MagicMock(return_value=None))
    monkeypatch.setattr(local_runtime.subprocess, "Popen", mock.MagicMock(return_value=fake_proc))
    async def fake_health(url, **kw):
        return mock.MagicMock(status_code=200)
    monkeypatch.setattr(local_runtime, "_probe_health", fake_health)

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    status = await m.start()

    assert status.state == "ready"
    assert status.pid == 12345
    assert status.endpoint == "http://127.0.0.1:62582"
    assert status.model_id == "fake-chat-model"


@pytest.mark.asyncio
async def test_manager_start_injects_ld_library_path_for_dynamic_binary(tmp_path, monkeypatch):
    """Linux/Mac spawn 时应注入 LD_LIBRARY_PATH=$exe.parent / DYLD_LIBRARY_PATH。"""
    from chayuan.server.model_registry import local_runtime
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    exe = services / "llama-server"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    fake_resolution = mock.MagicMock(
        missing=[],
        args=["--model", "/tmp/fake.gguf"],
        resolved_models={"chat": "fake-chat-model"},
        reason="",
    )
    monkeypatch.setattr(
        local_runtime, "_resolve_args_for",
        lambda *a, **kw: (fake_resolution, "/tmp/fake.gguf"),
    )

    captured: dict = {}

    def fake_popen(args, **kw):
        captured["env"] = kw.get("env")
        return mock.MagicMock(pid=4321, poll=mock.MagicMock(return_value=None))

    monkeypatch.setattr(local_runtime.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(local_runtime.sys, "platform", "linux")

    async def fake_health(url, **kw):
        return mock.MagicMock(status_code=200)
    monkeypatch.setattr(local_runtime, "_probe_health", fake_health)

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    await m.start()

    env = captured["env"]
    assert env is not None  # 不能裸 inherit
    assert str(services) in env.get("LD_LIBRARY_PATH", "")
    # 其它环境变量(PATH 等)应该被继承
    assert "PATH" in env


@pytest.mark.asyncio
async def test_manager_start_missing_exe_fails(tmp_path, monkeypatch):
    """vendor 二进制缺失时,start() 状态 → failed"""
    from chayuan.server.model_registry import local_runtime
    # 隔离开发机仓库自带 vendor/services/ 预编译 binary,只看 tmp 空目录
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "empty-services"])
    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    status = await m.start()
    assert status.state == "failed"
    # last_error 提到 llama-server 或 model 未就绪都视为符合预期
    err = (status.last_error or "").lower()
    assert "llama-server" in err or "model" in err or "candidate" in err


@pytest.mark.asyncio
async def test_manager_stop_kills_process(tmp_path, monkeypatch):
    """stop() 调 terminate + wait,状态 → stopped"""
    from chayuan.server.model_registry import local_runtime

    fake_proc = mock.MagicMock(pid=12345)
    fake_proc.poll.return_value = None  # 进程还活着
    fake_proc.terminate = mock.MagicMock()
    fake_proc.wait = mock.MagicMock(return_value=0)

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    m._process = fake_proc
    m._status = local_runtime.RuntimeStatus(state="ready", pid=12345)

    await m.stop()

    fake_proc.terminate.assert_called_once()
    fake_proc.wait.assert_called_once()
    assert m._status.state == "stopped"
    assert m._status.pid is None


@pytest.mark.asyncio
async def test_manager_restart_stop_then_start(tmp_path, monkeypatch):
    """restart() 应该等价于 stop + start"""
    from chayuan.server.model_registry import local_runtime
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    (services / "llama-server.exe").write_bytes(b"stub")
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    fake_resolution = mock.MagicMock(
        missing=[],
        args=["--model", "/tmp/fake.gguf"],
        resolved_models={"chat": "m1"},
        reason="",
    )
    monkeypatch.setattr(local_runtime, "_resolve_args_for", lambda *a, **kw: (fake_resolution, "/tmp/fake.gguf"))

    proc1 = mock.MagicMock(pid=111, poll=mock.MagicMock(return_value=None), wait=mock.MagicMock(return_value=0))
    proc2 = mock.MagicMock(pid=222, poll=mock.MagicMock(return_value=None), wait=mock.MagicMock(return_value=0))
    popen_mock = mock.MagicMock(side_effect=[proc1, proc2])
    monkeypatch.setattr(local_runtime.subprocess, "Popen", popen_mock)
    async def fake_health(url, **kw):
        return mock.MagicMock(status_code=200)
    monkeypatch.setattr(local_runtime, "_probe_health", fake_health)

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    s1 = await m.start()
    assert s1.pid == 111
    s2 = await m.restart()
    assert s2.pid == 222


def test_get_manager_singleton(tmp_path, monkeypatch):
    """get_manager() 返回 registry 里的 chat manager。"""
    from chayuan.server.model_registry import local_runtime
    from chayuan.server.model_registry import local_runtime_registry as lrr
    monkeypatch.setattr(local_runtime, "_singleton", None)
    monkeypatch.setattr(lrr, "_registry_singleton", None)
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", str(tmp_path))

    m1 = local_runtime.get_manager()
    m2 = local_runtime.get_manager()
    assert m1 is m2
    assert m1.capability == "chat"


@pytest.mark.asyncio
async def test_manager_start_llama_early_exit_captured(tmp_path, monkeypatch):
    """llama-server spawn 后立即退出(如 AVX2 不支持),stderr 应被捕获到 last_error"""
    from chayuan.server.model_registry import local_runtime
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    (services / "llama-server.exe").write_bytes(b"stub")
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    fake_resolution = mock.MagicMock(
        missing=[],
        args=["--model", "/tmp/fake.gguf"],
        resolved_models={"chat": "fake-chat-model"},
        reason="",
    )
    monkeypatch.setattr(
        local_runtime, "_resolve_args_for",
        lambda *a, **kw: (fake_resolution, "/tmp/fake.gguf"),
    )

    # 进程 spawn 即 exit,stderr 有 AVX2 报错
    fake_proc = mock.MagicMock()
    fake_proc.pid = 12345
    fake_proc.poll = mock.MagicMock(return_value=1)  # 进程已退出
    fake_proc.stdout = mock.MagicMock()
    fake_proc.stdout.read = mock.MagicMock(return_value=b"")  # AVX2 报错通过 stderr,stdout 空
    fake_proc.stderr = mock.MagicMock()
    fake_proc.stderr.read = mock.MagicMock(return_value=b"AVX2 instruction not supported by CPU\n")
    monkeypatch.setattr(local_runtime.subprocess, "Popen", mock.MagicMock(return_value=fake_proc))

    # health 不应被调到(因为 poll 立即捕获)
    health_called = []
    async def fake_health(url, **kw):
        health_called.append(url)
        return mock.MagicMock(status_code=200)
    monkeypatch.setattr(local_runtime, "_probe_health", fake_health)

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    status = await m.start()

    assert status.state == "failed"
    assert "AVX2" in (status.last_error or "")
    assert health_called == []  # 进程退出后不再探 health


@pytest.mark.asyncio
async def test_manager_start_health_timeout_kills(tmp_path, monkeypatch):
    """/health 60s 内不返 200,start 应 kill 进程并报 failed"""
    from chayuan.server.model_registry import local_runtime
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    (services / "llama-server.exe").write_bytes(b"stub")
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    fake_resolution = mock.MagicMock(
        missing=[],
        args=["--model", "/tmp/fake.gguf"],
        resolved_models={"chat": "fake-chat-model"},
        reason="",
    )
    monkeypatch.setattr(
        local_runtime, "_resolve_args_for",
        lambda *a, **kw: (fake_resolution, "/tmp/fake.gguf"),
    )

    # 进程活着但 health 永远 raise(连不上)
    fake_proc = mock.MagicMock()
    fake_proc.pid = 12345
    fake_proc.poll = mock.MagicMock(return_value=None)
    fake_proc.terminate = mock.MagicMock()
    fake_proc.wait = mock.MagicMock(return_value=0)
    monkeypatch.setattr(local_runtime.subprocess, "Popen", mock.MagicMock(return_value=fake_proc))

    async def fake_health(url, **kw):
        raise ConnectionError("refused")
    monkeypatch.setattr(local_runtime, "_probe_health", fake_health)

    # 把 deadline 拉到 0.3 秒避免测试卡 60s
    monkeypatch.setattr(local_runtime, "HEALTH_READY_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(local_runtime, "HEALTH_PROBE_INTERVAL_SEC", 0.05)

    m = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path)
    status = await m.start()

    assert status.state == "failed"
    assert "health" in (status.last_error or "").lower() or "200" in (status.last_error or "")
    # 超时后必须 kill 进程别留尸
    fake_proc.terminate.assert_called_once()


def test_local_runtime_settings_new_capability_fields_defaults():
    """新加的 4 个字段有默认值,旧测试不破。"""
    s = LocalRuntimeSettings()
    assert s.preload_embedding is False
    assert s.preload_rerank is False
    assert s.default_embedding_model == ""
    assert s.default_rerank_model == ""


def test_local_runtime_settings_new_fields_round_trip(tmp_path):
    """新字段 yaml round-trip 正确。"""
    yaml_path = tmp_path / "lr.yaml"
    s = LocalRuntimeSettings(
        preload_embedding=True,
        preload_rerank=True,
        default_embedding_model="bge-small-zh",
        default_rerank_model="bge-rerank-m3",
    )
    s.save(yaml_path)
    s2 = LocalRuntimeSettings.load(yaml_path)
    assert s2.preload_embedding is True
    assert s2.preload_rerank is True
    assert s2.default_embedding_model == "bge-small-zh"
    assert s2.default_rerank_model == "bge-rerank-m3"


def test_local_runtime_settings_old_yaml_compat(tmp_path):
    """没有新字段的旧 yaml 加载时新字段取默认。"""
    yaml_path = tmp_path / "lr.yaml"
    yaml_path.write_text(
        "preload_on_startup: true\nhost: 127.0.0.1\nport: 62582\n",
        encoding="utf-8",
    )
    s = LocalRuntimeSettings.load(yaml_path)
    assert s.preload_embedding is False
    assert s.preload_rerank is False
    assert s.default_embedding_model == ""


def test_manager_capability_defaults_to_chat(tmp_path):
    """构造器不传 capability 时默认 chat (向后兼容)。"""
    from chayuan.server.model_registry.local_runtime import LlamaRuntimeManager
    m = LlamaRuntimeManager(chayuan_root=tmp_path)
    assert m.capability == "chat"
    assert m.port_offset == 0


def test_manager_capability_embedding_uses_port_offset(tmp_path):
    """embedding manager port_offset=1,allocate_port preferred = settings.port + 1。"""
    from chayuan.server.model_registry.local_runtime import LlamaRuntimeManager
    m = LlamaRuntimeManager(chayuan_root=tmp_path, capability="embedding", port_offset=1)
    assert m.capability == "embedding"
    assert m.port_offset == 1


def test_manager_persist_status_uses_capability_key(tmp_path):
    """_persist_status 写 runtime.json 时按 capability 分 key,
    多个 manager 写不互覆盖。"""
    import json
    from chayuan.server.model_registry.local_runtime import LlamaRuntimeManager, RuntimeStatus

    m_chat = LlamaRuntimeManager(chayuan_root=tmp_path, capability="chat", port_offset=0)
    m_embed = LlamaRuntimeManager(chayuan_root=tmp_path, capability="embedding", port_offset=1)

    m_chat._status = RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62582", pid=111)
    m_chat._persist_status()
    m_embed._status = RuntimeStatus(state="ready", endpoint="http://127.0.0.1:62583", pid=222)
    m_embed._persist_status()

    data = json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))
    # llama key 下含 chat 和 embedding 两个子 key,不互覆盖
    assert data["llama"]["chat"]["pid"] == 111
    assert data["llama"]["embedding"]["pid"] == 222


def test_manager_find_llama_server_exe_unchanged(tmp_path, monkeypatch):
    """find_llama_server_exe 行为不受 capability 影响。"""
    from chayuan.server.model_registry import local_runtime
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    exe = services / "llama-server.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(local_runtime, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    m_chat = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path, capability="chat")
    m_embed = local_runtime.LlamaRuntimeManager(chayuan_root=tmp_path, capability="embedding", port_offset=1)
    assert m_chat.find_llama_server_exe() == exe
    assert m_embed.find_llama_server_exe() == exe


@pytest.mark.asyncio
async def test_manager_start_embedding_uses_embedding_resolver(tmp_path, monkeypatch):
    """embedding manager 调 start 时用 resolve_llamacpp_args(capability='embedding')。"""
    from chayuan.server.model_registry import local_runtime as lr
    from chayuan.server.model_registry import process_args

    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    (services / "llama-server.exe").write_bytes(b"stub")
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    captured_capability = []

    def fake_resolve(**kw):
        captured_capability.append(kw.get("capability", "<missing>"))
        return process_args.Resolution(
            process="llamacpp",
            args=["--model", "/tmp/embed.gguf", "--embedding", "--pooling", "cls"],
            resolved_models={"embedding": "bge"},
        )

    monkeypatch.setattr(lr.process_args, "resolve_llamacpp_args", fake_resolve)

    # mock Popen + health
    fake_proc = mock.MagicMock(pid=999, poll=mock.MagicMock(return_value=None))
    monkeypatch.setattr(lr.subprocess, "Popen", mock.MagicMock(return_value=fake_proc))
    async def fake_health(url, **kw):
        return mock.MagicMock(status_code=200)
    monkeypatch.setattr(lr, "_probe_health", fake_health)

    m = lr.LlamaRuntimeManager(chayuan_root=tmp_path, capability="embedding", port_offset=1)
    status = await m.start()
    assert status.state == "ready"
    assert captured_capability == ["embedding"]


@pytest.mark.asyncio
async def test_manager_start_rerank_uses_rerank_resolver(tmp_path, monkeypatch):
    """rerank manager 调 start 时用 resolve_llamacpp_args(capability='rerank')。"""
    from chayuan.server.model_registry import local_runtime as lr
    from chayuan.server.model_registry import process_args

    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    (services / "llama-server.exe").write_bytes(b"stub")
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    captured = []

    def fake_resolve(**kw):
        captured.append(kw.get("capability"))
        return process_args.Resolution(
            process="llamacpp",
            args=["--model", "/tmp/r.gguf", "--reranking"],
            resolved_models={"rerank": "bge-r"},
        )

    monkeypatch.setattr(lr.process_args, "resolve_llamacpp_args", fake_resolve)

    fake_proc = mock.MagicMock(pid=888, poll=mock.MagicMock(return_value=None))
    monkeypatch.setattr(lr.subprocess, "Popen", mock.MagicMock(return_value=fake_proc))
    async def fake_health(url, **kw):
        return mock.MagicMock(status_code=200)
    monkeypatch.setattr(lr, "_probe_health", fake_health)

    m = lr.LlamaRuntimeManager(chayuan_root=tmp_path, capability="rerank", port_offset=2)
    status = await m.start()
    assert status.state == "ready"
    assert "rerank" in captured


def test_sidecar_runtime_manager_default_engine_is_llama(tmp_path):
    """SidecarRuntimeManager 默认 engine='llama'(向后兼容)。"""
    from chayuan.server.model_registry.local_runtime import SidecarRuntimeManager
    m = SidecarRuntimeManager(chayuan_root=tmp_path)
    assert m.engine == "llama"
    assert m.capability == "chat"


def test_sidecar_runtime_manager_whisper_engine(tmp_path):
    """SidecarRuntimeManager(engine='whisper') 字段正确。"""
    from chayuan.server.model_registry.local_runtime import SidecarRuntimeManager
    m = SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr", port_offset=3
    )
    assert m.engine == "whisper"
    assert m.capability == "asr"
    assert m.port_offset == 3


def test_llama_runtime_manager_alias_inherits_sidecar(tmp_path):
    """LlamaRuntimeManager 是 SidecarRuntimeManager 子类,默认 engine='llama'。"""
    from chayuan.server.model_registry.local_runtime import (
        LlamaRuntimeManager, SidecarRuntimeManager,
    )
    m = LlamaRuntimeManager(chayuan_root=tmp_path)
    assert isinstance(m, SidecarRuntimeManager)
    assert m.engine == "llama"


def test_llama_runtime_manager_alias_with_capability_kw(tmp_path):
    """Plan 3B 已有写法 LlamaRuntimeManager(chayuan_root=..., capability='embedding', port_offset=1) 仍 work。"""
    from chayuan.server.model_registry.local_runtime import LlamaRuntimeManager
    m = LlamaRuntimeManager(chayuan_root=tmp_path, capability="embedding", port_offset=1)
    assert m.engine == "llama"
    assert m.capability == "embedding"
    assert m.port_offset == 1


# ---------------------------------------------------------------------------
# Plan 3C Task 3: find_server_exe 按 engine 找 binary
# ---------------------------------------------------------------------------

def test_find_server_exe_llama_engine(tmp_path, monkeypatch):
    """engine='llama' 找 llama-server[.exe]。"""
    from chayuan.server.model_registry import local_runtime as lr
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    exe = services / "llama-server.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    m = lr.SidecarRuntimeManager(chayuan_root=tmp_path, engine="llama")
    assert m.find_server_exe() == exe


def test_find_server_exe_whisper_engine(tmp_path, monkeypatch):
    """engine='whisper' 找 whisper-server[.exe]。"""
    from chayuan.server.model_registry import local_runtime as lr
    services = tmp_path / "services" / "whisper-server"
    services.mkdir(parents=True)
    exe = services / "whisper-server.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    m = lr.SidecarRuntimeManager(chayuan_root=tmp_path, engine="whisper", capability="asr", port_offset=3)
    assert m.find_server_exe() == exe


def test_find_server_exe_missing_returns_none(tmp_path, monkeypatch):
    """vendor 目录里没装,返回 None。"""
    from chayuan.server.model_registry import local_runtime as lr
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])
    m = lr.SidecarRuntimeManager(chayuan_root=tmp_path, engine="whisper")
    assert m.find_server_exe() is None


def test_find_llama_server_exe_back_compat(tmp_path, monkeypatch):
    """Plan 3B 已有 find_llama_server_exe 方法名,保留可调(返同一结果)。"""
    from chayuan.server.model_registry import local_runtime as lr
    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    exe = services / "llama-server.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    m = lr.LlamaRuntimeManager(chayuan_root=tmp_path)
    assert m.find_llama_server_exe() == exe
    assert m.find_server_exe() == exe


# ---------------------------------------------------------------------------
# Plan 3C Task 4: _resolve_args_for 按 engine 派发 + start() 传 self.engine
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sidecar_whisper_start_uses_whisper_resolver(tmp_path, monkeypatch):
    """engine='whisper' 启动时调 resolve_whisper_args(capability='asr')。"""
    from chayuan.server.model_registry import local_runtime as lr
    from chayuan.server.model_registry import process_args

    services = tmp_path / "services" / "whisper-server"
    services.mkdir(parents=True)
    (services / "whisper-server.exe").write_bytes(b"stub")
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    captured = []

    def fake_whisper_resolve(**kw):
        captured.append(("whisper", kw.get("capability", "<missing>")))
        return process_args.Resolution(
            process="whispercpp",
            args=["--model", "/tmp/ggml-tiny.bin"],
            resolved_models={"asr": "whisper-tiny"},
        )

    monkeypatch.setattr(lr.process_args, "resolve_whisper_args", fake_whisper_resolve)

    fake_proc = mock.MagicMock(pid=777, poll=mock.MagicMock(return_value=None))
    monkeypatch.setattr(lr.subprocess, "Popen", mock.MagicMock(return_value=fake_proc))
    async def fake_health(url, **kw):
        return mock.MagicMock(status_code=200)
    monkeypatch.setattr(lr, "_probe_health", fake_health)

    m = lr.SidecarRuntimeManager(chayuan_root=tmp_path, engine="whisper", capability="asr", port_offset=3)
    status = await m.start()
    assert status.state == "ready"
    assert captured == [("whisper", "asr")]


@pytest.mark.asyncio
async def test_sidecar_llama_start_still_uses_llama_resolver(tmp_path, monkeypatch):
    """engine='llama'(Plan 3B 行为)仍走 resolve_llamacpp_args。"""
    from chayuan.server.model_registry import local_runtime as lr
    from chayuan.server.model_registry import process_args

    services = tmp_path / "services" / "llama-server"
    services.mkdir(parents=True)
    (services / "llama-server.exe").write_bytes(b"stub")
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "services"])

    captured = []
    monkeypatch.setattr(
        lr.process_args, "resolve_llamacpp_args",
        lambda **kw: (captured.append(("llama", kw.get("capability"))), process_args.Resolution(
            process="llamacpp",
            args=["--model", "/tmp/qwen.gguf", "--ctx-size", "8192"],
            resolved_models={"chat": "qwen"},
        ))[1],
    )

    fake_proc = mock.MagicMock(pid=111, poll=mock.MagicMock(return_value=None))
    monkeypatch.setattr(lr.subprocess, "Popen", mock.MagicMock(return_value=fake_proc))
    async def fake_health(url, **kw):
        return mock.MagicMock(status_code=200)
    monkeypatch.setattr(lr, "_probe_health", fake_health)

    m = lr.SidecarRuntimeManager(chayuan_root=tmp_path, engine="llama", capability="chat")
    status = await m.start()
    assert status.state == "ready"
    assert captured == [("llama", "chat")]


def test_local_runtime_settings_asr_fields_defaults():
    """Plan 3C: preload_asr / default_asr_model 默认值。"""
    s = LocalRuntimeSettings()
    assert s.preload_asr is False
    assert s.default_asr_model == ""


def test_local_runtime_settings_asr_round_trip(tmp_path):
    """Plan 3C: asr 字段 yaml round-trip。"""
    yaml_path = tmp_path / "lr.yaml"
    s = LocalRuntimeSettings(preload_asr=True, default_asr_model="whisper-tiny")
    s.save(yaml_path)
    s2 = LocalRuntimeSettings.load(yaml_path)
    assert s2.preload_asr is True
    assert s2.default_asr_model == "whisper-tiny"


def test_local_runtime_settings_old_yaml_no_asr_field(tmp_path):
    """Plan 3B 写的 yaml(无 asr 字段)加载时 asr 取默认值。"""
    yaml_path = tmp_path / "lr.yaml"
    yaml_path.write_text(
        "preload_on_startup: true\nhost: 127.0.0.1\nport: 62582\n"
        "preload_embedding: false\npreload_rerank: false\n",
        encoding="utf-8",
    )
    s = LocalRuntimeSettings.load(yaml_path)
    assert s.preload_asr is False
    assert s.default_asr_model == ""


# ---------------------------------------------------------------------------
# Plan 3D Task 2: find_server_exe(engine='infinity') 返 sys.executable
# ---------------------------------------------------------------------------

def test_find_server_exe_infinity_engine_returns_python(tmp_path):
    """engine='infinity' find_server_exe 返 sys.executable(Python 解释器)。"""
    import sys
    from chayuan.server.model_registry.local_runtime import SidecarRuntimeManager
    m = SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="infinity", capability="image-embedding", port_offset=4
    )
    exe = m.find_server_exe()
    assert exe is not None
    assert exe == Path(sys.executable)


def test_find_server_exe_infinity_not_affected_by_install_dirs(tmp_path, monkeypatch):
    """engine='infinity' 不查 _INSTALL_SERVICES_DIRS,直接用 sys.executable。"""
    from chayuan.server.model_registry import local_runtime as lr
    # 即使 install dirs 为空,infinity 也能返 python 解释器
    monkeypatch.setattr(lr, "_INSTALL_SERVICES_DIRS", [tmp_path / "nonexistent"])
    m = lr.SidecarRuntimeManager(chayuan_root=tmp_path, engine="infinity", port_offset=4)
    exe = m.find_server_exe()
    assert exe is not None


# ---------------------------------------------------------------------------
# Plan 3D Task 3: _resolve_args_for 加 infinity 分支 → resolve_image_embedding_args
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sidecar_infinity_start_uses_image_embedding_resolver(tmp_path, monkeypatch):
    """engine='infinity' 启动时调 resolve_image_embedding_args(capability='image-embedding')。"""
    from chayuan.server.model_registry import local_runtime as lr
    from chayuan.server.model_registry import process_args

    captured = []

    def fake_image_emb_resolve(**kw):
        captured.append(("infinity", kw.get("capability", "<missing>")))
        return process_args.Resolution(
            process="infinity",
            args=["-m", "chayuan.server.image_source.infinity_server", "--model", "siglip2-base"],
            resolved_models={"image-embedding": "siglip2-base"},
        )

    monkeypatch.setattr(lr.process_args, "resolve_image_embedding_args", fake_image_emb_resolve)

    fake_proc = mock.MagicMock(pid=555, poll=mock.MagicMock(return_value=None))
    monkeypatch.setattr(lr.subprocess, "Popen", mock.MagicMock(return_value=fake_proc))
    async def fake_health(url, **kw):
        return mock.MagicMock(status_code=200)
    monkeypatch.setattr(lr, "_probe_health", fake_health)

    m = lr.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="infinity", capability="image-embedding", port_offset=4
    )
    status = await m.start()
    assert status.state == "ready"
    assert captured == [("infinity", "image-embedding")]


# ---------------------------------------------------------------------------
# Plan 3D Task 6: LocalRuntimeSettings 加 preload_image_embedding + default_image_embedding_model
# ---------------------------------------------------------------------------

def test_local_runtime_settings_image_embedding_fields_defaults():
    """Plan 3D: preload_image_embedding / default_image_embedding_model 默认值。"""
    s = LocalRuntimeSettings()
    assert s.preload_image_embedding is False
    assert s.default_image_embedding_model == ""


def test_local_runtime_settings_image_embedding_round_trip(tmp_path):
    """Plan 3D: image-embedding 字段 yaml round-trip。"""
    yaml_path = tmp_path / "lr.yaml"
    s = LocalRuntimeSettings(
        preload_image_embedding=True,
        default_image_embedding_model="siglip2-base",
    )
    s.save(yaml_path)
    s2 = LocalRuntimeSettings.load(yaml_path)
    assert s2.preload_image_embedding is True
    assert s2.default_image_embedding_model == "siglip2-base"


def test_local_runtime_settings_old_yaml_no_image_embedding_field(tmp_path):
    """Plan 3C 写的 yaml(无 image-embedding 字段)加载时取默认值。"""
    yaml_path = tmp_path / "lr.yaml"
    yaml_path.write_text(
        "preload_on_startup: true\nhost: 127.0.0.1\nport: 62582\n"
        "preload_embedding: false\npreload_rerank: false\npreload_asr: false\n",
        encoding="utf-8",
    )
    s = LocalRuntimeSettings.load(yaml_path)
    assert s.preload_image_embedding is False
    assert s.default_image_embedding_model == ""


# ════════════════════════════════════════════════════════════════════════
# 自愈机制:PIPE drain + mark_unhealthy + ensure_ready 自动重启
# ════════════════════════════════════════════════════════════════════════

import time as _time
import threading as _threading


def test_mark_unhealthy_under_threshold_keeps_ready(tmp_path):
    """单次 mark_unhealthy 不触发 failed — 阈值是 2 次/30s 窗口。"""
    from chayuan.server.model_registry import local_runtime
    m = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    # 模拟当前 ready
    m._status = local_runtime.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:1")
    triggered = m.mark_unhealthy(reason="test1")
    assert triggered is False, "1 次未到阈值,不应触发"
    assert m.status.state == "ready"


def test_mark_unhealthy_above_threshold_flips_to_failed(tmp_path):
    """2 次 mark_unhealthy(在窗口内)→ state=failed + last_error 含日志尾。"""
    from chayuan.server.model_registry import local_runtime
    m = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    m._status = local_runtime.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:1")
    # 模拟一些日志已被 drainer 累积
    m._stderr_buf.append("[whisper] decoding...")
    m._stderr_buf.append("[whisper] segment 0 -> 你好")
    assert m.mark_unhealthy("first") is False
    triggered = m.mark_unhealthy("second")
    assert triggered is True, "2 次累计应触发"
    assert m.status.state == "failed"
    assert "PIPE 阻塞" in (m.status.last_error or "")
    assert "你好" in (m.status.last_error or ""), "应把 ring buffer 日志尾追加到 last_error"


def test_mark_unhealthy_events_outside_window_dont_count(tmp_path, monkeypatch):
    """窗口外(>30s)的老 event 不算 — 第二次 mark 时只看到自己,不触发。"""
    from chayuan.server.model_registry import local_runtime
    m = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    m._status = local_runtime.RuntimeStatus(state="ready")
    # 注入一个 60s 前的旧 event
    m._unhealthy_events.append(_time.time() - 60)
    triggered = m.mark_unhealthy("recent")
    # 老 event 在 mark_unhealthy 内会被过滤,新 event=1 → 不触发
    assert triggered is False
    assert m.status.state == "ready"


def test_recent_log_tail_aggregates_stdout_stderr(tmp_path):
    """recent_log_tail 应合并 stdout/stderr ring buffer 后 N 行。"""
    from chayuan.server.model_registry import local_runtime
    m = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    m._stdout_buf.extend(["o1", "o2", "o3"])
    m._stderr_buf.extend(["e1", "e2"])
    tail = m.recent_log_tail(n=10)
    assert "o3" in tail and "e2" in tail
    # 空 buffer:tail 也应空字符串,不抛
    m2 = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    assert m2.recent_log_tail() == ""


def test_pipe_drainer_does_not_block_on_full_pipe(tmp_path):
    """**根因回归测试**:模拟一个长输出的"子进程"(用 os.pipe 当假 stdout),
    确认 drainer 线程持续读 → 写端不会因 kernel buffer 满阻塞。

    如果不开 drainer,Linux 64KB / Win 4KB pipe buffer 写满后 write 阻塞,
    sidecar 主线程 fprintf 卡死 → "用一会就识别不了"经典坑。
    """
    import os
    from chayuan.server.model_registry import local_runtime

    m = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    # 起两对 pipe 模拟 sub.stdout / sub.stderr
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()

    class _FakeProc:
        def __init__(self, stdout_fd, stderr_fd):
            self.stdout = os.fdopen(stdout_fd, "rb", buffering=0)
            self.stderr = os.fdopen(stderr_fd, "rb", buffering=0)

    fake = _FakeProc(out_r, err_r)
    m._start_pipe_drainers(fake)

    # 往 pipe 灌远超 64KB 的内容 — 没 drainer 的话第 ~64KB 字节就 block
    out_writer = os.fdopen(out_w, "wb")
    payload = (b"[whisper] inference line\n") * 5000  # ~125 KB
    # 不要无限等:用单独线程写,主线程 join 1s 看是否完成
    written = {"ok": False}
    def _writer():
        try:
            out_writer.write(payload)
            out_writer.flush()
            out_writer.close()
            written["ok"] = True
        except Exception:
            pass
    t = _threading.Thread(target=_writer, daemon=True)
    t.start()
    t.join(timeout=2.0)
    assert written["ok"], "drainer 应持续读 pipe,写端不应被阻塞"

    # ring buffer 应该有捕到内容
    _time.sleep(0.1)  # 给 drainer 线程 schedule 一下
    assert len(m._stdout_buf) > 0, "drainer 应把读到的行追加到 ring buffer"

    # cleanup:关闭 err 写端让那个 drainer 自然退出
    os.close(err_w)


def test_drainer_buffer_caps_at_200_lines(tmp_path):
    """ring buffer maxlen=200 — 灌 1000 行只留最后 200。防内存膨胀。"""
    from chayuan.server.model_registry import local_runtime
    m = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    for i in range(1000):
        m._stderr_buf.append(f"line {i}")
    assert len(m._stderr_buf) == 200
    assert m._stderr_buf[-1] == "line 999"
    assert m._stderr_buf[0] == "line 800"


def test_ensure_ready_noop_when_already_ready(tmp_path):
    """state=ready 时 ensure_ready 不重启 — 直接返回当前 status。"""
    import asyncio
    from chayuan.server.model_registry import local_runtime
    m = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    m._status = local_runtime.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:1")
    # 若 ensure_ready 误触 start,这里没 mock binary 会失败 — 我们靠"没 raise"判断
    start_called = []
    async def _no_start(**kw):
        start_called.append(kw)
        return m._status
    m.start = _no_start  # type: ignore
    result = asyncio.run(m.ensure_ready())
    assert start_called == [], "ready 时 ensure_ready 不应触发 start"
    assert result.state == "ready"


def test_ensure_ready_calls_start_when_failed(tmp_path):
    """state=failed(mark_unhealthy 触发) → ensure_ready 调 start 重启。"""
    import asyncio
    from chayuan.server.model_registry import local_runtime
    m = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    m._status = local_runtime.RuntimeStatus(state="failed", last_error="ReadTimeout 累计")
    started = []
    stopped = []
    async def _fake_start(*, model_id=None):
        started.append(model_id)
        m._status = local_runtime.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:2")
        return m._status
    async def _fake_stop(**kw):
        stopped.append(True)
        m._process = None
    m.start = _fake_start  # type: ignore
    m.stop = _fake_stop    # type: ignore

    result = asyncio.run(m.ensure_ready(model_id="m"))
    assert result.state == "ready"
    assert started == ["m"], "应触发 start 一次"


def test_ensure_ready_calls_start_when_stopped(tmp_path):
    """state=stopped → ensure_ready 调 start 拉起。"""
    import asyncio
    from chayuan.server.model_registry import local_runtime
    m = local_runtime.SidecarRuntimeManager(
        chayuan_root=tmp_path, engine="whisper", capability="asr",
    )
    m._status = local_runtime.RuntimeStatus(state="stopped")
    started = []
    async def _fake_start(*, model_id=None):
        started.append(model_id)
        m._status = local_runtime.RuntimeStatus(state="ready", endpoint="http://127.0.0.1:3")
        return m._status
    m.start = _fake_start  # type: ignore
    result = asyncio.run(m.ensure_ready())
    assert started == [None]
    assert result.state == "ready"
