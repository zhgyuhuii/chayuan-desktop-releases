"""``runtime.auto_start_store`` bootstrap 标记 API 单元测试。

is_bootstrapped / mark_bootstrapped 覆盖三件事:
  * 默认状态(未 mark)返回 False;
  * mark 一次后返回 True,且持久化到 sidecar_settings.json;
  * 与 auto_start dict 共存,不互相覆盖。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chayuan.server.runtime import auto_start_store


@pytest.fixture
def store_tmp(tmp_path, monkeypatch):
    """把 _settings_path 改到 tmp,避免污染真实 ``<CHAYUAN_ROOT>/data``。"""
    target = tmp_path / "sidecar_settings.json"
    monkeypatch.setattr(auto_start_store, "_settings_path", lambda: target)
    return target


def test_is_bootstrapped_default_false(store_tmp):
    assert auto_start_store.is_bootstrapped("embedding") is False
    assert auto_start_store.is_bootstrapped("nope") is False


def test_mark_then_is_bootstrapped_true(store_tmp: Path):
    auto_start_store.mark_bootstrapped("embedding")
    assert auto_start_store.is_bootstrapped("embedding") is True
    # 别的 cap 仍然 False
    assert auto_start_store.is_bootstrapped("rerank") is False


def test_mark_persists_to_disk(store_tmp: Path):
    auto_start_store.mark_bootstrapped("embedding")
    auto_start_store.mark_bootstrapped("ocr")
    data = json.loads(store_tmp.read_text(encoding="utf-8"))
    assert sorted(data["bootstrapped_caps"]) == ["embedding", "ocr"]


def test_mark_is_idempotent(store_tmp: Path):
    auto_start_store.mark_bootstrapped("embedding")
    auto_start_store.mark_bootstrapped("embedding")
    auto_start_store.mark_bootstrapped("embedding")
    data = json.loads(store_tmp.read_text(encoding="utf-8"))
    assert data["bootstrapped_caps"] == ["embedding"]


def test_bootstrap_coexists_with_auto_start(store_tmp: Path):
    auto_start_store.set_("embedding", True)
    auto_start_store.mark_bootstrapped("embedding")
    data = json.loads(store_tmp.read_text(encoding="utf-8"))
    assert data["auto_start"]["embedding"] is True
    assert data["bootstrapped_caps"] == ["embedding"]
    # 翻 auto_start False 不应清掉 bootstrap 标记 — 关键不变量,
    # 用户关掉服务不等于"想再重新装机一次"
    auto_start_store.set_("embedding", False)
    assert auto_start_store.is_bootstrapped("embedding") is True
