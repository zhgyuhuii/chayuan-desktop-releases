"""runtime/pytorch_installer 无 pip wheel 安装器测试。

覆盖 wheel 解压(``_extract_wheel``)、平台 tag 探测、幂等检查,以及一个
**可联网时才跑**的真实「下小 pure wheel + 解压」端到端验证(filelock)。

为什么单建文件
==============
test_pytorch_installer_offline_wheels.py 守的是 ``_find_offline_wheels`` /
``build_install_recipe`` 的 recipe 形态;本文件守的是真正干活的
``install_pytorch_wheels`` 链路 —— 下载 / zipfile 解压 / 校验。
"""
from __future__ import annotations

import io
import os
import urllib.request
import zipfile
from pathlib import Path

import pytest


def _make_fake_wheel(path: Path, pkg: str = "demo", ver: str = "1.0") -> None:
    """造一个最小合规 wheel(zip):含 ``<pkg>/`` + ``.dist-info/`` + ``.data/``。"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{pkg}/__init__.py", "VALUE = 1\n")
        zf.writestr(f"{pkg}/sub/mod.py", "X = 2\n")
        zf.writestr(f"{pkg}-{ver}.dist-info/METADATA", f"Name: {pkg}\nVersion: {ver}\n")
        zf.writestr(f"{pkg}-{ver}.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        # .data/purelib 内容要被摊进 target
        zf.writestr(f"{pkg}-{ver}.data/purelib/extra_pkg/__init__.py", "Y = 3\n")
        # .data/scripts 要被忽略
        zf.writestr(f"{pkg}-{ver}.data/scripts/demo-cli", "#!/bin/sh\necho hi\n")


# ───────────────────────────── _extract_wheel ─────────────────────────────


def test_extract_wheel_layout(tmp_path):
    """解压:<pkg>/ + dist-info 直接进 target;.data/purelib 摊平;.data/scripts 丢弃。"""
    from chayuan.server.runtime import pytorch_installer as pi

    whl = tmp_path / "demo-1.0-py3-none-any.whl"
    _make_fake_wheel(whl)
    target = tmp_path / "py_packages"

    logs: list = []
    pi._extract_wheel(whl, target, log=logs.append)

    assert (target / "demo" / "__init__.py").is_file()
    assert (target / "demo" / "sub" / "mod.py").is_file()
    assert (target / "demo-1.0.dist-info" / "METADATA").is_file()
    # .data/purelib 内容摊进 target(去掉 .data/purelib 前缀)
    assert (target / "extra_pkg" / "__init__.py").is_file()
    # .data/scripts 被忽略
    assert not (target / "demo-1.0.data").exists()
    assert not (target / "demo-cli").exists()
    assert any("extract" in m for m in logs)


def test_extract_wheel_rejects_zip_slip(tmp_path):
    """含越界路径(../)的 wheel 必须被拒绝,不写到 target 外。"""
    from chayuan.server.runtime import pytorch_installer as pi

    whl = tmp_path / "evil-1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.writestr("../escaped.py", "PWNED = 1\n")
    target = tmp_path / "py_packages"

    with pytest.raises(pi.WheelInstallError):
        pi._extract_wheel(whl, target, log=lambda m: None)
    assert not (tmp_path / "escaped.py").exists()


# ──────────────────────── 平台 / py tag 探测 ────────────────────────


def test_py_tag_format():
    from chayuan.server.runtime import pytorch_installer as pi

    tag = pi._py_tag()
    assert tag.startswith("cp3")
    assert tag[2:].isdigit()


def test_host_platform_tags_nonempty_on_known_os():
    """已知 OS(linux/win/mac)上必须能给出至少一个 platform tag。"""
    import sys

    from chayuan.server.runtime import pytorch_installer as pi

    tags = pi._host_platform_tags()
    if sys.platform.startswith(("win", "linux")) or sys.platform == "darwin":
        assert tags, f"{sys.platform} 应有 platform tag"
        assert all(isinstance(t, str) and t for t in tags)


def test_torch_cdn_url_shape():
    """torch CDN URL 应是 download.pytorch.org/whl/cpu 下的 .whl。"""
    import sys

    from chayuan.server.runtime import pytorch_installer as pi

    tags = pi._torch_wheel_platform_tags()
    if not tags:
        pytest.skip("当前平台不在 torch wheel 映射里")
    url = pi._torch_cdn_wheel_url("torch", pi._TORCH_VERSION, "cpu", tags[0])
    assert url.startswith("https://download.pytorch.org/whl/cpu/")
    assert url.endswith(".whl")
    assert pi._TORCH_VERSION in url
    # 非 macOS wheel 文件名带 +cpu local 标识
    if sys.platform != "darwin":
        assert "%2Bcpu" in url or "+cpu" in url


def test_torch_cdn_url_cuda_variant():
    """CUDA variant 的 CDN URL 应走 /whl/cuXXX index,文件名带 +cuXXX 标识。"""
    import sys

    from chayuan.server.runtime import pytorch_installer as pi

    tags = pi._torch_wheel_platform_tags()
    if not tags:
        pytest.skip("当前平台不在 torch wheel 映射里")
    if sys.platform == "darwin":
        # macOS 没有 CUDA wheel,应直接拒绝
        with pytest.raises(pi.WheelInstallError):
            pi._torch_cdn_wheel_url("torch", pi._TORCH_VERSION, "cu124", tags[0])
        return
    url = pi._torch_cdn_wheel_url("torch", pi._TORCH_VERSION, "cu124", tags[0])
    assert url.startswith("https://download.pytorch.org/whl/cu124/")
    assert "cu124" in url
    assert url.endswith(".whl")


def test_torch_cdn_url_rejects_unknown_variant():
    """未知 variant 应抛 WheelInstallError。"""
    from chayuan.server.runtime import pytorch_installer as pi

    with pytest.raises(pi.WheelInstallError):
        pi._torch_cdn_wheel_url("torch", pi._TORCH_VERSION, "cu999", "win_amd64")


def test_resolve_torch_url_picks_first_working(monkeypatch):
    """_resolve_torch_cdn_wheel_url:跳过探测失败的候选,返回第一个能下载的。"""
    from chayuan.server.runtime import pytorch_installer as pi

    tags = pi._torch_wheel_platform_tags()
    if not tags:
        pytest.skip("当前平台不在 torch wheel 映射里")
    # 只让最后一个候选 tag 的 URL "存在",前面的都当 404
    good = pi._torch_cdn_wheel_url("torch", pi._TORCH_VERSION, "cpu", tags[-1])
    monkeypatch.setattr(pi, "_url_exists", lambda u: u == good)
    assert pi._resolve_torch_cdn_wheel_url("torch", pi._TORCH_VERSION, "cpu") == good


def test_resolve_torch_url_all_fail_raises(monkeypatch):
    """所有候选都探测失败 → 抛 WheelInstallError。"""
    from chayuan.server.runtime import pytorch_installer as pi

    if not pi._torch_wheel_platform_tags():
        pytest.skip("当前平台不在 torch wheel 映射里")
    monkeypatch.setattr(pi, "_url_exists", lambda u: False)
    with pytest.raises(pi.WheelInstallError):
        pi._resolve_torch_cdn_wheel_url("torch", pi._TORCH_VERSION, "cpu")


# ──────────────────── version.py 解析 / 扫描 py_packages ────────────────────


def test_parse_torch_version_py_cpu(tmp_path):
    """CPU 版 torch/version.py:__version__ 带 +cpu,cuda = None。"""
    from chayuan.server.runtime import pytorch_installer as pi

    vp = tmp_path / "version.py"
    vp.write_text(
        "__version__ = '2.7.0+cpu'\n"
        "debug = False\n"
        "cuda: Optional[str] = None\n"
        "git_version = 'abc'\n",
        encoding="utf-8",
    )
    ver, build = pi._parse_torch_version_py(vp)
    assert ver == "2.7.0"
    assert build == "cpu"


def test_parse_torch_version_py_cuda_from_local(tmp_path):
    """CUDA 版:__version__ 带 +cu124,优先采纳 local 段。"""
    from chayuan.server.runtime import pytorch_installer as pi

    vp = tmp_path / "version.py"
    vp.write_text(
        "__version__ = '2.7.0+cu124'\n"
        "cuda: Optional[str] = '12.4'\n",
        encoding="utf-8",
    )
    ver, build = pi._parse_torch_version_py(vp)
    assert ver == "2.7.0"
    assert build == "cu124"


def test_parse_torch_version_py_cuda_from_cuda_field(tmp_path):
    """__version__ 无 local 段时,从 cuda='12.4' 字段推出 cu124。"""
    from chayuan.server.runtime import pytorch_installer as pi

    vp = tmp_path / "version.py"
    vp.write_text(
        "__version__ = '2.7.0'\n"
        "cuda = '12.4'\n",
        encoding="utf-8",
    )
    ver, build = pi._parse_torch_version_py(vp)
    assert ver == "2.7.0"
    assert build == "cu124"


def test_scan_installed_torch_not_installed(tmp_path, monkeypatch):
    """py_packages/ 下没 torch → torch_installed=False,但 target_dir 仍给出。"""
    from chayuan.server.runtime import pytorch_installer as pi

    pkg_dir = tmp_path / "py_packages"
    pkg_dir.mkdir()
    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(pkg_dir))

    r = pi.scan_installed_torch()
    assert r["torch_installed"] is False
    assert r["is_cuda"] is False
    assert r["target_dir"] == str(pkg_dir)


def test_scan_installed_torch_cpu(tmp_path, monkeypatch):
    """py_packages/torch/ 完整 + CPU version.py → 识别为 CPU 版。"""
    from chayuan.server.runtime import pytorch_installer as pi

    pkg_dir = tmp_path / "py_packages"
    (pkg_dir / "torch").mkdir(parents=True)
    (pkg_dir / "torch" / "__init__.py").write_text("")
    (pkg_dir / "torch" / "version.py").write_text(
        "__version__ = '2.7.0+cpu'\ncuda = None\n", encoding="utf-8"
    )
    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(pkg_dir))

    r = pi.scan_installed_torch()
    assert r["torch_installed"] is True
    assert r["torch_version"] == "2.7.0"
    assert r["torch_cuda_build"] == "cpu"
    assert r["is_cuda"] is False


def test_scan_installed_torch_cuda(tmp_path, monkeypatch):
    """torch/version.py 带 +cu124 → is_cuda=True。"""
    from chayuan.server.runtime import pytorch_installer as pi

    pkg_dir = tmp_path / "py_packages"
    (pkg_dir / "torch").mkdir(parents=True)
    (pkg_dir / "torch" / "__init__.py").write_text("")
    (pkg_dir / "torch" / "version.py").write_text(
        "__version__ = '2.7.0+cu124'\ncuda = '12.4'\n", encoding="utf-8"
    )
    # torchvision 也放一份,验证版本从 dist-info 读
    (pkg_dir / "torchvision").mkdir()
    (pkg_dir / "torchvision" / "__init__.py").write_text("")
    di = pkg_dir / "torchvision-0.22.0.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Name: torchvision\nVersion: 0.22.0\n", encoding="utf-8")
    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(pkg_dir))

    r = pi.scan_installed_torch()
    assert r["torch_installed"] is True
    assert r["torch_cuda_build"] == "cu124"
    assert r["is_cuda"] is True
    assert r["torchvision_installed"] is True
    assert r["torchvision_version"] == "0.22.0"


def test_install_rejects_unknown_variant(tmp_path, monkeypatch):
    """install_pytorch_wheels 收到未知 variant → action=failed,不下载。"""
    from chayuan.server.runtime import pytorch_installer as pi

    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(tmp_path / "py_packages"))
    result = pi.install_pytorch_wheels(variant="cu999", log=lambda m: None)
    assert result["action"] == "failed"


# ──────────────────────── 幂等 / 目标目录 ────────────────────────


def test_already_installed_detection(tmp_path):
    """_torch_already_installed:torch/__init__.py + version.py 都在才算装好。"""
    from chayuan.server.runtime import pytorch_installer as pi

    target = tmp_path / "py_packages"
    assert pi._torch_already_installed(target) is False

    (target / "torch").mkdir(parents=True)
    (target / "torch" / "__init__.py").write_text("")
    assert pi._torch_already_installed(target) is False  # 还缺 version.py

    (target / "torch" / "version.py").write_text("__version__='x'")
    assert pi._torch_already_installed(target) is True


def test_install_skips_when_already_installed(tmp_path, monkeypatch):
    """install_pytorch_wheels:已装过且非 force → 直接 skipped,不下载。"""
    from chayuan.server.runtime import pytorch_installer as pi

    pkg_dir = tmp_path / "py_packages"
    (pkg_dir / "torch").mkdir(parents=True)
    (pkg_dir / "torch" / "__init__.py").write_text("")
    (pkg_dir / "torch" / "version.py").write_text("__version__='2.7.0'")
    monkeypatch.setenv("CHAYUAN_PY_PACKAGES_DIR", str(pkg_dir))

    result = pi.install_pytorch_wheels(prefer_offline=True, log=lambda m: None)
    assert result["action"] == "skipped"


# ──────────────────── 真实联网:下小 pure wheel + 解压 ────────────────────


def _has_network() -> bool:
    try:
        urllib.request.urlopen("https://pypi.org/pypi/filelock/json", timeout=8).close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _has_network(), reason="无网络,跳过真实下载验证")
def test_real_download_and_extract_pure_wheel(tmp_path):
    """端到端:从 PyPI 解析 filelock 的 wheel URL → 下载 → zipfile 解压 → 校验。

    filelock 是纯 python 小包(~20 KB),用它验证「解析 URL + 下载 + 解压」整条链路
    正确,而不用拖 ~250 MB 的 torch。
    """
    from chayuan.server.runtime import pytorch_installer as pi

    logs: list = []
    url = pi._resolve_pypi_wheel_url("filelock", "3.18.0", pure=True, log=logs.append)
    assert url.endswith(".whl")
    assert "filelock" in url

    dest = tmp_path / url.rsplit("/", 1)[-1].split("#")[0]
    pi._download_file(url, dest, log=logs.append)
    assert dest.is_file() and dest.stat().st_size > 0

    target = tmp_path / "py_packages"
    pi._extract_wheel(dest, target, log=logs.append)
    # filelock 解压后应有 filelock/__init__.py
    assert (target / "filelock" / "__init__.py").is_file()
