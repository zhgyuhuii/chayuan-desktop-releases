"""96 题:probe_all 并发执行 + 单 check 3 秒硬超时。

业务背景:
  service_config_page 调 probe_all() 渲染卡片。如果某个 check 慢(docker
  subprocess 在 Windows 上 4-10s),串行模式下 N 个慢 check 会卡几十秒,
  NiceGUI 客户端超时 → 页面打不开。

  现修为并发执行,每个 check 硬超时 3s,**任一慢 check 不再阻塞整页**。
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest


class _FastCheck:
    service = "fast"
    label = "Fast"
    level = "optional"

    def probe(self):
        from chayuan.server.config_panel.service_checks import ServiceStatus
        time.sleep(0.05)
        return ServiceStatus(
            service="fast", label="Fast", level="optional",
            ok=True, configured=True, endpoint="x", detail="ok",
        )


class _SlowCheck:
    service = "slow"
    label = "Slow"
    level = "optional"

    def probe(self):
        from chayuan.server.config_panel.service_checks import ServiceStatus
        # 故意阻塞 5 秒,远超 3s 超时阈值
        time.sleep(5.0)
        return ServiceStatus(
            service="slow", label="Slow", level="optional",
            ok=True, configured=True, endpoint="", detail="finally",
        )


class _CrashCheck:
    service = "crash"
    label = "Crash"
    level = "optional"

    def probe(self):
        raise RuntimeError("intentional")


def test_probe_all_runs_concurrently():
    """3 个 0.5s 的 check 并发跑应当 ~0.5s 完成,不是 1.5s。"""
    from chayuan.server.config_panel.service_checks import probe_all

    class _HalfSec:
        service = ""; label = ""; level = "optional"
        def __init__(self, n):
            self.service = f"s{n}"
            self.label = f"L{n}"
        def probe(self):
            from chayuan.server.config_panel.service_checks import ServiceStatus
            time.sleep(0.5)
            return ServiceStatus(
                service=self.service, label=self.label, level="optional",
                ok=True, configured=True, endpoint="", detail="ok",
            )

    fakes = [_HalfSec(i) for i in range(3)]
    with patch(
        "chayuan.server.config_panel.service_checks.all_checks",
        return_value=fakes,
    ):
        t0 = time.time()
        out = probe_all()
        elapsed = time.time() - t0
    assert len(out) == 3
    # 串行 1.5s,并发应当 < 1s。给点 buffer 算 1.0s
    assert elapsed < 1.0, f"probe_all 不是并发的,耗时 {elapsed:.2f}s"


def test_probe_all_slow_check_times_out_in_3s():
    """单个 5s check 应当在 3s 后被标 'timeout',不阻塞 probe_all 返回。"""
    from chayuan.server.config_panel.service_checks import probe_all

    fakes = [_FastCheck(), _SlowCheck()]
    with patch(
        "chayuan.server.config_panel.service_checks.all_checks",
        return_value=fakes,
    ):
        t0 = time.time()
        out = probe_all()
        elapsed = time.time() - t0
    # 不应等满 5 秒
    assert elapsed < 4.0, f"超时未生效,耗时 {elapsed:.2f}s"

    by_svc = {s.service: s for s in out}
    assert by_svc["fast"].ok is True
    assert by_svc["slow"].ok is False
    assert "超时" in by_svc["slow"].detail


def test_probe_all_crashed_check_returns_friendly_error():
    """check 内部抛异常 → 不冒泡,转成红色 ServiceStatus。"""
    from chayuan.server.config_panel.service_checks import probe_all

    fakes = [_FastCheck(), _CrashCheck()]
    with patch(
        "chayuan.server.config_panel.service_checks.all_checks",
        return_value=fakes,
    ):
        out = probe_all()

    by_svc = {s.service: s for s in out}
    assert by_svc["fast"].ok is True
    assert by_svc["crash"].ok is False
    assert "RuntimeError" in by_svc["crash"].detail
    assert "intentional" in by_svc["crash"].detail


def test_probe_all_returns_empty_when_all_checks_raises():
    """all_checks() 抛 → 返空列表,不冒泡。"""
    from chayuan.server.config_panel.service_checks import probe_all

    with patch(
        "chayuan.server.config_panel.service_checks.all_checks",
        side_effect=RuntimeError("catalog broken"),
    ):
        assert probe_all() == []


def test_probe_all_handles_empty_check_list():
    from chayuan.server.config_panel.service_checks import probe_all
    with patch(
        "chayuan.server.config_panel.service_checks.all_checks",
        return_value=[],
    ):
        assert probe_all() == []


def test_probe_all_real_world_smoke():
    """端到端:用真 all_checks() 跑一次,确保 < 5s 内返回。"""
    from chayuan.server.config_panel.service_checks import probe_all

    t0 = time.time()
    out = probe_all()
    elapsed = time.time() - t0
    assert elapsed < 5.0, f"真实环境 probe_all 耗时 {elapsed:.2f}s,卡顿!"
    assert isinstance(out, list)
