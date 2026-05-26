"""runtime/pytorch_installer._find_offline_wheels 回归测试。

PyInstaller spec(chayuan-server.spec)把 wheel 打到子目录布局
``vendor/torch_wheels/<platform>_py<ver>_<variant>/torch-*.whl``,顶层是空的。
早期 _find_offline_wheels 用 ``d.glob("*.whl")`` 非递归扫,装机后永远扫不到,
默默回退在线下载。这套测试守住"必须 rglob"的修法。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def offline_wheels_layout(tmp_path, monkeypatch):
    """模拟 PyInstaller 装机后的 vendor/torch_wheels/ 子目录布局。"""
    root = tmp_path / "vendor" / "torch_wheels"
    sub = root / "win_amd64_py312_cpu"
    sub.mkdir(parents=True)
    torch_whl = sub / "torch-2.6.0+cpu-cp312-cp312-win_amd64.whl"
    tv_whl = sub / "torchvision-0.21.0+cpu-cp312-cp312-win_amd64.whl"
    torch_whl.write_bytes(b"PK\x03\x04fake-torch")
    tv_whl.write_bytes(b"PK\x03\x04fake-tv")
    # 通过 env 让 _offline_wheels_dir 直接命中 root(优先级最高)
    monkeypatch.setenv("CHAYUAN_OFFLINE_WHEELS_DIR", str(root))
    return {"root": root, "sub": sub, "torch": torch_whl, "tv": tv_whl}


def test_find_offline_wheels_cpu_in_subdir(offline_wheels_layout):
    """rglob 必须能从子目录找到 torch + torchvision 配对。"""
    from chayuan.server.runtime import pytorch_installer as pi

    # _offline_wheels_dir 默认不读 CHAYUAN_OFFLINE_WHEELS_DIR env(看现行实现),
    # 直接 monkey patch 它指向 fixture 的 root
    pi_local = pi
    orig = pi._offline_wheels_dir
    try:
        pi._offline_wheels_dir = lambda: offline_wheels_layout["root"]  # type: ignore[attr-defined]
        wheels = pi._find_offline_wheels("cpu")
    finally:
        pi._offline_wheels_dir = orig  # type: ignore[attr-defined]

    assert len(wheels) == 2, f"应该返 [torch, torchvision] 两个,实际 {wheels}"
    names = sorted(w.name for w in wheels)
    assert any(n.startswith("torch-") for n in names)
    assert any(n.startswith("torchvision-") for n in names)


def test_find_offline_wheels_cuda_variant_filter(tmp_path, monkeypatch):
    """同根下 cpu + cu124 两个子目录同时存在时,variant 过滤要把跨 variant 的剔掉。"""
    from chayuan.server.runtime import pytorch_installer as pi

    root = tmp_path / "vendor" / "torch_wheels"
    cpu_dir = root / "win_amd64_py312_cpu"
    cu_dir = root / "win_amd64_py312_cu124"
    cpu_dir.mkdir(parents=True)
    cu_dir.mkdir(parents=True)
    (cpu_dir / "torch-2.6.0+cpu-cp312-cp312-win_amd64.whl").write_bytes(b"x")
    (cpu_dir / "torchvision-0.21.0+cpu-cp312-cp312-win_amd64.whl").write_bytes(b"x")
    (cu_dir / "torch-2.6.0+cu124-cp312-cp312-win_amd64.whl").write_bytes(b"x")
    (cu_dir / "torchvision-0.21.0+cu124-cp312-cp312-win_amd64.whl").write_bytes(b"x")

    orig = pi._offline_wheels_dir
    try:
        pi._offline_wheels_dir = lambda: root  # type: ignore[attr-defined]
        cpu_wheels = pi._find_offline_wheels("cpu")
        cu_wheels = pi._find_offline_wheels("cu124")
    finally:
        pi._offline_wheels_dir = orig  # type: ignore[attr-defined]

    assert len(cpu_wheels) == 2
    assert all("+cpu" in w.name and "+cu124" not in w.name for w in cpu_wheels)
    assert len(cu_wheels) == 2
    assert all("+cu124" in w.name for w in cu_wheels)


def test_find_offline_wheels_returns_empty_when_dir_missing(monkeypatch):
    """offline dir 不存在(None)时返空列表,不抛。"""
    from chayuan.server.runtime import pytorch_installer as pi

    orig = pi._offline_wheels_dir
    try:
        pi._offline_wheels_dir = lambda: None  # type: ignore[attr-defined]
        assert pi._find_offline_wheels("cpu") == []
    finally:
        pi._offline_wheels_dir = orig  # type: ignore[attr-defined]


def test_find_offline_wheels_returns_empty_when_pair_incomplete(tmp_path):
    """只有 torch 没 torchvision(或反之)— 返空,让 caller 走在线 fallback。"""
    from chayuan.server.runtime import pytorch_installer as pi

    root = tmp_path / "wheels"
    sub = root / "win_amd64_py312_cpu"
    sub.mkdir(parents=True)
    (sub / "torch-2.6.0+cpu-cp312-cp312-win_amd64.whl").write_bytes(b"x")
    # 没 torchvision

    orig = pi._offline_wheels_dir
    try:
        pi._offline_wheels_dir = lambda: root  # type: ignore[attr-defined]
        result = pi._find_offline_wheels("cpu")
    finally:
        pi._offline_wheels_dir = orig  # type: ignore[attr-defined]

    assert result == []


# ───────────────────────────────────────────────────────────────
# build_install_recipe:**不再用 pip**,改成调 ``chayuan install-pytorch``
# Click 子命令(pip 不支持被 PyInstaller freeze;wheel 解压安装器替代)
# ───────────────────────────────────────────────────────────────


def test_online_recipe_calls_install_pytorch_no_pip(tmp_path, monkeypatch):
    """在线 recipe 命令应调 ``install-pytorch`` 子命令,且**不含 pip**。"""
    from chayuan.server.runtime import pytorch_installer as pi

    pkg_dir = tmp_path / "py_packages"
    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(pkg_dir))

    # prefer_offline=False 强制走在线分支
    recipe = pi.build_install_recipe("cpu", prefer_offline=False)
    cmd = recipe.cmd

    assert "install-pytorch" in cmd, f"recipe 应调 install-pytorch 子命令:{cmd}"
    assert "pip" not in cmd, f"recipe 不应再用 pip:{cmd}"
    assert "-m" not in cmd or cmd[cmd.index("-m") + 1] != "pip", \
        f"recipe 不应 ``-m pip``:{cmd}"
    assert "--offline" not in cmd, f"在线 recipe 不应带 --offline:{cmd}"


def test_offline_recipe_calls_install_pytorch_with_offline_flag(tmp_path, monkeypatch):
    """离线 recipe 命令应带 ``--offline`` 标志,且不含 pip。"""
    from chayuan.server.runtime import pytorch_installer as pi

    pkg_dir = tmp_path / "py_packages"
    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(pkg_dir))

    wheels_root = tmp_path / "torch_wheels"
    sub = wheels_root / "win_amd64_py312_cpu"
    sub.mkdir(parents=True)
    (sub / "torch-2.6.0+cpu-cp312-cp312-win_amd64.whl").write_bytes(b"x")
    (sub / "torchvision-0.21.0+cpu-cp312-cp312-win_amd64.whl").write_bytes(b"x")

    orig = pi._offline_wheels_dir
    try:
        pi._offline_wheels_dir = lambda: wheels_root  # type: ignore[attr-defined]
        recipe = pi.build_install_recipe("cpu", prefer_offline=True)
    finally:
        pi._offline_wheels_dir = orig  # type: ignore[attr-defined]

    cmd = recipe.cmd
    assert "install-pytorch" in cmd, f"recipe 应调 install-pytorch 子命令:{cmd}"
    assert "--offline" in cmd, f"离线 recipe 必须带 --offline:{cmd}"
    assert "pip" not in cmd, f"recipe 不应再用 pip:{cmd}"


def test_recipe_passes_variant_flag(tmp_path, monkeypatch):
    """recipe 命令应带 ``--variant <variant>`` 把 cpu / cuXXX 透传给 CLI 子命令。"""
    from chayuan.server.runtime import pytorch_installer as pi

    pkg_dir = tmp_path / "py_packages"
    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(pkg_dir))

    cpu_recipe = pi.build_install_recipe("cpu", prefer_offline=False)
    assert "--variant" in cpu_recipe.cmd
    assert cpu_recipe.cmd[cpu_recipe.cmd.index("--variant") + 1] == "cpu"

    cuda_recipe = pi.build_install_recipe("cu124", prefer_offline=False)
    assert "--variant" in cuda_recipe.cmd
    assert cuda_recipe.cmd[cuda_recipe.cmd.index("--variant") + 1] == "cu124"
    # CUDA recipe 的 note 应提到大体积,提醒用户
    assert "GB" in cuda_recipe.note


def test_ensure_torch_target_on_syspath_idempotent(tmp_path, monkeypatch):
    """ensure_torch_target_on_syspath 把生效 torch 目录加进 sys.path,且幂等。

    py_packages/ 里放一份合法 torch —— 此时 auto 模式应选 py_packages 本身
    (防混用:不退回系统 Python 的 torch),把它加进 sys.path 且只加一次。
    """
    import sys

    from chayuan.server.runtime import pytorch_installer as pi

    pkg_dir = tmp_path / "py_packages"
    # py_packages/ 内放一份合法 torch,让 resolve_active_torch_dir(auto)命中它,
    # 行为不受测试机系统 Python 是否装了 torch 影响。
    (pkg_dir / "torch").mkdir(parents=True)
    (pkg_dir / "torch" / "__init__.py").write_text("")
    (pkg_dir / "torch" / "version.py").write_text(
        "__version__ = '2.7.0+cpu'\ncuda = None\n", encoding="utf-8"
    )
    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(pkg_dir))
    # 用 auto 选用配置,避免读到机器上残留的 torch_selection.json
    monkeypatch.setenv(
        "CHAYUAN_TORCH_SELECTION_FILE", str(tmp_path / "torch_selection.json")
    )

    # 清掉可能的残留
    s = str(pkg_dir)
    while s in sys.path:
        sys.path.remove(s)

    try:
        added = pi.ensure_torch_target_on_syspath()
        assert added == s
        assert sys.path.count(s) == 1
        # 再调一次 —— 不应重复插入
        pi.ensure_torch_target_on_syspath()
        assert sys.path.count(s) == 1
    finally:
        while s in sys.path:
            sys.path.remove(s)


def test_ensure_torch_target_no_mix_external_and_py_packages(tmp_path, monkeypatch):
    """防混用:active 是外部目录时,py_packages 不应也被加进 sys.path。

    场景复现「系统 torch 2.11 + py_packages torchvision 0.22 混搭」的根因 ——
    旧实现把 active 与 py_packages 都插进 sys.path,会导致跨目录拼装。新实现只
    加一个 torch 目录。
    """
    import sys

    from chayuan.server.runtime import pytorch_installer as pi

    # py_packages/ 为空(没装 torch);外部目录放一份「系统 torch」。
    pkg_dir = tmp_path / "py_packages"
    pkg_dir.mkdir()
    ext_dir = tmp_path / "system_site_packages"
    (ext_dir / "torch").mkdir(parents=True)
    (ext_dir / "torch" / "__init__.py").write_text("")
    (ext_dir / "torch" / "version.py").write_text(
        "__version__ = '2.11.0+cpu'\ncuda = None\n", encoding="utf-8"
    )
    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(pkg_dir))
    sel_file = tmp_path / "torch_selection.json"
    sel_file.write_text(
        '{"mode": "path", "path": "%s", "prefer": null}' % str(ext_dir),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHAYUAN_TORCH_SELECTION_FILE", str(sel_file))

    ps, es = str(pkg_dir), str(ext_dir)
    for x in (ps, es):
        while x in sys.path:
            sys.path.remove(x)

    try:
        pi.ensure_torch_target_on_syspath()
        # 只加 active(外部目录),py_packages 不加 —— 杜绝跨目录拼装
        assert es in sys.path
        assert ps not in sys.path
    finally:
        for x in (ps, es):
            while x in sys.path:
                sys.path.remove(x)
