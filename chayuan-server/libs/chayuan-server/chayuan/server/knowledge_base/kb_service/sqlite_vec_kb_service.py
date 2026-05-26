"""``KBService`` 子类:基于 ``SqliteVecStore`` 的单机版默认实现(Phase 5.x)。

衔接关系::

    KBServiceFactory(vector_store_type="sqlite-vec")
        └─> SqliteVecKBService
              ├─> retrieval.vector_store_local.SqliteVecStore  (向量 CRUD + KNN)
              └─> server.utils.get_Embeddings(self.embed_model)  (langchain Embeddings)

设计要点:

* **单 sqlite 文件**:所有 KB 共享 ``<CHAYUAN_ROOT>/vectors/local.sqlite``,每个 KB
  对应一个 ``vec0`` 虚拟表 ``vec_<vector_namespace>``。drop_kb 走 collection 删除
  (vec0 的 ``DROP TABLE``),避免文件级删除影响其它 KB。
* **collection 名 = ``self.vector_namespace``**:已经在 ``KBService.__init__`` 里
  做过去重 / sanitization,这里直接用。
* **ID 一致性**:``do_add_doc`` 返 ``[{"id": uuid4_str, "metadata": ...}]``;基类
  ``add_doc`` 把 doc_infos 写入 ``FileDocModel``。``do_delete_doc`` 反向用
  ``list_docs_from_db`` 拿 file→ids 映射,再调 ``store.delete``。
* **向量维度**:首次写入时按 embed 实际输出推断,自动 ``ensure_collection``;后续
  跨进程重启自动复用,``vec_meta`` 表持久。
* **异常容忍**:``do_drop_kb`` / ``do_delete_doc`` 在 collection 不存在时静默跳过
  (与 base.drop_kb 的 best-effort 语义对齐)。
"""
from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from chayuan.server.knowledge_base.kb_service.base import (
    KBService,
    SupportedVSType,
)
from chayuan.server.knowledge_base.utils import KnowledgeFile
from chayuan.server.retrieval.vector_store_local import (
    CollectionSpec,
    LocalVectorStore,
    SqliteVecStore,
    VectorEntry,
)
from chayuan.settings import CHAYUAN_ROOT, Settings

logger = logging.getLogger("chayuan.kb.sqlite_vec")


# ──────────────────────────────────────────────────────────────────────
# 进程内共享单 store
# ──────────────────────────────────────────────────────────────────────


_VECTOR_DIR_NAME = "vectors"  # CLAUDE.md §2.1 数据目录布局
_VECTOR_FILE_NAME = "local.sqlite"
_store: Optional[LocalVectorStore] = None
_store_lock = threading.RLock()


def _resolve_data_dir() -> Path:
    """优先 ``Settings.basic_settings.KB_ROOT_PATH`` 的父目录(即 CHAYUAN_ROOT)。

    单机版 sidecar 启动时已通过 ``CHAYUAN_ROOT`` env 注入,``CHAYUAN_ROOT``
    解析见 ``chayuan.paths``。这里再读一次以兼容 dev 直跑(无 sidecar)。
    """
    return Path(CHAYUAN_ROOT) / _VECTOR_DIR_NAME


def _get_store() -> LocalVectorStore:
    """进程级共享 store。懒 open,线程安全。"""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is None:
            data_dir = _resolve_data_dir()
            _store = SqliteVecStore.open(data_dir, filename=_VECTOR_FILE_NAME)
            logger.info(
                "[sqlite-vec] opened shared store at %s/%s",
                data_dir, _VECTOR_FILE_NAME,
            )
    return _store


def _close_store_for_test() -> None:
    """测试 fixture teardown 用。生产路径不调。"""
    global _store
    with _store_lock:
        if _store is not None:
            try:
                _store.close()
            finally:
                _store = None


# ──────────────────────────────────────────────────────────────────────
# KBService 适配
# ──────────────────────────────────────────────────────────────────────


