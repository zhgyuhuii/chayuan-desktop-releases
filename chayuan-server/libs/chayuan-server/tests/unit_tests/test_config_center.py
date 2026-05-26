"""配置中心端到端：迁移 + seed + CRUD + 订阅 + 3 store 集成热路径。

用 SQLite 内存库 + 绕过真实 Redis 验证本地订阅 dispatch 可达。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture：准备一个隔离的 tmp CHAYUAN_ROOT + SQLite 内存库 + 重载相关模块
# ---------------------------------------------------------------------------

@pytest.fixture
def _isolated(monkeypatch, tmp_path):
    """把 CHAYUAN_ROOT 指到 tmp_path；SQLite 文件走 tmp；重载 db engine。"""
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    monkeypatch.setenv("CHAYUAN_ROOT_IGNORE_STATE", "1")
    monkeypatch.setenv("CHAYUAN_APPS_YAML", str(tmp_path / "apps.yaml"))
    monkeypatch.setenv(
        "CHAYUAN_CUSTOM_TOOLS_YAML", str(tmp_path / "custom_tools.yaml"),
    )
    monkeypatch.setenv(
        "CHAYUAN_WS_ENDPOINTS_YAML", str(tmp_path / "websocket_endpoints.yaml"),
    )
    # 独立 SQLite 避免污染用户 DB
    db_file = tmp_path / "test.db"
    monkeypatch.setenv(
        "CHAYUAN_TEST_DB_URI",
        f"sqlite:///{db_file}",
    )

    # 重载 chayuan.settings + db.base 指向新 URI
    import importlib
    import chayuan.settings as s
    importlib.reload(s)
    # 强行覆盖 SQLALCHEMY_DATABASE_URI（基础配置走 yaml，此处直接 mock 对象）
    s.Settings.basic_settings.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_file}"

    import chayuan.server.db.base as db_base
    importlib.reload(db_base)

    # 建全部 ORM 表（包含 knowledge_base 等，tools_factory 导入时会查）
    from chayuan.server.knowledge_base.migrate import create_tables
    create_tables()
    # 再建配置中心表（confirm 已在 Base.metadata 里）
    from chayuan.server.config_center.models import ConfigEntry, ConfigHistory
    ConfigEntry.__table__.create(bind=db_base.engine, checkfirst=True)
    ConfigHistory.__table__.create(bind=db_base.engine, checkfirst=True)

    # reset ConfigStore 单例 + 订阅者 local map（避免跨 test 互污染）
    import chayuan.server.config_center.store as cc_store
    import chayuan.server.config_center.subscribe as cc_sub
    cc_store._STORE = None
    cc_sub._LOCAL.clear()

    yield tmp_path


# ---------------------------------------------------------------------------
# 单元：ConfigStore CRUD
# ---------------------------------------------------------------------------

def test_store_set_get_roundtrip(_isolated):
    from chayuan.server.config_center import get_store

    store = get_store()
    v = store.set("testns", "foo", {"a": 1, "b": "二"}, updated_by="tester",
                  comment="first write")
    assert v == 1
    got = store.get("testns", "foo")
    assert got == {"a": 1, "b": "二"}

    # 再写一次 → version=2
    v2 = store.set("testns", "foo", [1, 2, 3])
    assert v2 == 2
    assert store.get("testns", "foo") == [1, 2, 3]

    hist = store.history("testns", "foo")
    assert len(hist) == 2
    assert hist[0]["version"] == 2
    assert hist[0]["value"] == [1, 2, 3]


def test_store_delete(_isolated):
    from chayuan.server.config_center import get_store

    store = get_store()
    store.set("ns1", "k1", "hello")
    assert store.get("ns1", "k1") == "hello"
    assert store.delete("ns1", "k1") is True
    assert store.get("ns1", "k1") is None
    # 删除也会落 history
    hist = store.history("ns1", "k1")
    assert len(hist) >= 1
    assert "DELETE" in hist[0]["comment"]


def test_get_namespace_returns_dict(_isolated):
    from chayuan.server.config_center import get_store

    store = get_store()
    store.set("batch", "a", 1)
    store.set("batch", "b", "x")
    store.set("batch", "c", [1, 2])

    full = store.get_namespace("batch")
    assert full == {"a": 1, "b": "x", "c": [1, 2]}


# ---------------------------------------------------------------------------
# 订阅回调：dispatch_local 可用；写入触发
# ---------------------------------------------------------------------------

def test_local_subscriber_called_on_set(_isolated):
    from chayuan.server.config_center import get_store, register_callback

    got: list = []

    def _cb(evt):
        got.append(evt)

    register_callback("sub_ns", _cb)

    store = get_store()
    store.set("sub_ns", "k", 42)
    assert any(e.get("op") == "set" and e.get("namespace") == "sub_ns" for e in got)

    store.delete("sub_ns", "k")
    assert any(e.get("op") == "delete" and e.get("namespace") == "sub_ns" for e in got)


# ---------------------------------------------------------------------------
# Bootstrap：yaml → DB 种子
# ---------------------------------------------------------------------------

def test_seed_from_yaml(_isolated):
    from chayuan.server.config_center import get_store, seed_from_yaml

    tmp = _isolated
    # 写一个 apps.yaml
    yaml_path = tmp / "apps.yaml"
    yaml_path.write_text(
        "apps:\n"
        "  - app_id: seed1\n"
        "    app_secret: plain\n"
        "    name: seedapp\n"
        "    enabled: true\n"
        "    created_at: '2020'\n"
        "    callback_url: ''\n"
        "    callback_events: []\n"
        "    scopes: [chat:read]\n",
        encoding="utf-8",
    )

    n = seed_from_yaml("apps", yaml_path, top_key="apps")
    assert n == 1

    store = get_store()
    apps = store.get("apps", "apps")
    assert isinstance(apps, list) and apps[0]["app_id"] == "seed1"

    # 再次调用：DB 已有数据，不重复导入
    n2 = seed_from_yaml("apps", yaml_path, top_key="apps")
    assert n2 == 0


# ---------------------------------------------------------------------------
# Store 集成：apps_store 走配置中心，yaml 仅作镜像
# ---------------------------------------------------------------------------

def test_apps_store_uses_config_center(_isolated):
    # 关 secret 加密简化断言
    os.environ.pop("CHAYUAN_MASTER_KEY", None)
    from chayuan.server.shared import secret_store
    secret_store._reset_for_tests()

    from chayuan.server.config_center import get_store
    from chayuan.server.config_panel.apps_store import (
        create_app, list_apps, delete_app,
    )

    spec = create_app("integ", scopes=["chat:read"])
    # 1) DB 侧能读到
    db_val = get_store().get("apps", "apps")
    assert isinstance(db_val, list) and db_val[0]["app_id"] == spec.app_id
    # 2) list_apps 返回一致
    lst = list_apps()
    assert len(lst) == 1 and lst[0].app_id == spec.app_id
    # 3) yaml 镜像也写了
    yaml_path = _isolated / "apps.yaml"
    assert yaml_path.is_file()
    # 4) 删完同步
    assert delete_app(spec.app_id) is True
    assert list_apps() == []
    db_val2 = get_store().get("apps", "apps")
    assert db_val2 == []


def test_hot_update_path_triggers_runtime_reload(_isolated):
    """模拟配置变更 → 订阅回调 → tools_factory 的自定义工具注册表刷新。"""
    # 关加密简化
    os.environ.pop("CHAYUAN_MASTER_KEY", None)
    from chayuan.server.shared import secret_store
    secret_store._reset_for_tests()

    from chayuan.server.config_panel.custom_tools_store import (
        CustomToolSpec, ParamSpec, save_tool,
    )
    from chayuan.server.agent.tools_factory import custom_tools_runtime
    from chayuan.server.agent.tools_factory.tools_registry import (
        _TOOLS_REGISTRY,
    )
    from chayuan.server.config_center import register_callback

    # 注册回调：变更时自动重载
    reload_count = {"n": 0}

    def _cb(_evt):
        reload_count["n"] += 1
        custom_tools_runtime.load_and_register()

    register_callback("custom_tools", _cb)

    # 第一次保存
    save_tool(CustomToolSpec(
        name="hot_tool", description="x", method="GET",
        url="https://e.com", enabled=True,
    ))
    # 订阅回调应被触发（至少 1 次）
    assert reload_count["n"] >= 1
    assert "hot_tool" in _TOOLS_REGISTRY

    # 禁用 → 再保存 → 回调触发 → reload 后从注册表卸载
    save_tool(CustomToolSpec(
        name="hot_tool", description="x", method="GET",
        url="https://e.com", enabled=False,
    ))
    assert reload_count["n"] >= 2
    assert "hot_tool" not in _TOOLS_REGISTRY
