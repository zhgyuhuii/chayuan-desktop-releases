"""``SqliteVecKBService`` 单元测试(Phase 5.x)。

策略:不真起 langchain Embeddings(会拉外部 API / 大模型权重),用
``monkeypatch`` 把 ``_get_embeddings`` 替换为返回确定性向量的 fake,
专注测 KBService 接口契约 + SqliteVecStore 集成。

覆盖:
    * vs_type 报告 ``sqlite-vec``
    * do_init 不做 IO(允许在没装 embed model 时构造)
    * do_create_kb 是 lazy(空 KB 不建 collection)
    * do_add_doc 首次按真实向量推断 dim 自动建 collection
    * do_add_doc 返回 ``[{"id": ..., "metadata": ...}]`` 与 base 协议一致
    * do_search 命中且按 score 降序
    * do_search 在空 KB / collection 不存在时返 []
    * do_delete_doc 通过 list_docs_from_db 反查 ID 并真实删除
    * do_drop_kb / do_clear_vs 移除 collection

需要 sqlite_vec;若环境缺扩展能力整组 skip。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, List
from unittest.mock import patch

import pytest
from langchain_core.documents import Document


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


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


class FakeEmbeddings:
    """确定性 embeddings,生成在多维上分散的向量 ── 短文本也不会全部共线。

    每个文本的字符按位置打到不同维度上,空槽填本文本长度对应的常量,确保
    "a" 与 "z" 的方向不同(否则 cosine 距离恒为 0,score_threshold 无法验证)。
    """

    DIM = 8

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text)

    def _embed(self, text: str) -> List[float]:
        v = [0.0] * self.DIM
        for i, c in enumerate(text):
            # 按字符位置 spread 到不同维度,值为 ord/256
            v[i % self.DIM] += float(ord(c) % 256) / 256.0
        # 在尾部加一点随文本长度变化的"指纹",避免极端情况(空文本 / 单字符)
        v[(len(text)) % self.DIM] += 1.0
        return v


@pytest.fixture
def isolated_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _sqlite_vec_available: bool
) -> Iterator[Path]:
    """每个测试一个独立的 CHAYUAN_ROOT,store 文件不跨测试共享。"""
    if not _sqlite_vec_available:
        pytest.skip("sqlite_vec / load_extension 不可用")
    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.sqlite_vec_kb_service.CHAYUAN_ROOT",
        tmp_path,
    )
    # 清掉跨测试的 module-level 单例
    from chayuan.server.knowledge_base.kb_service import sqlite_vec_kb_service as svks

    svks._store = None  # type: ignore[attr-defined]
    yield tmp_path
    svks._close_store_for_test()


@pytest.fixture
def kb_service(
    isolated_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """构造 SqliteVecKBService,绕开 KBService.__init__ 里的 DB / 文件副作用。

    base.KBService.__init__ 会:
        - 调 ``get_kb_vector_namespace`` (DB 查询,可能 0 行或 'milvus')
        - 调 ``get_kb_path`` / ``get_doc_path`` (依赖 KB_ROOT_PATH 配置)

    单元测试只关心 do_* 方法的契约,不需要 DB。我们用 ``__new__`` 跳过
    __init__,然后手填几个必需属性。
    """
    from chayuan.server.knowledge_base.kb_service.sqlite_vec_kb_service import (
        SqliteVecKBService,
    )

    svc = SqliteVecKBService.__new__(SqliteVecKBService)
    svc.kb_name = "kb_test"
    svc.kb_info = "test kb"
    svc.embed_model = "fake-embed"
    svc.vector_namespace = "ns_test"
    svc.kb_path = str(isolated_data_dir / "kb")
    svc.doc_path = str(isolated_data_dir / "docs")
    svc.do_init()

    # 注入 fake embeddings:替换 _get_embeddings 而非走 get_Embeddings
    fake = FakeEmbeddings()
    monkeypatch.setattr(svc, "_get_embeddings", lambda: fake)
    return svc


# ──────────────────────────────────────────────────────────────────────
# 基础契约
# ──────────────────────────────────────────────────────────────────────


def test_vs_type_is_sqlite_vec(kb_service) -> None:
    from chayuan.server.knowledge_base.kb_service.base import SupportedVSType

    assert kb_service.vs_type() == SupportedVSType.SQLITE_VEC == "sqlite-vec"


def test_do_create_kb_is_lazy(kb_service, isolated_data_dir: Path) -> None:
    """do_create_kb 不立刻建 collection(空 KB 不占向量库存储)。"""
    from chayuan.server.knowledge_base.kb_service.sqlite_vec_kb_service import (
        _get_store,
    )

    kb_service.do_create_kb()
    store = _get_store()
    assert "ns_test" not in store.list_collections()


# ──────────────────────────────────────────────────────────────────────
# add / search 主路径
# ──────────────────────────────────────────────────────────────────────


def test_do_add_doc_creates_collection_and_returns_doc_infos(
    kb_service, isolated_data_dir: Path
) -> None:
    from chayuan.server.knowledge_base.kb_service.sqlite_vec_kb_service import (
        _get_store,
    )

    docs = [
        Document(page_content="foo bar", metadata={"source": "f.txt"}),
        Document(page_content="hello world", metadata={"source": "f.txt"}),
    ]
    infos = kb_service.do_add_doc(docs)

    assert len(infos) == 2
    for info in infos:
        assert "id" in info and len(info["id"]) > 0
        assert info["metadata"]["source"] == "f.txt"

    store = _get_store()
    assert "ns_test" in store.list_collections()
    assert store.count("ns_test") == 2


def test_do_add_doc_empty_returns_empty(kb_service) -> None:
    assert kb_service.do_add_doc([]) == []


def test_do_search_returns_topk_in_score_order(kb_service) -> None:
    docs = [
        Document(page_content="abc", metadata={"src": "a"}),
        Document(page_content="abd", metadata={"src": "b"}),
        Document(page_content="xyz", metadata={"src": "c"}),
    ]
    kb_service.do_add_doc(docs)

    hits = kb_service.do_search("abc", top_k=3, score_threshold=0.0)
    assert len(hits) == 3
    # tuple (Document, score)
    for doc, score in hits:
        assert isinstance(doc, Document)
        assert isinstance(score, float)
    scores = [s for _, s in hits]
    assert scores == sorted(scores, reverse=True)
    # 与自身完全匹配的"abc" 应排第一,score ≈ 1.0
    assert hits[0][0].page_content == "abc"
    assert hits[0][1] > 0.99


def test_do_search_empty_kb_returns_empty(kb_service) -> None:
    """collection 还没建(空 KB)→ 直接返 [],不抛。"""
    hits = kb_service.do_search("anything", top_k=5, score_threshold=0.0)
    assert hits == []


def test_do_search_score_threshold_filters(kb_service) -> None:
    docs = [
        Document(page_content="a", metadata={}),
        Document(page_content="z", metadata={}),
    ]
    kb_service.do_add_doc(docs)
    # 较高 threshold 只保留与 query 极接近的
    hits = kb_service.do_search("a", top_k=10, score_threshold=0.99)
    assert len(hits) >= 1
    # 自身命中 score≈1
    assert hits[0][0].page_content == "a"


def test_do_search_top_k_zero_returns_empty(kb_service) -> None:
    kb_service.do_add_doc([Document(page_content="x", metadata={})])
    assert kb_service.do_search("x", top_k=0, score_threshold=0.0) == []


# ──────────────────────────────────────────────────────────────────────
# delete / drop / clear
# ──────────────────────────────────────────────────────────────────────


def test_do_delete_doc_removes_by_file_name(kb_service, monkeypatch) -> None:
    """do_delete_doc 调 list_docs_from_db 拿 file→ids,然后真实从 store 删。"""
    docs_a = [
        Document(page_content="aaa", metadata={"source": "a.txt"}),
        Document(page_content="bbb", metadata={"source": "a.txt"}),
    ]
    docs_b = [Document(page_content="ccc", metadata={"source": "b.txt"})]
    infos_a = kb_service.do_add_doc(docs_a)
    kb_service.do_add_doc(docs_b)

    # mock list_docs_from_db 返回 a.txt 的 ids
    a_ids = [info["id"] for info in infos_a]

    def _fake_list_docs_from_db(*, kb_name: str, file_name: str, **_kw):
        if file_name == "a.txt":
            return [{"id": i, "metadata": {}} for i in a_ids]
        return []

    monkeypatch.setattr(
        "chayuan.server.db.repository.knowledge_file_repository.list_docs_from_db",
        _fake_list_docs_from_db,
    )

    from chayuan.server.knowledge_base.kb_service.sqlite_vec_kb_service import (
        _get_store,
    )
    store = _get_store()
    assert store.count("ns_test") == 3

    class _FakeKBFile:
        kb_name = "kb_test"
        filename = "a.txt"

    removed = kb_service.do_delete_doc(_FakeKBFile())
    assert sorted(removed) == sorted(a_ids)
    assert store.count("ns_test") == 1


def test_do_delete_doc_no_matching_records_is_noop(kb_service, monkeypatch) -> None:
    monkeypatch.setattr(
        "chayuan.server.db.repository.knowledge_file_repository.list_docs_from_db",
        lambda **_kw: [],
    )

    class _FakeKBFile:
        kb_name = "kb_test"
        filename = "missing.txt"

    assert kb_service.do_delete_doc(_FakeKBFile()) == []


def test_do_drop_kb_removes_collection(kb_service) -> None:
    kb_service.do_add_doc(
        [Document(page_content="x", metadata={})]
    )
    from chayuan.server.knowledge_base.kb_service.sqlite_vec_kb_service import (
        _get_store,
    )

    assert "ns_test" in _get_store().list_collections()
    kb_service.do_drop_kb()
    assert "ns_test" not in _get_store().list_collections()
    # 二次 drop 静默
    kb_service.do_drop_kb()


def test_do_clear_vs_drops_then_lazy_recreate(kb_service) -> None:
    kb_service.do_add_doc(
        [Document(page_content="x", metadata={})]
    )
    from chayuan.server.knowledge_base.kb_service.sqlite_vec_kb_service import (
        _get_store,
    )

    assert _get_store().count("ns_test") == 1

    kb_service.do_clear_vs()
    # collection 被删除,不立即重建(lazy 与 do_create_kb 一致)
    assert "ns_test" not in _get_store().list_collections()

    # 再次 add 时按需重建
    kb_service.do_add_doc(
        [Document(page_content="y", metadata={})]
    )
    assert _get_store().count("ns_test") == 1


# ──────────────────────────────────────────────────────────────────────
# 工厂集成
# ──────────────────────────────────────────────────────────────────────


def test_factory_returns_sqlite_vec_kb_service(
    monkeypatch: pytest.MonkeyPatch, isolated_data_dir: Path
) -> None:
    """``KBServiceFactory.get_service(..., 'sqlite-vec')`` 返 SqliteVecKBService。"""
    from chayuan.server.knowledge_base.kb_service.base import (
        KBServiceFactory,
        SupportedVSType,
    )
    from chayuan.server.knowledge_base.kb_service.sqlite_vec_kb_service import (
        SqliteVecKBService,
    )

    # 工厂会调 KBService.__init__,可能依赖 DB / 文件。stub 掉避免拉重资源。
    monkeypatch.setattr(SqliteVecKBService, "do_init", lambda self: None)
    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.base.get_kb_vector_namespace",
        lambda *_a, **_kw: "ns_factory",
    )
    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.base.get_kb_path",
        lambda name: str(isolated_data_dir / name),
    )
    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.base.get_doc_path",
        lambda name: str(isolated_data_dir / name / "docs"),
    )
    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.base.kb_exists",
        lambda *_a, **_kw: True,
    )

    svc = KBServiceFactory.get_service(
        kb_name="kb_factory",
        vector_store_type=SupportedVSType.SQLITE_VEC,
        embed_model="fake",
    )
    assert isinstance(svc, SqliteVecKBService)
    assert svc.vs_type() == "sqlite-vec"
