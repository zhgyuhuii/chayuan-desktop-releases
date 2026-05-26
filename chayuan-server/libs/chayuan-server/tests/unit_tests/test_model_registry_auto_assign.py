"""``auto_assign.promote_defaults_from_local`` 行为测试。

测试 patch :mod:`config_panel.runtime_framework_panel` 的 load/save 函数,
避免触碰真实 yaml。本地候选用注入的 ``LocalModelIndex`` 实例。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from chayuan.server.model_registry.auto_assign import promote_defaults_from_local
from chayuan.server.model_registry.local_index import (
    LocalModelEntry,
    LocalModelIndex,
)


def _idx(entries: list[LocalModelEntry]) -> LocalModelIndex:
    td = Path(tempfile.mkdtemp(prefix="chayuan-autoassign-test-"))
    p = td / "local_models.json"
    p.write_text(json.dumps({
        "version": 1, "items": [e.to_dict() for e in entries],
    }), encoding="utf-8")
    return LocalModelIndex(p)


def _entry(model_id: str, capability: str) -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id, path=f"/tmp/fake/{model_id}",
        relpath=model_id, capability=capability, format="gguf",
        family=capability.replace("-", "_"), size_bytes=1024,
    )


# ─────────────────────────── 主流程 ───────────────────────────


def test_promotes_first_local_when_no_default():
    idx = _idx([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
        _entry("bge-rerank", "rerank"),
    ])
    saves: dict[str, str] = {}

    def fake_save(cap, mid):
        saves[cap] = mid
        return True, "ok"

    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._load_capability_defaults",
        return_value={},
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._save_capability_default",
        side_effect=fake_save,
    ):
        promoted = promote_defaults_from_local()

    # 三类都应该被自动指派
    assert saves == {
        "chat": "qwen3-4b",
        "embedding": "bge-m3",
        "rerank": "bge-rerank",
    }
    assert promoted == saves


def test_does_not_overwrite_existing_default_by_default():
    idx = _idx([_entry("qwen3-4b", "chat"), _entry("bge-m3", "text-embedding")])
    saves: dict[str, str] = {}

    def fake_save(cap, mid):
        saves[cap] = mid
        return True, "ok"

    # chat 已经有 default,不该被覆盖;embedding 没有,会被指派
    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._load_capability_defaults",
        return_value={"chat": "user-pick-cloud"},
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._save_capability_default",
        side_effect=fake_save,
    ):
        promoted = promote_defaults_from_local()

    assert "chat" not in saves
    assert saves.get("embedding") == "bge-m3"
    assert promoted == {"embedding": "bge-m3"}


def test_overwrite_flag_forces_local_pick():
    idx = _idx([_entry("qwen3-4b", "chat")])
    saves: dict[str, str] = {}

    def fake_save(cap, mid):
        saves[cap] = mid
        return True, "ok"

    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._load_capability_defaults",
        return_value={"chat": "user-pick-cloud"},
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._save_capability_default",
        side_effect=fake_save,
    ):
        promote_defaults_from_local(overwrite=True)
    assert saves.get("chat") == "qwen3-4b"


def test_no_local_candidates_no_change():
    idx = _idx([])
    saves: dict[str, str] = {}

    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._load_capability_defaults",
        return_value={},
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._save_capability_default",
        side_effect=lambda c, m: (saves.update({c: m}) or (True, "ok")),
    ):
        promoted = promote_defaults_from_local()
    assert promoted == {}
    assert saves == {}


def test_save_failure_does_not_block_other_caps():
    """某 cap save 失败时,其它 cap 应该继续尝试。"""
    idx = _idx([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
    ])

    def flaky_save(cap, mid):
        if cap == "chat":
            return False, "yaml lock"
        return True, "ok"

    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._load_capability_defaults",
        return_value={},
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._save_capability_default",
        side_effect=flaky_save,
    ):
        promoted = promote_defaults_from_local()
    # chat 没成功,embedding 应该成功
    assert "chat" not in promoted
    assert promoted.get("embedding") == "bge-m3"


def test_save_raising_exception_does_not_block():
    idx = _idx([_entry("qwen3-4b", "chat"), _entry("bge-m3", "text-embedding")])

    def raising_save(cap, mid):
        if cap == "chat":
            raise RuntimeError("yaml corrupt")
        return True, "ok"

    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._load_capability_defaults",
        return_value={},
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._save_capability_default",
        side_effect=raising_save,
    ):
        promoted = promote_defaults_from_local()
    assert promoted.get("embedding") == "bge-m3"
    assert "chat" not in promoted


def test_capabilities_filter_restricts_scope():
    """传 capabilities= 限定范围时,只处理指定 panel cap。"""
    idx = _idx([
        _entry("qwen3-4b", "chat"),
        _entry("bge-m3", "text-embedding"),
    ])
    saves: dict[str, str] = {}

    with mock.patch(
        "chayuan.server.model_registry.local_index.get_local_index",
        return_value=idx,
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._load_capability_defaults",
        return_value={},
    ), mock.patch(
        "chayuan.server.config_panel.runtime_framework_panel._save_capability_default",
        side_effect=lambda c, m: (saves.update({c: m}) or (True, "ok")),
    ):
        promoted = promote_defaults_from_local(capabilities=["chat"])
    assert "embedding" not in saves
    assert saves.get("chat") == "qwen3-4b"
    assert "embedding" not in promoted


def test_config_panel_unavailable_returns_empty():
    """config_panel import 失败时不应抛,返回空 dict。"""
    with mock.patch.dict(
        __import__("sys").modules,
        {"chayuan.server.config_panel.runtime_framework_panel": None},
    ):
        promoted = promote_defaults_from_local()
    assert promoted == {}
