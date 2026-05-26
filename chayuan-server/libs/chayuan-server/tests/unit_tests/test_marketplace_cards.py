"""marketplace.query_cards 单元测试.

策略:
* 把 REGISTRY_FILE / LocalModelIndex 都打到 monkeypatch 上,直接 in-memory
  喂数据
* 验证: 合并 / 过滤 / 分页 / 排序 / lifecycle 进度合并
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chayuan.server.model_registry import marketplace as mp
from chayuan.server.model_registry.local_index import LocalModelEntry


def _entry(model_id: str, capability: str = "chat", size: int = 1024) -> LocalModelEntry:
    return LocalModelEntry(
        model_id=model_id,
        path=f"/tmp/{model_id.replace('/', '__')}",
        relpath=model_id.replace("/", "__"),
        capability=capability,
        size_bytes=size,
        source_tag="huggingface",
    )


@pytest.fixture
def fake_local_index(monkeypatch):
    class _Idx:
        def __init__(self, entries):
            self._entries = entries
        def list_entries(self):
            return list(self._entries)

    holder = {"idx": _Idx([])}
    monkeypatch.setattr(mp, "get_local_index", lambda: holder["idx"])
    return holder


@pytest.fixture
def fake_catalog(monkeypatch, tmp_path: Path):
    """把 REGISTRY_FILE 替换成 tmp 文件,允许测试自由写入。"""
    fp = tmp_path / "registry.json"
    monkeypatch.setattr(mp, "REGISTRY_FILE", fp)

    def _write(items):
        fp.write_text(json.dumps({"items": items}), encoding="utf-8")

    return _write


# ---------- 基础: 远端 catalog 渲染 ----------

def test_query_cards_from_remote_catalog_only(fake_local_index, fake_catalog):
    fake_catalog([
        {"model_id": "Qwen/Qwen2.5-7B", "capability": "chat", "size": 14_000_000_000},
        {"model_id": "BAAI/bge-m3", "capability": "embedding", "size": 1_000_000_000},
    ])
    page = mp.query_cards(page=1, page_size=10)
    assert page.total == 2
    statuses = {c.status for c in page.items}
    assert statuses == {"available"}
    # capability 归一化: embedding → text-embedding
    caps = {c.capability for c in page.items}
    assert "text-embedding" in caps


# ---------- 本地覆盖远端 ----------

def test_local_entry_overrides_remote_status(fake_local_index, fake_catalog):
    fake_catalog([
        {"model_id": "Qwen/Qwen2.5-7B", "capability": "chat", "size": 100},
    ])
    fake_local_index["idx"]._entries = [_entry("Qwen/Qwen2.5-7B", "chat", size=14_000_000_000)]
    page = mp.query_cards()
    assert page.total == 1
    card = page.items[0]
    assert card.status == "ready"
    # 本地实测体积应覆盖估算
    assert card.size_bytes == 14_000_000_000


def test_local_only_entry_appears(fake_local_index, fake_catalog):
    fake_catalog([])
    fake_local_index["idx"]._entries = [_entry("MyOrg/CustomModel", "chat")]
    page = mp.query_cards()
    assert page.total == 1
    assert page.items[0].status == "ready"
    assert page.items[0].vendor == "MyOrg"


# ---------- 过滤 ----------

def test_capability_filter(fake_local_index, fake_catalog):
    fake_catalog([
        {"model_id": "a/x", "capability": "chat"},
        {"model_id": "b/y", "capability": "embedding"},
        {"model_id": "c/z", "capability": "rerank"},
    ])
    page = mp.query_cards(capability="chat")
    assert {c.id for c in page.items} == {"a/x"}


def test_capability_filter_with_alias(fake_local_index, fake_catalog):
    """传 'text-embedding' 应该和 catalog 'embedding' 都命中。"""
    fake_catalog([
        {"model_id": "b/y", "capability": "embedding"},
    ])
    page = mp.query_cards(capability="text-embedding")
    assert page.total == 1


def test_vendor_filter(fake_local_index, fake_catalog):
    fake_catalog([
        {"model_id": "Qwen/A", "capability": "chat"},
        {"model_id": "Meta/B", "capability": "chat"},
    ])
    page = mp.query_cards(vendor="Qwen")
    assert {c.id for c in page.items} == {"Qwen/A"}


def test_q_keyword_search(fake_local_index, fake_catalog):
    fake_catalog([
        {"model_id": "Qwen/Qwen2.5-Coder-7B", "capability": "chat", "tags": ["coder"]},
        {"model_id": "Meta/Llama-3", "capability": "chat", "tags": []},
    ])
    page = mp.query_cards(q="coder")
    assert {c.id for c in page.items} == {"Qwen/Qwen2.5-Coder-7B"}


# ---------- 排序 + 分页 ----------

def test_pagination_and_total(fake_local_index, fake_catalog):
    fake_catalog([
        {"model_id": f"v/m{i}", "capability": "chat"}
        for i in range(50)
    ])
    page1 = mp.query_cards(page=1, page_size=20)
    assert page1.total == 50
    assert len(page1.items) == 20
    page3 = mp.query_cards(page=3, page_size=20)
    assert len(page3.items) == 10


def test_sort_ready_before_available(fake_local_index, fake_catalog):
    fake_catalog([
        {"model_id": "v/avail-large", "capability": "chat", "size": 999},
        {"model_id": "v/ready-small", "capability": "chat", "size": 100},
    ])
    fake_local_index["idx"]._entries = [_entry("v/ready-small", "chat", size=100)]
    page = mp.query_cards()
    # ready 应排在 available 前
    assert page.items[0].id == "v/ready-small"
    assert page.items[0].status == "ready"


# ---------- to_dict 序列化 ----------

def test_card_to_dict_shape(fake_local_index, fake_catalog):
    fake_catalog([
        {"model_id": "v/m", "capability": "chat", "size": 100, "tags": ["a", "b"]}
    ])
    page = mp.query_cards()
    d = page.items[0].to_dict()
    assert set(d.keys()) >= {
        "id", "vendor", "capability", "name", "status",
        "size_bytes", "supported_runtimes", "tags",
    }
    assert d["supported_runtimes"]  # chat 应有 ollama / vllm / llamacpp
