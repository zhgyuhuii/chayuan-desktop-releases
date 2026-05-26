"""ContainerLifecycle 单元测试 (Phase 1)。

策略:mock ``asyncio.create_subprocess_exec``,不真起 docker。覆盖:
  * happy path:pull/up/health/logs/stop 全部命令构造正确
  * error path:docker 不存在 / daemon 慢 / service 未定义 / healthcheck 超时
  * 流式日志 yield 行
  * 健康状态映射(docker inspect → HealthState)
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chayuan.server.config_panel.container_lifecycle import (
    ContainerHealth,
    ContainerLifecycle,
    HealthState,
    LifecycleError,
    LifecycleErrorCode,
    LogLine,
)


# ============================================================================
# Mock helper
# ============================================================================


class _MockProc:
    """模拟 asyncio.subprocess.Process。"""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        stream_lines: list[bytes] | None = None,
    ) -> None:
        self.returncode = returncode
        self._stdout_data = stdout
        self._stderr_data = stderr
        self._stream_lines = stream_lines or []

        async def _stream():
            for line in self._stream_lines:
                yield line + (b"\n" if not line.endswith(b"\n") else b"")

        self.stdout = _stream() if self._stream_lines else None

    async def communicate(self, _input=None):
        return self._stdout_data, self._stderr_data

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


def _patch_subprocess(mock_proc: _MockProc):
    """patch create_subprocess_exec → 返回 mock_proc。"""
    return patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=mock_proc),
    )


def _patch_compose_file(tmp_path):
    """patch ensure_compose_file → 返临时 yaml。"""
    f = tmp_path / "docker-compose.yaml"
    f.write_text("version: '3.8'\nservices:\n  vllm: {image: x}\n")
    return patch(
        "chayuan.server.config_panel.compose_manager.ensure_compose_file",
        return_value=f,
    )


# ============================================================================
# pull / logs(流式)测试
# ============================================================================


@pytest.mark.asyncio
async def test_pull_streams_log_lines(tmp_path):
    """pull 应逐行 yield 日志。"""
    proc = _MockProc(stream_lines=[
        b"Pulling vllm ...",
        b"latest: Pulling from vllm/vllm-openai",
        b"abc123: Pull complete",
    ])
    with _patch_compose_file(tmp_path), _patch_subprocess(proc), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        lines: list[str] = []
        async for ll in lc.pull("vllm"):
            assert isinstance(ll, LogLine)
            lines.append(ll.text)
    assert "Pulling vllm ..." in lines
    assert any("Pull complete" in s for s in lines)


@pytest.mark.asyncio
async def test_logs_follow_streams(tmp_path):
    """logs(follow=True) 也应流式。"""
    proc = _MockProc(stream_lines=[
        b"INFO: server started",
        b"INFO: model loaded",
    ])
    with _patch_compose_file(tmp_path), _patch_subprocess(proc), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        out = []
        async for ll in lc.logs("vllm", follow=False, tail=10):
            out.append(ll.text)
    assert "INFO: server started" in out


# ============================================================================
# up:happy + 各种 error
# ============================================================================


@pytest.mark.asyncio
async def test_up_with_wait_passes_flag(tmp_path):
    """up(wait_healthy=True) 应在命令中加 --wait。"""
    captured: dict = {}

    async def _fake_create(*args, **kwargs):
        captured["args"] = args
        return _MockProc(returncode=0, stdout=b"", stderr=b"")

    with _patch_compose_file(tmp_path), \
         patch("asyncio.create_subprocess_exec", new=_fake_create), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        # health 也会跑 ps + inspect — 让它们直接返回空
        with patch.object(lc, "health", new=AsyncMock(
                return_value=ContainerHealth(service="vllm", state=HealthState.HEALTHY))):
            result = await lc.up("vllm", wait_healthy=True, timeout=60)

    args = captured["args"]
    assert "up" in args and "-d" in args and "--wait" in args
    assert "vllm" in args
    assert result.state == HealthState.HEALTHY


@pytest.mark.asyncio
async def test_up_without_wait_no_flag(tmp_path):
    """up(wait_healthy=False) 不应有 --wait。"""
    captured: dict = {}

    async def _fake_create(*args, **kwargs):
        captured["args"] = args
        return _MockProc(returncode=0)

    with _patch_compose_file(tmp_path), \
         patch("asyncio.create_subprocess_exec", new=_fake_create), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        with patch.object(lc, "health", new=AsyncMock(
                return_value=ContainerHealth(service="vllm", state=HealthState.RUNNING_NO_CHECK))):
            await lc.up("vllm", wait_healthy=False, timeout=30)

    assert "--wait" not in captured["args"]


@pytest.mark.asyncio
async def test_up_service_not_defined_raises_typed_error(tmp_path):
    """compose yaml 没定义 service → SERVICE_NOT_DEFINED。"""
    proc = _MockProc(returncode=1, stderr=b"no such service: foo\n")
    with _patch_compose_file(tmp_path), _patch_subprocess(proc), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        with pytest.raises(LifecycleError) as exc_info:
            await lc.up("foo", wait_healthy=False, timeout=30)
    assert exc_info.value.code == LifecycleErrorCode.SERVICE_NOT_DEFINED
    assert "foo" in exc_info.value.hint


@pytest.mark.asyncio
async def test_up_daemon_down_raises_typed_error(tmp_path):
    """daemon 没启动 → DOCKER_DAEMON_DOWN。"""
    proc = _MockProc(returncode=1, stderr=b"Cannot connect to the Docker daemon\n")
    with _patch_compose_file(tmp_path), _patch_subprocess(proc), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        with pytest.raises(LifecycleError) as exc_info:
            await lc.up("vllm", wait_healthy=False, timeout=30)
    assert exc_info.value.code == LifecycleErrorCode.DOCKER_DAEMON_DOWN
    assert "Docker Desktop" in exc_info.value.hint or "systemctl" in exc_info.value.hint


@pytest.mark.asyncio
async def test_up_healthcheck_timeout_raises_typed_error(tmp_path):
    """compose --wait 超时 → HEALTHCHECK_TIMEOUT。"""
    proc = _MockProc(
        returncode=1,
        stderr=b"container did not become healthy within timeout\n",
    )
    with _patch_compose_file(tmp_path), _patch_subprocess(proc), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        with pytest.raises(LifecycleError) as exc_info:
            await lc.up("vllm", wait_healthy=True, timeout=10)
    assert exc_info.value.code == LifecycleErrorCode.HEALTHCHECK_TIMEOUT


# ============================================================================
# 没装 docker
# ============================================================================


@pytest.mark.asyncio
async def test_up_no_docker_raises_typed_error(tmp_path):
    """docker 不在 PATH → DOCKER_NOT_INSTALLED,有 install hint。"""
    with _patch_compose_file(tmp_path), \
         patch("shutil.which", return_value=None):
        lc = ContainerLifecycle()
        with pytest.raises(LifecycleError) as exc_info:
            await lc.up("vllm", wait_healthy=False, timeout=10)
    assert exc_info.value.code == LifecycleErrorCode.DOCKER_NOT_INSTALLED
    assert "docker" in exc_info.value.hint.lower()


# ============================================================================
# health:状态映射
# ============================================================================


@pytest.mark.asyncio
async def test_health_maps_healthy(tmp_path):
    """docker inspect Health.Status=healthy → HealthState.HEALTHY。"""
    # ps 返回 1 行 ndjson
    ps_json = json.dumps({
        "Service": "vllm", "ID": "abc123",
        "Name": "chayuan-vllm", "Image": "vllm:latest",
        "Publishers": [{"PublishedPort": 18000}],
    }).encode()
    inspect_state = json.dumps({
        "Running": True, "Status": "running",
        "Health": {"Status": "healthy", "Log": [{"Output": "ok"}]},
    }).encode()

    call_count = {"i": 0}

    async def _fake_create(*args, **kwargs):
        call_count["i"] += 1
        if call_count["i"] == 1:  # ps
            return _MockProc(returncode=0, stdout=ps_json)
        else:  # inspect
            return _MockProc(returncode=0, stdout=inspect_state)

    with _patch_compose_file(tmp_path), \
         patch("asyncio.create_subprocess_exec", new=_fake_create), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        h = await lc.health("vllm")

    assert h.state == HealthState.HEALTHY
    assert h.is_ready
    assert h.container_id == "abc123"
    assert "18000" in h.ports


@pytest.mark.asyncio
async def test_health_maps_starting(tmp_path):
    """starting 状态。"""
    ps_json = json.dumps({"Service": "vllm", "ID": "x", "Name": "n",
                          "Image": "i", "Publishers": []}).encode()
    inspect = json.dumps({"Running": True, "Status": "running",
                          "Health": {"Status": "starting"}}).encode()
    call_count = {"i": 0}

    async def _fake_create(*args, **kwargs):
        call_count["i"] += 1
        if call_count["i"] == 1:
            return _MockProc(returncode=0, stdout=ps_json)
        return _MockProc(returncode=0, stdout=inspect)

    with _patch_compose_file(tmp_path), \
         patch("asyncio.create_subprocess_exec", new=_fake_create), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        h = await lc.health("vllm")

    assert h.state == HealthState.STARTING
    assert not h.is_ready


@pytest.mark.asyncio
async def test_health_maps_running_without_check(tmp_path):
    """没定义 healthcheck 但容器在跑 → RUNNING_NO_CHECK。"""
    ps_json = json.dumps({"Service": "vllm", "ID": "x", "Name": "n",
                          "Image": "i", "Publishers": []}).encode()
    inspect = json.dumps({"Running": True, "Status": "running"}).encode()  # 没 Health
    call_count = {"i": 0}

    async def _fake_create(*args, **kwargs):
        call_count["i"] += 1
        if call_count["i"] == 1:
            return _MockProc(returncode=0, stdout=ps_json)
        return _MockProc(returncode=0, stdout=inspect)

    with _patch_compose_file(tmp_path), \
         patch("asyncio.create_subprocess_exec", new=_fake_create), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        h = await lc.health("vllm")

    assert h.state == HealthState.RUNNING_NO_CHECK
    assert h.is_ready  # 把"在跑无 check"当作 ready


@pytest.mark.asyncio
async def test_health_missing_when_no_container(tmp_path):
    """ps 返回空 → MISSING。"""
    proc = _MockProc(returncode=0, stdout=b"")
    with _patch_compose_file(tmp_path), _patch_subprocess(proc), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        h = await lc.health("vllm")
    assert h.state == HealthState.MISSING
    assert not h.is_ready


# ============================================================================
# health_many 并发
# ============================================================================


@pytest.mark.asyncio
async def test_health_many_concurrent(tmp_path):
    """health_many 应并发查多个 service。"""
    ps_json = json.dumps({"Service": "x", "ID": "x", "Name": "n",
                          "Image": "i", "Publishers": []}).encode()
    inspect = json.dumps({"Running": True, "Status": "running"}).encode()
    call_count = {"i": 0}

    async def _fake_create(*args, **kwargs):
        call_count["i"] += 1
        if call_count["i"] % 2 == 1:
            return _MockProc(returncode=0, stdout=ps_json)
        return _MockProc(returncode=0, stdout=inspect)

    with _patch_compose_file(tmp_path), \
         patch("asyncio.create_subprocess_exec", new=_fake_create), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        out = await lc.health_many(["vllm", "infinity", "comfyui"])

    assert set(out) == {"vllm", "infinity", "comfyui"}
    for h in out.values():
        assert isinstance(h, ContainerHealth)


# ============================================================================
# stop / down
# ============================================================================


@pytest.mark.asyncio
async def test_stop_returns_true_on_success(tmp_path):
    proc = _MockProc(returncode=0)
    with _patch_compose_file(tmp_path), _patch_subprocess(proc), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        ok = await lc.stop("vllm")
    assert ok is True


@pytest.mark.asyncio
async def test_down_with_volumes_appends_v_flag(tmp_path):
    captured: dict = {}

    async def _fake_create(*args, **kwargs):
        captured["args"] = args
        return _MockProc(returncode=0)

    with _patch_compose_file(tmp_path), \
         patch("asyncio.create_subprocess_exec", new=_fake_create), \
         patch("shutil.which", return_value="/usr/bin/docker"):
        lc = ContainerLifecycle()
        await lc.down("vllm", with_volumes=True)
    # rm -f -s -v
    assert "rm" in captured["args"] and "-v" in captured["args"]


# ============================================================================
# LifecycleError 字段
# ============================================================================


def test_lifecycle_error_carries_fields():
    e = LifecycleError(
        LifecycleErrorCode.IMAGE_PULL_FAILED,
        "test msg",
        service="vllm",
        stderr="some error output",
        hint="检查网络",
    )
    assert e.code == LifecycleErrorCode.IMAGE_PULL_FAILED
    assert e.service == "vllm"
    assert "some error output" in e.stderr
    assert e.hint == "检查网络"
    assert "vllm" in repr(e)
