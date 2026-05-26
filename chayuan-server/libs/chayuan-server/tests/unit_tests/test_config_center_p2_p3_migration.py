"""P2 / P3 迁移：kb_settings / model_settings 双写 + 反向同步 + 旁路 mirror。

P2: kb_settings.yaml（vs_config.py 走 save_updates）
P3: model_settings.yaml（model_config.py 旁路 _atomic_write → 显式
    mirror_namespace_to_db）
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def _isolated(monkeypatch, tmp_path):
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


# ---------------------------------------------------------------------------
# P2: kb_settings
# ---------------------------------------------------------------------------

def test_p2_seed_and_save_updates(_isolated):
    tmp = _isolated
    _write_yaml(
        tmp / "kb_settings.yaml",
        "DEFAULT_VS_TYPE: faiss\n"
        "SEARCH_ENGINE_TOP_K: 5\n"
        "kbs_config:\n"
        "  faiss: {}\n"
        "  milvus:\n"
        "    host: 127.0.0.1\n"
        "    port: 19530\n",
    )

    from chayuan.server.config_center import seed_from_yaml, get_store
    from chayuan.server.config_panel.yaml_store import save_updates

    # seed 一次
    n = seed_from_yaml("kb_settings", tmp / "kb_settings.yaml")
    assert n == 3

    store = get_store()
    assert store.get("kb_settings", "DEFAULT_VS_TYPE") == "faiss"
    assert store.get("kb_settings", "SEARCH_ENGINE_TOP_K") == 5

    # save_updates 改路径 → 自动 mirror
    path, bak, changes = save_updates(
        "kb_settings.yaml",
        {"DEFAULT_VS_TYPE": "milvus"},
    )
    assert "DEFAULT_VS_TYPE" in changes
    assert store.get("kb_settings", "DEFAULT_VS_TYPE") == "milvus"


def test_p2_nested_dict_save(_isolated):
    """保存 ``kbs_config.milvus.host`` 这种嵌套路径，顶层 kbs_config 整体 mirror。"""
    tmp = _isolated
    _write_yaml(
        tmp / "kb_settings.yaml",
        "kbs_config:\n"
        "  milvus:\n"
        "    host: 127.0.0.1\n"
        "    port: 19530\n",
    )

    from chayuan.server.config_center import get_store
    from chayuan.server.config_panel.yaml_store import save_updates

    save_updates("kb_settings.yaml", {"kbs_config.milvus.host": "10.0.0.1"})

    cfg = get_store().get("kb_settings", "kbs_config")
    assert cfg["milvus"]["host"] == "10.0.0.1"
    assert cfg["milvus"]["port"] == 19530


# ---------------------------------------------------------------------------
# P3: model_settings
# ---------------------------------------------------------------------------

def test_p3_seed_model_settings(_isolated):
    tmp = _isolated
    _write_yaml(
        tmp / "model_settings.yaml",
        "DEFAULT_LLM_MODEL: qwen-plus\n"
        "DEFAULT_EMBEDDING_MODEL: bge-m3\n"
        "MODEL_PLATFORMS:\n"
        "  - platform_name: xinference\n"
        "    api_base_url: http://127.0.0.1:9997/v1\n"
        "    llm_models: [qwen-plus]\n"
        "    embed_models: [bge-m3]\n",
    )

    from chayuan.server.config_center import seed_from_yaml, get_store

    n = seed_from_yaml("model_settings", tmp / "model_settings.yaml")
    assert n == 3

    store = get_store()
    assert store.get("model_settings", "DEFAULT_LLM_MODEL") == "qwen-plus"
    platforms = store.get("model_settings", "MODEL_PLATFORMS")
    assert isinstance(platforms, list) and platforms[0]["platform_name"] == "xinference"


def test_p3_mirror_namespace_to_db_exposed(_isolated):
    """model_config.py 走的旁路：直接用 ``_atomic_write`` 写文件后，显式调
    ``mirror_namespace_to_db`` 把 doc 全量同步到 DB。"""
    tmp = _isolated
    _write_yaml(tmp / "model_settings.yaml", "DEFAULT_LLM_MODEL: old\n")

    from chayuan.server.config_center import get_store
    from chayuan.server.config_panel import yaml_store

    # 模拟 model_config 旁路：手动构造新 doc，写文件，再调 mirror
    load = yaml_store.load_yaml("model_settings.yaml")
    doc = load.doc
    doc["DEFAULT_LLM_MODEL"] = "new-llm"
    doc["MODEL_PLATFORMS"] = [{"platform_name": "p1"}]

    from chayuan.pydantic_settings_file import import_yaml
    yaml_store._atomic_write(load.path, lambda f: import_yaml().dump(doc, f))  # noqa: SLF001
    yaml_store.mirror_namespace_to_db("model_settings.yaml", doc)

    store = get_store()
    assert store.get("model_settings", "DEFAULT_LLM_MODEL") == "new-llm"
    assert store.get("model_settings", "MODEL_PLATFORMS") == [{"platform_name": "p1"}]


def test_p2_p3_reverse_sync_to_yaml(_isolated):
    """另一副本写 DB → 本副本收到回调 → 覆写本地 yaml。"""
    tmp = _isolated
    kb_yaml = tmp / "kb_settings.yaml"
    model_yaml = tmp / "model_settings.yaml"
    _write_yaml(kb_yaml, "DEFAULT_VS_TYPE: faiss\n")
    _write_yaml(model_yaml, "DEFAULT_LLM_MODEL: old\n")

    from chayuan.server.config_center import (
        get_store, register_callback, make_yaml_sync_callback,
    )

    register_callback("kb_settings",
                      make_yaml_sync_callback("kb_settings", kb_yaml))
    register_callback("model_settings",
                      make_yaml_sync_callback("model_settings", model_yaml))

    store = get_store()
    store.set("kb_settings", "DEFAULT_VS_TYPE", "milvus")
    store.set("model_settings", "DEFAULT_LLM_MODEL", "new-llm")

    assert "milvus" in kb_yaml.read_text(encoding="utf-8")
    assert "new-llm" in model_yaml.read_text(encoding="utf-8")
