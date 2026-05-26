"""``packaging/pyinstaller/build.py`` 的 ``--release`` 串联烟雾测试。

只测 argv 构造与 subprocess 调用顺序;不真跑 PyInstaller / chayuan_packaging。

build.py 不是常规 Python 包,所以这里用 importlib.util 按文件路径加载。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest import mock


_BUILD_PATH = (
    Path(__file__).resolve().parents[4]
    / "packaging" / "pyinstaller" / "build.py"
)


def _load_build_module() -> ModuleType:
    """按文件路径加载 ``build.py`` 为匿名 module(避免污染 sys.path)。"""
    assert _BUILD_PATH.is_file(), f"build.py not found at {_BUILD_PATH}"
    spec = importlib.util.spec_from_file_location("chayuan_build_for_test", _BUILD_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────── argv 构造 ───────────────────────


def test_chayuan_pack_cmd_basic():
    bm = _load_build_module()
    cmd = bm.chayuan_pack_cmd("lite", "fetch")
    # 第一段是 python 解释器路径
    assert cmd[0] == sys.executable
    assert cmd[1:] == ["-m", "chayuan_packaging", "fetch", "--release", "lite"]


def test_chayuan_pack_cmd_offline_appends_flag():
    bm = _load_build_module()
    cmd = bm.chayuan_pack_cmd("pro", "stage", offline=True)
    assert "--offline" in cmd
    # --release pro 必须保留
    assert "--release" in cmd and "pro" in cmd


# ─────────────────────── chain 调用顺序 ───────────────────────


def test_run_chayuan_pack_executes_fetch_then_stage():
    bm = _load_build_module()
    calls: list[list[str]] = []
    with mock.patch.object(bm.subprocess, "check_call",
                           side_effect=lambda cmd, cwd: calls.append(list(cmd))):
        bm.run_chayuan_pack("lite")
    # 必须先 fetch 再 stage
    assert len(calls) == 2
    assert calls[0][3] == "fetch"
    assert calls[1][3] == "stage"
    # 两步都必须带 --release lite
    for c in calls:
        assert "--release" in c
        idx = c.index("--release")
        assert c[idx + 1] == "lite"


def test_run_chayuan_pack_offline_forwards_flag_to_both_steps():
    bm = _load_build_module()
    calls: list[list[str]] = []
    with mock.patch.object(bm.subprocess, "check_call",
                           side_effect=lambda cmd, cwd: calls.append(list(cmd))):
        bm.run_chayuan_pack("standard", offline=True)
    for c in calls:
        assert "--offline" in c


def test_run_chayuan_pack_stops_on_fetch_failure():
    """fetch 挂了不应继续 stage——否则会用半截 cache 打出坏包。"""
    import subprocess as sp

    bm = _load_build_module()
    calls: list[list[str]] = []

    def _fail_first(cmd, cwd):
        calls.append(list(cmd))
        if calls[-1][3] == "fetch":
            raise sp.CalledProcessError(returncode=1, cmd=cmd)

    with mock.patch.object(bm.subprocess, "check_call", side_effect=_fail_first):
        try:
            bm.run_chayuan_pack("lite")
        except sp.CalledProcessError:
            pass
    # 只跑了 fetch,没有 stage
    assert len(calls) == 1
    assert calls[0][3] == "fetch"
