"""Text2SQL 训练语料 Repository。

职责：
- 幂等写入（sha1 去重）
- 按 source + kind + 审核状态 查询
- 用计数 / 反馈分 累计（失败不抛，避免业务路径被审计影响）

向量索引目前采用 **轻量内存向量存**（按 source_id 懒加载），理由：
- 样本量预期每源 < 数千条，远低于 FAISS 起步规模
- 避免给每个数据源再起一套 FAISS 文件，部署更简单
- 未来规模变大，换 Milvus collection 的实现只改本文件不影响上层
"""
from __future__ import annotations

import hashlib
import logging
import threading
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from chayuan.server.db.models.sql_training_model import SqlTrainingSampleModel
from chayuan.server.db.session import session_scope, with_session

logger = logging.getLogger("chayuan.sql_training.repo")


def _ref_hash(source_id: int, kind: str, *parts: str) -> str:
    raw = f"{source_id}|{kind}|" + "|".join(p or "" for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@with_session
def add_sample(
    session: Session,
    *,
    source_id: int,
    kind: str,
    question: str = "",
    sql: str = "",
    content: str = "",
    dialect: str = "",
    approved: int = 1,
    created_by: Optional[int] = None,
) -> Tuple[int, bool]:
    """upsert：存在则返回 (id, False)；新增返回 (id, True)。"""
    if kind not in ("ddl", "doc", "pair"):
        raise ValueError(f"kind must be ddl/doc/pair, got {kind!r}")
    rh = _ref_hash(source_id, kind, question, sql, content)
    existing = session.query(SqlTrainingSampleModel).filter(
        SqlTrainingSampleModel.ref_hash == rh
    ).one_or_none()
    if existing is not None:
        return int(existing.id), False
    row = SqlTrainingSampleModel(
        source_id=int(source_id), kind=kind,
        question=question or "", sql=sql or "", content=content or "",
        dialect=dialect or "",
        approved=int(approved), ref_hash=rh, created_by=created_by,
    )
    session.add(row)
    session.flush()
    # 新增 → 异步清理本源向量缓存
    _invalidate_index(int(source_id))
    return int(row.id), True


@with_session
def list_samples(
    session: Session,
    source_id: int,
    kind: Optional[str] = None,
    approved_only: bool = True,
    limit: int = 500,
) -> List[Dict]:
    q = session.query(SqlTrainingSampleModel).filter(
        SqlTrainingSampleModel.source_id == int(source_id)
    )
    if kind:
        q = q.filter(SqlTrainingSampleModel.kind == kind)
    if approved_only:
        q = q.filter(SqlTrainingSampleModel.approved == 1)
    rows = q.order_by(SqlTrainingSampleModel.id.desc()).limit(int(limit)).all()
    return [
        {
            "id": r.id, "source_id": r.source_id, "kind": r.kind,
            "question": r.question, "sql": r.sql, "content": r.content,
            "dialect": r.dialect, "approved": r.approved,
            "hit_count": r.hit_count, "feedback_score": r.feedback_score,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@with_session
def delete_sample(session: Session, sample_id: int) -> bool:
    row = session.get(SqlTrainingSampleModel, int(sample_id))
    if row is None:
        return False
    sid = int(row.source_id)
    session.delete(row)
    _invalidate_index(sid)
    return True


def bump_hit(sample_ids: List[int]) -> None:
    """成功使用一条样本后加计数；任何异常都忽略。"""
    if not sample_ids:
        return
    try:
        with session_scope() as s:
            s.query(SqlTrainingSampleModel).filter(
                SqlTrainingSampleModel.id.in_([int(x) for x in sample_ids])
            ).update(
                {SqlTrainingSampleModel.hit_count: SqlTrainingSampleModel.hit_count + 1},
                synchronize_session=False,
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("bump_hit 失败（忽略）：%r", e)


@with_session
def feedback(session: Session, sample_id: int, delta: int) -> None:
    row = session.get(SqlTrainingSampleModel, int(sample_id))
    if row is None:
        return
    row.feedback_score = int(row.feedback_score or 0) + int(delta)


# ---------------------------------------------------------------------------
# 按源维护的内存向量索引（懒加载 + 失效）
# ---------------------------------------------------------------------------

_INDEX_LOCK = threading.RLock()
_INDEX_CACHE: Dict[int, "_SourceIndex"] = {}


class _SourceIndex:
    """极简向量索引：numpy dot + 归一化；足以支撑每源几千条样本。

    刻意不用 FAISS：① 免文件管理 ② 直接跟 session 内存 ③ 失效成本 O(1)。
    """

    def __init__(self, source_id: int):
        self.source_id = source_id
        self._built = False
        self._embeddings = None  # np.ndarray (n, d)
        self._rows: List[Dict] = []

    def build(self, samples: List[Dict]) -> None:
        """用现有 embed_texts 接口批量向量化。"""
        import numpy as np
        if not samples:
            self._embeddings = None
            self._rows = []
            self._built = True
            return
        # 关键：把 question / content / DDL 作为 key 文本，分 kind 差异化处理
        texts = []
        for s in samples:
            if s.get("kind") == "pair":
                texts.append(s.get("question") or "")
            elif s.get("kind") == "ddl":
                texts.append((s.get("sql") or "")[:600])
            else:  # doc
                texts.append((s.get("content") or "")[:600])
        from chayuan.server.knowledge_base.kb_service.base import (
            KBServiceFactory,  # 仅为复用 embed 调用路径
        )
        # 直接走 kb_doc_api 下面的 embedding 通道最稳；但存在项目 embed 入口：
        from chayuan.server.utils import get_Embeddings
        emb = get_Embeddings()
        vecs = emb.embed_documents(texts)
        mat = np.asarray(vecs, dtype="float32")
        # 归一化
        norm = np.linalg.norm(mat, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        self._embeddings = (mat / norm).astype("float32")
        self._rows = samples
        self._built = True

    def search(self, query: str, top_k: int, kind: Optional[str] = None) -> List[Dict]:
        import numpy as np
        if not self._built or self._embeddings is None or not self._rows:
            return []
        from chayuan.server.utils import get_Embeddings
        emb = get_Embeddings()
        try:
            q = np.asarray(emb.embed_query(query), dtype="float32")
        except Exception:  # noqa: BLE001
            return []
        qn = np.linalg.norm(q)
        if qn == 0:
            return []
        q = q / qn
        scores = self._embeddings @ q  # cosine
        # 按 kind 过滤
        order = np.argsort(-scores)
        out = []
        for idx in order:
            row = self._rows[int(idx)]
            if kind and row.get("kind") != kind:
                continue
            out.append({**row, "rag_score": float(scores[int(idx)])})
            if len(out) >= top_k:
                break
        return out


def _get_index(source_id: int) -> _SourceIndex:
    with _INDEX_LOCK:
        idx = _INDEX_CACHE.get(int(source_id))
        if idx is None or not idx._built:
            idx = _SourceIndex(int(source_id))
            samples = list_samples(int(source_id), approved_only=True, limit=5000)
            try:
                idx.build(samples)
            except Exception as e:  # noqa: BLE001
                logger.warning("build SQL training index 失败 source=%s：%r", source_id, e)
                idx._built = True  # 空索引，下次不重试
            _INDEX_CACHE[int(source_id)] = idx
        return idx


def _invalidate_index(source_id: int) -> None:
    with _INDEX_LOCK:
        _INDEX_CACHE.pop(int(source_id), None)


def retrieve_similar(
    source_id: int, query: str, top_k: int = 5, kind: Optional[str] = None,
) -> List[Dict]:
    """按 kind 做相似检索。kind=None 时全量。"""
    idx = _get_index(int(source_id))
    return idx.search(query=query, top_k=int(top_k), kind=kind)
