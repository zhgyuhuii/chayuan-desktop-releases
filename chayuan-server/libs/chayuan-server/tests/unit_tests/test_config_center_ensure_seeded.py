"""ensure_seeded(51 题)单元测试。

策略:
  * 用临时 SQLite 内存库
  * 临时 yaml + ConfigEntry 表
  * 验证:
    - 全空 namespace 整体 seed
    - 已有 keys 不重复
    - yaml 新增 key 自动补 seed
    - yaml 不存在 / 解析失败时不崩
    - top_key 模式
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml as _yaml


@pytest.fixture
def tmp_db(monkeypatch):
    """临时内存 SQLite + ConfigEntry 表。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from chayuan.server.config_center.models import Base, ConfigEntry

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr(
        "chayuan.server.db.base.SessionLocal", Session,
    )
    return Session


@pytest.fixture
def tmp_yaml(tmp_path):
    def _writer(name: str, content: dict) -> Path:
        p = tmp_path / name
        p.write_text(_yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")
        return p
    return _writer


def _count_in_db(Session, namespace: str) -> int:
    from chayuan.server.config_center.models import ConfigEntry
    with Session() as s:
        return s.query(ConfigEntry).filter_by(namespace=namespace).count()


# ============================================================================
# tests
# ============================================================================


def test_ensure_seeded_inserts_when_empty(tmp_db, tmp_yaml):
    """DB 完全为空时,把 yaml 顶层 keys 全部 seed。"""
    from chayuan.server.config_center import ensure_seeded

    yaml_path = tmp_yaml("test1.yaml", {
        "key_a": "value_a",
        "key_b": [1, 2, 3],
        "key_c": {"nested": "ok"},
    })
    rpt = ensure_seeded("ns_test1", yaml_path)
    assert rpt["seeded"] == 3
    assert rpt["matched"] == 0
    assert rpt["total"] == 3
    assert rpt["skipped"] == 0
    assert _count_in_db(tmp_db, "ns_test1") == 3


def test_ensure_seeded_skips_already_existing(tmp_db, tmp_yaml):
    """已经在 DB 的 key,不重复 insert。"""
    from chayuan.server.config_center import ensure_seeded

    yaml_path = tmp_yaml("test2.yaml", {"k1": "v1", "k2": "v2"})
    # 第 1 次 seed
    ensure_seeded("ns_test2", yaml_path)
    assert _count_in_db(tmp_db, "ns_test2") == 2
    # 第 2 次:全 matched,seeded=0
    rpt = ensure_seeded("ns_test2", yaml_path)
    assert rpt["seeded"] == 0
    assert rpt["matched"] == 2
    assert _count_in_db(tmp_db, "ns_test2") == 2


def test_ensure_seeded_delta_adds_only_missing(tmp_db, tmp_yaml):
    """DB 已有部分 keys,yaml 新增了 keys → 只补缺的。"""
    from chayuan.server.config_center import ensure_seeded

    # 第 1 次:yaml 只有 k1
    yaml_v1 = tmp_yaml("test3.yaml", {"k1": "v1"})
    ensure_seeded("ns_test3", yaml_v1)
    assert _count_in_db(tmp_db, "ns_test3") == 1

    # 第 2 次:yaml 升级,新增 k2 / k3
    yaml_v2 = tmp_yaml("test3.yaml", {"k1": "v1-changed", "k2": "v2", "k3": "v3"})
    rpt = ensure_seeded("ns_test3", yaml_v2)
    # k1 已在 DB(matched),k2 / k3 新增(seeded=2)
    assert rpt["seeded"] == 2
    assert rpt["matched"] == 1
    assert rpt["total"] == 3
    assert _count_in_db(tmp_db, "ns_test3") == 3

    # k1 的值不被 yaml 新值覆盖(尊重 DB 用户改过的值)
    from chayuan.server.config_center.models import ConfigEntry
    with tmp_db() as s:
        k1 = s.query(ConfigEntry).filter_by(namespace="ns_test3", key="k1").first()
        assert json.loads(k1.value) == "v1", "k1 不应被 yaml 新值覆盖"


def test_ensure_seeded_yaml_missing(tmp_db, tmp_path):
    """yaml 文件不存在 → skipped=1, 不抛错。"""
    from chayuan.server.config_center import ensure_seeded

    rpt = ensure_seeded("ns_nope", tmp_path / "nonexistent.yaml")
    assert rpt["skipped"] == 1
    assert rpt["seeded"] == 0
    assert _count_in_db(tmp_db, "ns_nope") == 0


def test_ensure_seeded_yaml_parse_failure(tmp_db, tmp_path):
    """坏 yaml → skipped=1, 不抛错。"""
    from chayuan.server.config_center import ensure_seeded

    bad = tmp_path / "bad.yaml"
    bad.write_text("[: invalid yaml :", encoding="utf-8")
    rpt = ensure_seeded("ns_bad", bad)
    assert rpt["skipped"] == 1
    assert _count_in_db(tmp_db, "ns_bad") == 0


def test_ensure_seeded_with_top_key(tmp_db, tmp_yaml):
    """top_key='apps' 模式:只取 yaml 里 apps 字段当 single key=apps 存。"""
    from chayuan.server.config_center import ensure_seeded
    from chayuan.server.config_center.models import ConfigEntry

    yaml_path = tmp_yaml("apps.yaml", {
        "apps": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
        "ignore_me": "should not be saved",
    })
    rpt = ensure_seeded("ns_apps", yaml_path, top_key="apps")
    assert rpt["seeded"] == 1   # 只 1 个 key("apps")
    assert _count_in_db(tmp_db, "ns_apps") == 1

    with tmp_db() as s:
        row = s.query(ConfigEntry).filter_by(namespace="ns_apps").first()
        assert row.key == "apps"
        # value 应是 list
        assert json.loads(row.value) == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]


def test_ensure_seeded_top_key_missing(tmp_db, tmp_yaml):
    """yaml 里没有指定的 top_key → skipped=1。"""
    from chayuan.server.config_center import ensure_seeded

    yaml_path = tmp_yaml("ns.yaml", {"other_key": "x"})
    rpt = ensure_seeded("ns_x", yaml_path, top_key="apps")
    assert rpt["skipped"] == 1
