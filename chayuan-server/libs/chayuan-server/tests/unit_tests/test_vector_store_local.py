"""``vector_store_local.SqliteVecStore`` 单元测试(Phase 4)。

测试覆盖:
    * collection 生命周期(ensure / list / drop / count)
    * upsert(创建 + 覆盖 + 维度校验 + 自动建 collection)
    * delete(by id + IN 批量 + 不存在的 id 不报错)
    * search(KNN top-k + 距离顺序 + 空 collection)
    * search filter(等值 + IN + 多键 AND)
    * SQL 注入防护(非法 collection 名)
    * close 幂等

需要 sqlite-vec 0.1.x;若环境缺扩展能力(libsqlite 编译时禁用 load_extension)
整组测试 ``skip``。
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from chayuan.server.retrieval.vector_store_local import (
    CollectionSpec,
    SqliteVecStore,
    VectorEntry,
    _payload_matches,
)


@pytest.fixture(scope="module")
def _sqlite_vec_available() -> bool:
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        return False
    import sqlite3

    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
    except Exception:
        return False
    finally:
        conn.close()
    return True


@pytest.fixture
def store(tmp_path: Path, _sqlite_vec_available: bool) -> SqliteVecStore:
    if not _sqlite_vec_available:
        pytest.skip("sqlite_vec / load_extension 不可用")
    s = SqliteVecStore.open(tmp_path)
    yield s
    s.close()


def _vec(*xs: float) -> list[float]:
    return list(xs)


# ──────────────────────────────────────────────────────────────────────
# collection lifecycle
# ──────────────────────────────────────────────────────────────────────


def test_ensure_collection_creates_and_is_idempotent(store: SqliteVecStore) -> None:
    store.ensure_collection(CollectionSpec("kb_a", dim=4))
    store.ensure_collection(CollectionSpec("kb_a", dim=4))  # 二次为 no-op
    assert store.list_collections() == ["kb_a"]
    assert store.count("kb_a") == 0


def test_ensure_collection_dim_mismatch_raises(store: SqliteVecStore) -> None:
    store.ensure_collection(CollectionSpec("kb_a", dim=4))
    with pytest.raises(ValueError, match="维度不匹配"):
        store.ensure_collection(CollectionSpec("kb_a", dim=8))


def test_ensure_collection_invalid_name_raises(store: SqliteVecStore) -> None:
    with pytest.raises(ValueError, match="invalid collection name"):
        store.ensure_collection(CollectionSpec("kb a", dim=4))  # 空格
    with pytest.raises(ValueError, match="invalid collection name"):
        store.ensure_collection(CollectionSpec("9kb", dim=4))   # 数字开头
    with pytest.raises(ValueError, match="invalid collection name"):
        store.ensure_collection(CollectionSpec("kb;DROP TABLE", dim=4))  # SQL 注入尝试


def test_ensure_collection_unsupported_metric_raises(store: SqliteVecStore) -> None:
    with pytest.raises(ValueError, match="unsupported metric"):
        store.ensure_collection(CollectionSpec("kb", dim=4, metric="hamming"))


def test_drop_collection_removes_data(store: SqliteVecStore) -> None:
    store.ensure_collection(CollectionSpec("kb_a", dim=2))
    store.upsert("kb_a", [VectorEntry("c1", _vec(1.0, 0.0))])
    assert store.count("kb_a") == 1
    store.drop_collection("kb_a")
    assert store.list_collections() == []
    store.drop_collection("kb_a")  # 二次为 no-op


# ──────────────────────────────────────────────────────────────────────
# upsert
# ──────────────────────────────────────────────────────────────────────


def test_upsert_auto_creates_collection_with_inferred_dim(store: SqliteVecStore) -> None:
    n = store.upsert(
        "kb_auto",
        [VectorEntry("c1", _vec(0.1, 0.2, 0.3, 0.4))],
    )
    assert n == 1
    assert "kb_auto" in store.list_collections()
    assert store.count("kb_auto") == 1


def test_upsert_overwrites_same_id(store: SqliteVecStore) -> None:
    store.upsert("kb", [VectorEntry("c1", _vec(1.0, 0.0), text="v1")])
    store.upsert("kb", [VectorEntry("c1", _vec(0.0, 1.0), text="v2")])
    # 仍只有一条;且 search 拿到的 text 是 v2
    assert store.count("kb") == 1
    hits = store.search("kb", _vec(0.0, 1.0), k=1)
    assert hits and hits[0].id == "c1"
    assert hits[0].text == "v2"


def test_upsert_dim_mismatch_raises(store: SqliteVecStore) -> None:
    store.ensure_collection(CollectionSpec("kb", dim=4))
    with pytest.raises(ValueError, match="dim mismatch"):
        store.upsert("kb", [VectorEntry("c1", _vec(1.0, 2.0))])  # 应是 4 维


def test_upsert_empty_iterable_returns_zero(store: SqliteVecStore) -> None:
    assert store.upsert("kb", []) == 0


# ──────────────────────────────────────────────────────────────────────
# delete
# ──────────────────────────────────────────────────────────────────────


def test_delete_by_ids(store: SqliteVecStore) -> None:
    store.upsert(
        "kb",
        [
            VectorEntry("c1", _vec(1.0, 0.0)),
            VectorEntry("c2", _vec(0.0, 1.0)),
            VectorEntry("c3", _vec(1.0, 1.0)),
        ],
    )
    removed = store.delete("kb", ["c1", "c3"])
    assert removed == 2
    assert store.count("kb") == 1


def test_delete_unknown_collection_raises(store: SqliteVecStore) -> None:
    with pytest.raises(KeyError):
        store.delete("nope", ["x"])


def test_delete_unknown_id_returns_zero(store: SqliteVecStore) -> None:
    store.ensure_collection(CollectionSpec("kb", dim=2))
    assert store.delete("kb", ["nope"]) == 0


# ──────────────────────────────────────────────────────────────────────
# search
# ──────────────────────────────────────────────────────────────────────


def test_search_returns_topk_in_distance_order(store: SqliteVecStore) -> None:
    store.upsert(
        "kb",
        [
            VectorEntry("near", _vec(1.0, 0.0)),
            VectorEntry("mid", _vec(0.7, 0.7)),
            VectorEntry("far", _vec(0.0, 1.0)),
        ],
    )
    hits = store.search("kb", _vec(1.0, 0.0), k=3)
    assert [h.id for h in hits] == ["near", "mid", "far"]
    # distance 升序 + score 降序
    distances = [h.distance for h in hits]
    assert distances == sorted(distances)
    assert hits[0].score >= hits[1].score >= hits[2].score
    # cosine: near 与自己 distance ≈ 0
    assert hits[0].distance < 1e-5
    assert math.isclose(hits[0].score, 1.0, rel_tol=1e-3)


def test_search_empty_collection_returns_empty(store: SqliteVecStore) -> None:
    store.ensure_collection(CollectionSpec("kb", dim=2))
    assert store.search("kb", _vec(1.0, 0.0), k=5) == []


def test_search_unknown_collection_returns_empty(store: SqliteVecStore) -> None:
    assert store.search("nope", _vec(1.0, 0.0), k=5) == []


def test_search_zero_k_returns_empty(store: SqliteVecStore) -> None:
    store.upsert("kb", [VectorEntry("c1", _vec(1.0, 0.0))])
    assert store.search("kb", _vec(1.0, 0.0), k=0) == []


def test_search_filter_equality(store: SqliteVecStore) -> None:
    store.upsert(
        "kb",
        [
            VectorEntry("c1", _vec(1.0, 0.0), payload={"kind": "doc"}),
            VectorEntry("c2", _vec(0.9, 0.1), payload={"kind": "image"}),
            VectorEntry("c3", _vec(0.8, 0.2), payload={"kind": "doc"}),
        ],
    )
    hits = store.search("kb", _vec(1.0, 0.0), k=10, filter={"kind": "doc"})
    assert sorted(h.id for h in hits) == ["c1", "c3"]


def test_search_filter_in_clause(store: SqliteVecStore) -> None:
    store.upsert(
        "kb",
        [
            VectorEntry("c1", _vec(1.0, 0.0), payload={"kind": "doc"}),
            VectorEntry("c2", _vec(0.9, 0.1), payload={"kind": "image"}),
            VectorEntry("c3", _vec(0.8, 0.2), payload={"kind": "video"}),
        ],
    )
    hits = store.search("kb", _vec(1.0, 0.0), k=10, filter={"kind": ["doc", "video"]})
    assert sorted(h.id for h in hits) == ["c1", "c3"]


def test_search_filter_multikey_and(store: SqliteVecStore) -> None:
    store.upsert(
        "kb",
        [
            VectorEntry("c1", _vec(1.0, 0.0), payload={"kind": "doc", "lang": "zh"}),
            VectorEntry("c2", _vec(0.9, 0.1), payload={"kind": "doc", "lang": "en"}),
            VectorEntry("c3", _vec(0.8, 0.2), payload={"kind": "image", "lang": "zh"}),
        ],
    )
    hits = store.search(
        "kb", _vec(1.0, 0.0), k=10, filter={"kind": "doc", "lang": "zh"}
    )
    assert [h.id for h in hits] == ["c1"]


def test_payload_matches_helper() -> None:
    p = {"kind": "doc", "lang": "zh"}
    assert _payload_matches(p, {"kind": "doc"})
    assert _payload_matches(p, {"kind": ["doc", "video"]})
    assert _payload_matches(p, {"kind": "doc", "lang": "zh"})
    assert not _payload_matches(p, {"kind": "image"})
    assert not _payload_matches(p, {"kind": "doc", "lang": "en"})
    assert not _payload_matches({}, {"kind": "doc"})
    assert _payload_matches({"kind": "doc"}, {})  # 空 filter 全过


# ──────────────────────────────────────────────────────────────────────
# 边界 / 鲁棒
# ──────────────────────────────────────────────────────────────────────


def test_search_dim_mismatch_raises(store: SqliteVecStore) -> None:
    store.ensure_collection(CollectionSpec("kb", dim=4))
    with pytest.raises(ValueError, match="query dim mismatch"):
        store.search("kb", _vec(1.0, 0.0), k=1)


def test_close_is_idempotent(store: SqliteVecStore) -> None:
    store.close()
    store.close()


def test_persist_across_reopen(tmp_path: Path, _sqlite_vec_available: bool) -> None:
    if not _sqlite_vec_available:
        pytest.skip("sqlite_vec 不可用")
    s1 = SqliteVecStore.open(tmp_path)
    s1.upsert("kb", [VectorEntry("c1", _vec(1.0, 0.0), text="hello")])
    s1.close()

    s2 = SqliteVecStore.open(tmp_path)
    try:
        assert s2.list_collections() == ["kb"]
        hits = s2.search("kb", _vec(1.0, 0.0), k=1)
        assert hits[0].id == "c1"
        assert hits[0].text == "hello"
    finally:
        s2.close()