class SqliteVecKBService(KBService):
    """单机版默认 KB Service。"""

    # 由 do_init 写入,仅 Type 标注
    _embeddings_inst: Any = None
    _dim: Optional[int] = None

    def vs_type(self) -> str:  # type: ignore[override]
        return SupportedVSType.SQLITE_VEC

    # ── 抽象方法实现 ────────────────────────────────────────────────

    def do_init(self) -> None:
        # 不在 init 阶段做 IO:get_Embeddings 会拉模型 / 走外部 API,放到首次 add 时按需。
        self._embeddings_inst = None
        self._dim = None

    def do_create_kb(self) -> None:
        # 这里**不立刻建 collection** —— 维度要等首次 embed 出真实向量才知道。
        # base.create_kb 已经把 KB metadata 写到 DB。collection 在首次 do_add_doc
        # 时按需 ensure。这样空 KB 不占任何向量库存储。
        return

    def do_drop_kb(self) -> None:
        store = _get_store()
        try:
            store.drop_collection(self.vector_namespace)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[sqlite-vec] drop_collection %s 失败:%r", self.vector_namespace, e
            )

    def do_clear_vs(self) -> None:
        store = _get_store()
        store.drop_collection(self.vector_namespace)
        # 不重新 ensure_collection;首次 add 时再按需,与 do_create_kb 一致

    def do_add_doc(
        self, docs: List[Document], **kwargs: Any
    ) -> List[Dict[str, Any]]:
        if not docs:
            return []

        embeddings = self._get_embeddings()
        texts = [d.page_content for d in docs]
        vectors = embeddings.embed_documents(texts)
        if not vectors:
            return []

        # 首次写入按真实向量长度自动建 collection
        store = _get_store()
        if self._dim is None:
            self._dim = len(vectors[0])
        store.ensure_collection(
            CollectionSpec(name=self.vector_namespace, dim=self._dim)
        )

        # ID 用 uuid4 36 字符,与 ``FileDocModel.doc_id String(50)`` 兼容
        ids = [uuid.uuid4().hex for _ in docs]
        entries = [
            VectorEntry(
                id=i,
                vector=v,
                text=t,
                payload=_safe_metadata(d.metadata),
            )
            for i, v, t, d in zip(ids, vectors, texts, docs)
        ]
        store.upsert(self.vector_namespace, entries)
        # 与其它 backend 一致:返 ``{"id": ..., "metadata": ...}``,base 会写入 DB
        return [{"id": i, "metadata": dict(d.metadata)} for i, d in zip(ids, docs)]

    def do_delete_doc(
        self, kb_file: KnowledgeFile, **kwargs: Any
    ) -> List[str]:
        # 走 DB 反查 file→doc_ids 映射;比扫向量库稳定。
        from chayuan.server.db.repository.knowledge_file_repository import (
            list_docs_from_db,
        )

        records = list_docs_from_db(
            kb_name=self.kb_name, file_name=kb_file.filename
        )
        ids = [str(r["id"]) for r in records if r.get("id") is not None]
        if not ids:
            return []
        store = _get_store()
        try:
            store.delete(self.vector_namespace, ids)
        except KeyError:
            # collection 不存在(已 drop / 从未建过) → 视为 0 条删除,不报错
            pass
        return ids

    def do_search(
        self,
        query: str,
        top_k: int,
        score_threshold: float = Settings.kb_settings.SCORE_THRESHOLD,
    ) -> List[Tuple[Document, float]]:
        if top_k <= 0:
            return []
        embeddings = self._get_embeddings()
        vec = embeddings.embed_query(query)
        store = _get_store()
        # collection 不存在(空 KB) → 直接返空
        if self.vector_namespace not in store.list_collections():
            return []

        hits = store.search(self.vector_namespace, vec, k=top_k)
        out: List[Tuple[Document, float]] = []
        for h in hits:
            # score = 1/(1+distance):与单调相似度对齐(高=更相关)。
            # score_threshold 语义:**保留** score >= threshold 的命中;0.0 不过滤。
            if h.score < score_threshold:
                continue
            doc = Document(
                page_content=h.text,
                metadata={**h.payload, "id": h.id, "score": h.score},
            )
            out.append((doc, h.score))
        return out

    # ── 内部 helper ────────────────────────────────────────────────

    def _get_embeddings(self):
        if self._embeddings_inst is not None:
            return self._embeddings_inst
        from chayuan.server.utils import get_Embeddings

        self._embeddings_inst = get_Embeddings(self.embed_model)
        return self._embeddings_inst


def _safe_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """payload 必须能 JSON 序列化。把 ``Path`` / 时间戳 / set 等转成基础类型。

    向量库存的 metadata 跨进程读回,不能保留 langchain 的 ``Document`` 对象引用
    或 PosixPath 等。
    """
    out: Dict[str, Any] = {}
    for k, v in metadata.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = [_to_jsonable(x) for x in v]
        elif isinstance(v, dict):
            out[k] = _safe_metadata(v)
        else:
            out[k] = _to_jsonable(v)
    return out


def _to_jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


__all__ = ["SqliteVecKBService", "_close_store_for_test"]
