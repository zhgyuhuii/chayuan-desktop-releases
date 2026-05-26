"""``packaging.preflight_torch.preflight`` strict_vendor 模式测试。

验证不变量:
  * vendor 已有 wheel 对 → 返回它,无任何下载
  * vendor 缺 wheel + strict_vendor=False → 走 _download(老行为)
  * vendor 缺 wheel + strict_vendor=True → RuntimeError(不下载)
  * vendor 缺 wheel + strict_vendor=True + allow_missing=True → 跳过 + warn
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def pf_module():
    """动态加载 packaging/preflight_torch.py(PyPI ``packaging`` 命名冲突,
    必须用 spec_from_file_location 绕开,跟 build.py 里一样)。"""
    pf_path = Path(__file__).resolve().parent.parent / "packaging" / "preflight_torch.py"
    spec = importlib.util.spec_from_file_location("chayuan_preflight_torch_test", pf_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chayuan_preflight_torch_test"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("chayuan_preflight_torch_test", None)


def _make_target(pf, tmp_path):
    # WheelTarget 是 frozen dataclass,只接受 platform/py_version/variant;
    # subdir / index_url 是 property,从这 3 个字段派生。
    return pf.WheelTarget(platform="win_amd64", py_version="312", variant="cpu")


def test_vendor_ready_short_circuits_download(pf_module, tmp_path):
    """vendor 已有 torch + torchvision wheel 对 → 直接返回,不调 _download。"""
    target = _make_target(pf_module, tmp_path)
    sub = tmp_path / target.subdir
    sub.mkdir()
    (sub / "torch-2.6.0+cpu-cp312-cp312-win_amd64.whl").write_bytes(b"x")
    (sub / "torchvision-0.21.0+cpu-cp312-cp312-win_amd64.whl").write_bytes(b"y")

    with mock.patch.object(pf_module, "_download") as dl:
        result = pf_module.preflight([target], out_root=tmp_path)

    dl.assert_not_called()
    assert len(result) == 2
    assert any(p.name.startswith("torch-") for p in result)
    assert any(p.name.startswith("torchvision-") for p in result)


def test_missing_vendor_lenient_downloads(pf_module, tmp_path):
    """vendor 缺 + strict_vendor=False(默认) → 走 _download。"""
    target = _make_target(pf_module, tmp_path)

    def fake_download(t, out_dir, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "torch-2.6.0+cpu-cp312-cp312-win_amd64.whl").write_bytes(b"x")
        (out_dir / "torchvision-0.21.0+cpu-cp312-cp312-win_amd64.whl").write_bytes(b"y")

    with mock.patch.object(pf_module, "_download", side_effect=fake_download) as dl:
        result = pf_module.preflight([target], out_root=tmp_path)

    dl.assert_called_once()
    assert len(result) == 2


def test_missing_vendor_strict_raises(pf_module, tmp_path):
    """vendor 缺 + strict_vendor=True → RuntimeError,_download 不被调。"""
    target = _make_target(pf_module, tmp_path)

    with mock.patch.object(pf_module, "_download") as dl:
        with pytest.raises(RuntimeError, match="strict-vendor"):
            pf_module.preflight([target], out_root=tmp_path, strict_vendor=True)

    dl.assert_not_called()


def test_missing_vendor_strict_allow_missing_warns(pf_module, tmp_path, capsys):
    """vendor 缺 + strict_vendor=True + allow_missing=True → 跳过 + warn 到 stderr。"""
    target = _make_target(pf_module, tmp_path)

    with mock.patch.object(pf_module, "_download") as dl:
        result = pf_module.preflight(
            [target], out_root=tmp_path, strict_vendor=True, allow_missing=True,
        )

    dl.assert_not_called()
    assert result == []
    captured = capsys.readouterr()
    assert "strict-vendor" in captured.err
    assert "vendor/torch_wheels" in captured.err
