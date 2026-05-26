"""runtime/pytorch_installer 的「torch 多目录探测 + 用户自选 torch 目录」测试。

覆盖 PyTorch 安装 UX 增强:
  - validate_torch_dir:校验某目录是否有合法 torch(含「选到 torch/ 子目录」回退);
  - scan_torch_locations:跨目录汇总 CPU / CUDA 版各自存在与否;
  - get/set_torch_selection:auto / path / disabled 配置读写 + path 校验;
  - resolve_active_torch_dir:按配置算出生效目录(含 prefer 优先级);
  - torch_runtime_unavailable_reason:image-embedding 失败链路复用的可执行指引。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_torch_dir(root: Path, *, cuda: bool, version: str = "2.7.0") -> Path:
    """在 ``root`` 下造一份最小合法 torch(torch/__init__.py + version.py)。"""
    (root / "torch").mkdir(parents=True, exist_ok=True)
    (root / "torch" / "__init__.py").write_text("", encoding="utf-8")
    if cuda:
        body = f"__version__ = '{version}+cu124'\ncuda = '12.4'\n"
    else:
        body = f"__version__ = '{version}+cpu'\ncuda = None\n"
    (root / "torch" / "version.py").write_text(body, encoding="utf-8")
    return root


@pytest.fixture
def pi(monkeypatch, tmp_path):
    """隔离 pytorch_installer:py_packages 与 torch_selection.json 都落 tmp。"""
    from chayuan.server.runtime import pytorch_installer as _pi

    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(tmp_path / "py_packages"))
    monkeypatch.setenv("CHAYUAN_TORCH_SELECTION_FILE", str(tmp_path / "sel.json"))
    return _pi


def test_validate_torch_dir_rejects_empty(pi, tmp_path):
    r = pi.validate_torch_dir(str(tmp_path / "empty_does_not_exist"))
    assert r["valid"] is False
    assert "不存在" in r["reason"]


def test_validate_torch_dir_detects_cpu(pi, tmp_path):
    d = _make_torch_dir(tmp_path / "cpu_site", cuda=False)
    r = pi.validate_torch_dir(str(d))
    assert r["valid"] is True
    assert r["torch_version"] == "2.7.0"
    assert r["is_cuda"] is False
    assert r["torch_cuda_build"] == "cpu"


def test_validate_torch_dir_detects_cuda(pi, tmp_path):
    d = _make_torch_dir(tmp_path / "cuda_site", cuda=True)
    r = pi.validate_torch_dir(str(d))
    assert r["valid"] is True
    assert r["is_cuda"] is True
    assert r["torch_cuda_build"] == "cu124"


def test_validate_torch_dir_rebases_when_pointed_at_torch_subdir(pi, tmp_path):
    """用户可能选到 torch/ 目录本身 —— 自动回退到父目录。"""
    d = _make_torch_dir(tmp_path / "site", cuda=False)
    r = pi.validate_torch_dir(str(d / "torch"))
    assert r["valid"] is True
    assert r["dir"] == str(d)


def test_torch_selection_default_is_auto(pi):
    sel = pi.get_torch_selection()
    assert sel == {"mode": "auto", "path": None, "prefer": None}


def test_set_torch_selection_disabled(pi):
    cfg = pi.set_torch_selection(mode="disabled")
    assert cfg["mode"] == "disabled"
    assert pi.get_torch_selection()["mode"] == "disabled"
    # disabled → 无生效目录 + 给出可执行指引
    assert pi.resolve_active_torch_dir() is None
    reason = pi.torch_runtime_unavailable_reason()
    assert reason and "不使用 PyTorch" in reason


def test_set_torch_selection_path_validates(pi, tmp_path):
    # 非 torch 目录 → 拒绝
    with pytest.raises(ValueError):
        pi.set_torch_selection(mode="path", path=str(tmp_path / "nope"))
    # 合法 torch 目录 → 采纳,resolve 指向它
    d = _make_torch_dir(tmp_path / "external", cuda=False)
    cfg = pi.set_torch_selection(mode="path", path=str(d))
    assert cfg["mode"] == "path"
    assert cfg["path"] == str(d)
    assert pi.resolve_active_torch_dir() == str(d)
    # path 模式有生效目录 → 不报「不可用」
    assert pi.torch_runtime_unavailable_reason() is None


def test_resolve_active_prefers_cuda_when_both_present(pi):
    """CPU + CUDA 两版都装时,默认(prefer 缺省)优先 GPU 版。"""
    cpu_dir = "/fake/cpu"
    cuda_dir = "/fake/cuda"
    sel = {"mode": "auto", "path": None, "prefer": None}
    got = pi.resolve_active_torch_dir(
        selection=sel, has_cpu=True, has_cuda=True,
        cpu_dir=cpu_dir, cuda_dir=cuda_dir,
    )
    assert got == cuda_dir
    # prefer=cpu → 选 CPU 版
    sel_cpu = {"mode": "auto", "path": None, "prefer": "cpu"}
    got = pi.resolve_active_torch_dir(
        selection=sel_cpu, has_cpu=True, has_cuda=True,
        cpu_dir=cpu_dir, cuda_dir=cuda_dir,
    )
    assert got == cpu_dir


def test_scan_torch_locations_finds_py_packages(pi, tmp_path, monkeypatch):
    """py_packages/ 里放了 CPU torch → scan 汇总 has_cpu=True。"""
    # 屏蔽系统 site-packages 探测,避免测试机自带的 torch 干扰断言
    monkeypatch.setattr(pi, "_system_site_packages_dirs", lambda: [])
    pkg = tmp_path / "py_packages"
    _make_torch_dir(pkg, cuda=False)
    out = pi.scan_torch_locations()
    assert out["has_cpu"] is True
    assert out["cpu_dir"] == str(pkg)
    assert any(loc["valid"] for loc in out["locations"])
    assert out["selection"]["mode"] == "auto"
    assert out["active_dir"] == str(pkg)


def test_torch_runtime_unavailable_reason_when_nothing_installed(pi, tmp_path, monkeypatch):
    """没装任何 torch + auto 模式 → 给出去设置页装的指引。"""
    # 屏蔽系统 site-packages 探测,确保没有任何真实 torch 命中
    monkeypatch.setattr(pi, "_system_site_packages_dirs", lambda: [])
    reason = pi.torch_runtime_unavailable_reason()
    assert reason and "PyTorch" in reason
    assert "本地模型服务" in reason


def test_resolve_active_auto_prefers_py_packages_over_system(pi, tmp_path, monkeypatch):
    """防混用核心:auto 模式下 py_packages/ 自带 torch 时,绝不退回系统 Python。

    复现根因 —— 系统 Python 装着 torch 2.11(版本不可控),py_packages/ 里是
    本应用装的自洽 torch 2.7。auto 必须选 py_packages,否则 import torch 命中
    系统 2.11、import torchvision 命中 py_packages 0.22 → torchvision::nms 崩。
    """
    # py_packages/ 里放一份本应用装的 torch 2.7(自洽配对的一套)
    pkg = tmp_path / "py_packages"
    _make_torch_dir(pkg, cuda=False, version="2.7.0")
    # 系统 site-packages 里放一份版本不配套的 torch 2.11
    sys_sp = tmp_path / "system_site_packages"
    _make_torch_dir(sys_sp, cuda=False, version="2.11.0")
    monkeypatch.setattr(pi, "_system_site_packages_dirs", lambda: [sys_sp])

    # auto 模式(默认)→ 必须返回 py_packages,而不是系统目录
    got = pi.resolve_active_torch_dir()
    assert got == str(pkg)


def test_resolve_active_auto_falls_back_to_system_when_no_py_packages(pi, tmp_path, monkeypatch):
    """py_packages/ 没装 torch 时,auto 才退回系统 Python 的 torch。"""
    # py_packages/ 为空
    (tmp_path / "py_packages").mkdir()
    sys_sp = tmp_path / "system_site_packages"
    _make_torch_dir(sys_sp, cuda=False, version="2.11.0")
    monkeypatch.setattr(pi, "_system_site_packages_dirs", lambda: [sys_sp])

    got = pi.resolve_active_torch_dir()
    assert got == str(sys_sp)
