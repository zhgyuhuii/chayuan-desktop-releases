"""P1 迁移：tool_settings.yaml / prompt_settings.yaml 双写 + 反向同步 + seed。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def _isolated(monkeypatch, tmp_path):
    """DB 隔离 + CHAYUAN_ROOT 隔离（沿用 test_config_center 那套）。"""
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    monkeypatch.setenv("CHAYUAN_ROOT_IGNORE_STATE", "1")
    monkeypatch.delenv("CHAYUAN_CONFIG_CENTER_DISABLED", raising=False)

    db_file = tmp_path / "test.db"

    import importlib
    import chayuan.settings as s
    importlib.reload(s)
    s.Settings.basic_settings.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_file}"

    import chayuan.server.db.base as db_base
    importlib.reload(db_base)

    from chayuan.server.knowledge_base.migrate import create_tables
    create_tables()
    from chayuan.server.config_center.models import ConfigEntry, ConfigHistory
    ConfigEntry.__table__.create(bind=db_base.engine, checkfirst=True)
    ConfigHistory.__table__.create(bind=db_base.engine, checkfirst=True)

    import chayuan.server.config_center.store as cc_store
    import chayuan.server.config_center.subscribe as cc_sub
    cc_store._STORE = None
    cc_sub._LOCAL.clear()

    yield tmp_path


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_seed_tool_and_prompt_settings(_isolated):
    tmp = _isolated
    _write_yaml(
        tmp / "tool_settings.yaml",
        "search_local_knowledgebase:\n  use: true\n  top_k: 5\n"
        "arxiv:\n  use: false\n",
    )
    _write_yaml(
        tmp / "prompt_settings.yaml",
        "rag:\n  default: 'template body'\n  empty: ''\n",
    )

    from chayuan.server.config_center import seed_from_yaml, get_store

    n1 = seed_from_yaml("tool_settings", tmp / "tool_settings.yaml")
    n2 = seed_from_yaml("prompt_settings", tmp / "prompt_settings.yaml")
    assert n1 == 2   # 2 个顶层 key
    assert n2 == 1   # 1 个顶层 key

    store = get_store()
    assert store.get("tool_settings", "search_local_knowledgebase") == {
        "use": True, "top_k": 5,
    }
    assert store.get("prompt_settings", "rag") == {
        "default": "template body", "empty": "",
    }


def test_yaml_store_save_also_writes_config_center(_isolated):
    tmp = _isolated
    _write_yaml(
        tmp / "tool_settings.yaml",
        "search_local_knowledgebase:\n  use: false\n  top_k: 3\n",
    )

    from chayuan.server.config_panel.yaml_store import save_updates
    from chayuan.server.config_center import get_store

    # 面板通过 save_updates 改一个路径
    path, bak, changes = save_updates(
        "tool_settings.yaml",
        {"search_local_knowledgebase.top_k": 10},
    )
    assert "search_local_knowledgebase.top_k" in changes

    # 配置中心应收到该顶层 key 的新值
    store = get_store()
    val = store.get("tool_settings", "search_local_knowledgebase")
    assert val is not None
    assert val["top_k"] == 10


def test_db_change_syncs_back_to_yaml(_isolated):
    """模拟：另一个副本写了 DB → 本副本收到回调 → 覆写本地 yaml。"""
    tmp = _isolated
    yaml_path = tmp / "prompt_settings.yaml"
    _write_yaml(yaml_path, "rag:\n  default: old\n")

    from chayuan.server.config_center import (
        get_store, register_callback, make_yaml_sync_callback,
    )

    # 注册反向同步回调（模仿 server_app.startup 的行为）
    register_callback("prompt_settings",
                      make_yaml_sync_callback("prompt_settings", yaml_path))

    # 直接写 DB（模拟另一副本的操作）
    store = get_store()
    store.set("prompt_settings", "rag", {"default": "new"})

    # 本地 yaml 应该已经被覆写
    text = yaml_path.read_text(encoding="utf-8")
    assert "new" in text
    assert "old" not in text


def test_config_center_disabled_skips_mirror(_isolated, monkeypatch):
    """Kill switch 打开时，yaml 写入不应 mirror 到 DB。"""
    monkeypatch.setenv("CHAYUAN_CONFIG_CENTER_DISABLED", "1")

    tmp = _isolated
    _write_yaml(
        tmp / "tool_settings.yaml",
        "arxiv:\n  use: false\n",
    )

    from chayuan.server.config_panel.yaml_store import save_updates
    from chayuan.server.config_center import get_store

    save_updates("tool_settings.yaml", {"arxiv.use": True})

    # DB 应该什么都没有（CHAYUAN_CONFIG_CENTER_DISABLED 跳过了）
    assert get_store().get("tool_settings", "arxiv") is None
