"""``local_index.bundled_models_dir`` 解析优先级测试。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def reset_singleton(monkeypatch):
    import chayuan.server.model_registry.local_index as li
    monkeypatch.setattr(li, "_SINGLETON", None)
    yield


def _mk_bundled_layout(root: Path) -> Path:
    """在 root 下建一个最小 bundled_models 目录,方便测试 is_dir 判定。"""
    for sub in ("chat", "embedding", "rerank", "asr", "ocr", "image", "custom"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def test_env_override_wins(tmp_path, monkeypatch, reset_singleton):
    bundled = _mk_bundled_layout(tmp_path / "custom_bundled")
    monkeypatch.setenv("CHAYUAN_BUNDLED_MODELS_DIR", str(bundled))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    from chayuan.server.model_registry.local_index import bundled_models_dir
    assert bundled_models_dir() == bundled


def test_env_pointing_to_missing_dir_falls_through(tmp_path, monkeypatch, reset_singleton):
    """env var 指向不存在的目录时,应继续回退到下一档,而不是返回 ``None``。"""
    monkeypatch.setenv("CHAYUAN_BUNDLED_MODELS_DIR", str(tmp_path / "nope"))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    from chayuan.server.model_registry.local_index import bundled_models_dir
    # 仓库内 vendor/bundled_models 真实存在(本仓库已建好骨架),应被找到
    got = bundled_models_dir()
    assert got is not None
    assert got.is_dir()
    assert got.name == "bundled_models"


def test_meipass_takes_precedence_over_repo(tmp_path, monkeypatch, reset_singleton):
    monkeypatch.delenv("CHAYUAN_BUNDLED_MODELS_DIR", raising=False)
    bundled = _mk_bundled_layout(tmp_path / "meipass" / "bundled_models")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)

    from chayuan.server.model_registry.local_index import bundled_models_dir
    assert bundled_models_dir() == bundled


def test_repo_dev_path_resolved(monkeypatch, reset_singleton):
    """在本仓库工作树里跑测试,没有 env / _MEIPASS 时应解析到 <repo>/vendor/bundled_models。"""
    monkeypatch.delenv("CHAYUAN_BUNDLED_MODELS_DIR", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    from chayuan.server.model_registry.local_index import (
        BUNDLED_CAPABILITY_DIRS,
        bundled_models_dir,
    )
    got = bundled_models_dir()
    assert got is not None and got.is_dir(), f"未解析到仓库 bundled_models,got={got}"
    # 7 个 capability 子目录都应在
    for cap in BUNDLED_CAPABILITY_DIRS:
        assert (got / cap).is_dir(), f"缺失 capability 子目录: {cap}"


def test_returns_none_when_all_paths_absent(tmp_path, monkeypatch, reset_singleton):
    """env / _MEIPASS / repo / argv0 都没有时,返回 None。"""
    monkeypatch.delenv("CHAYUAN_BUNDLED_MODELS_DIR", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    # 用 monkeypatch 替换 __file__ 让 parents[5] 落到一个不存在的位置
    fake = tmp_path / "fake" / "libs" / "chayuan-server" / "chayuan" / "server" / "model_registry"
    fake.mkdir(parents=True, exist_ok=True)
    fake_file = fake / "local_index.py"
    fake_file.touch()
    import chayuan.server.model_registry.local_index as li
    monkeypatch.setattr(li, "__file__", str(fake_file))

    monkeypatch.setattr(sys, "argv", [str(tmp_path / "fake" / "exe-doesnt-exist")])
    assert li.bundled_models_dir() is None
