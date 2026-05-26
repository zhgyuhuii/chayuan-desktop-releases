"""local_index 原子写入的 Windows 重试回归测试。

行为合同
--------
``_atomic_replace_with_retry`` 必须:
1. ``os.replace`` 一次成功 → 立即返回,不 sleep。
2. ``os.replace`` 前几次抛 ``PermissionError``([WinError 5] 拒绝访问)、
   之后成功 → 退避重试直到成功。
3. ``os.replace`` 始终失败 → 重试耗尽后把 ``PermissionError`` 抛出去。

背景:Windows 上 Defender 实时扫描 / Search 索引器 / OneDrive 同步盘会
瞬时锁住刚写好的文件,``os.replace`` 覆盖时报 [WinError 5]。线上反馈:
"写 ...local_models.json 失败:[WinError 5] 拒绝访问"。
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from chayuan.server.model_registry.local_index import _atomic_replace_with_retry


def test_replace_succeeds_first_try_no_sleep():
    calls = []
    with (
        patch("os.replace", lambda a, b: calls.append((a, b))),
        patch("time.sleep") as mock_sleep,
    ):
        _atomic_replace_with_retry("a.tmp", Path("dst.json"))
    assert len(calls) == 1
    mock_sleep.assert_not_called()


def test_replace_retries_then_succeeds():
    attempt = {"n": 0}

    def flaky_replace(_a, _b):
        attempt["n"] += 1
        if attempt["n"] < 3:
            raise PermissionError("[WinError 5] 拒绝访问")
        return None

    with patch("os.replace", flaky_replace), patch("time.sleep") as mock_sleep:
        _atomic_replace_with_retry("a.tmp", Path("dst.json"))

    assert attempt["n"] == 3  # 失败 2 次,第 3 次成功
    assert mock_sleep.call_count == 2  # 失败后各 sleep 一次


def test_replace_always_fails_raises_after_retries():
    def always_denied(_a, _b):
        raise PermissionError("[WinError 5] 拒绝访问")

    with (
        patch("os.replace", always_denied),
        patch("time.sleep"),
        pytest.raises(PermissionError, match="WinError 5"),
    ):
        _atomic_replace_with_retry("a.tmp", Path("dst.json"), attempts=4)
