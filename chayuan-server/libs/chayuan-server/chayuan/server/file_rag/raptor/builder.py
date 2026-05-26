"""RAPTOR 构建器。

输入：一个 KB 名称（kb_name）
过程：
  1. 从 KB 向量库读全量 chunks（metadata 中 raptor_level 为空的视为 level=0 原文）
  2. 对 level=0 chunks 做 GMM 软聚类
  3. 对每簇调 LLM 生成摘要 → 产生 level=1 Documents
  4. 把 level=1 Documents 写回同一向量库（metadata.raptor_level=1, raptor_cluster_id=K）
  5. 若 level=1 的个数 > 1，递归到 level=2 ... 直到 cluster_count==1 或达到 MAX_LEVELS

输出：同一向量库里多出若干层"摘要 Documents"；其它代码无需改动即可召回。

**幂等**：重跑前会先按 metadata `raptor_level>=1` 删除旧摘要，避免重复堆积。

**成本感知**：每层调用 LLM 数 ≈ 簇数；10k chunks / cluster_size=5 → 约 2000+400+80+...
总 LLM 调用约 2500 次。在生产用小模型（GPT-4o-mini / deepseek-chat）约 0.5 美元。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from langchain_core.documents import Document

logger = logging.getLogger("chayuan.raptor.builder")


SUMMARY_SYSTEM = """你是文本摘要专家。请对下面一组相关片段进行**归纳性**摘要（不是简单拼接）：
- 80-200 字，重点覆盖共同主题、关键事实、数值 / 日期 / 实体
- 保持客观，不要自己发挥
- 末尾列出 2-5 个关键词（逗号分隔）"""


@dataclass
class RaptorBuildReport:
    kb_name: str
    levels: int
    summaries_added: int
    elapsed_sec: float = 0.0
    error: Optional[str] = None


def build_raptor_for_kb(
    kb_name: str,
    *,
    target_cluster_size: int = 5,
    max_levels: int = 3,
    llm_model: Optional[str] = None,
    summarizer: Optional[Callable[[str, str], str]] = None,
) -> RaptorBuildReport:
    """build 入口。``summarizer`` 可注入自定义（测试用）；默认走 get_ChatOpenAI。"""
    import time
    t0 = time.time()
    report = RaptorBuildReport(kb_name=kb_name, levels=0, summaries_added=0)

    try:
        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    except Exception as e:  # noqa: BLE001
        report.error = f"KBServiceFactory 不可用：{e}"
        return report
    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        report.error = f"KB {kb_name!r} 不存在"
        return report

    summarize = summarizer or _default_summarizer(llm_model)

    # 1) 先清除旧摘要（幂等）
    _cleanup_old_summaries(kb)

    # 2) 取 level=0 的全量 chunks + 对应向量
    level0 = _load_level_docs(kb, level=0)
    if not level0:
        report.error = "KB 中没有可摘要的 chunks（上传过文件了吗？）"
        return report

    current_docs = level0
    added_total = 0
    for level in range(1, int(max_levels) + 1):
        logger.info("[raptor] kb=%s level=%d input=%d", kb_name, level, len(current_docs))
        # 向量化（用 KB 自己配的 embedding 模型）
        try:
            from chayuan.server.utils import get_Embeddings
            emb = get_Embeddings(kb.embed_model)
            vectors = emb.embed_documents([d.page_content or "" for d in current_docs])
        except Exception as e:  # noqa: BLE001
            logger.warning("embed 失败，停止递归：%r", e)
            report.error = f"embed failed at level {level}: {e}"
            break

        # 聚类
        from chayuan.server.file_rag.raptor.clustering import gmm_cluster
        labels, n_clusters = gmm_cluster(
            vectors, target_cluster_size=int(target_cluster_size),
        )
        if n_clusters <= 1 or len(current_docs) <= int(target_cluster_size):
            logger.info("[raptor] level=%d 已收敛（簇=%d），停止递归", level, n_clusters)
            break

        # 每簇摘要
        level_docs: List[Document] = []
        for cluster_id in sorted(set(labels)):
            members = [d for d, lb in zip(current_docs, labels) if lb == cluster_id]
            if not members:
                continue
            joined = "\n\n".join(
                (d.page_content or "")[:1500] for d in members[:12]
            )
            try:
                summary = summarize(joined, kb_name)
            except Exception as e:  # noqa: BLE001
                logger.warning("摘要生成失败 cluster=%s (level=%d): %r",
                                cluster_id, level, e)
                continue
            if not summary:
                continue
            summary_id = f"raptor:{kb_name}:L{level}:C{cluster_id}:{uuid.uuid4().hex[:8]}"
            meta: Dict[str, Any] = {
                "raptor_level": level,
                "raptor_cluster_id": int(cluster_id),
                "raptor_kb": kb_name,
                "raptor_source_ids": [
                    str((m.metadata or {}).get("id")) for m in members
                    if (m.metadata or {}).get("id")
                ],
                "id": summary_id,
                "source": f"__raptor__/L{level}/C{cluster_id}",
            }
            level_docs.append(Document(page_content=summary, metadata=meta))

        if not level_docs:
            logger.info("[raptor] level=%d 无产出，停止", level)
            break

        # 写回向量库（走 KB 的 do_add_doc，保证 schema 一致）
        try:
            kb.do_add_doc(docs=level_docs)
        except Exception as e:  # noqa: BLE001
            logger.warning("写回向量库失败 level=%d: %r", level, e)
            report.error = f"add failed at level {level}: {e}"
            break

        added_total += len(level_docs)
        report.levels = level
        current_docs = level_docs

    report.summaries_added = added_total
    report.elapsed_sec = round(time.time() - t0, 2)
    # RAPTOR 改变了 KB 文档集合 → BM25 索引必须失效
    try:
        from chayuan.server.file_rag.hybrid_service import invalidate_bm25_cache
        invalidate_bm25_cache(kb_name)
    except Exception:  # noqa: BLE001
        pass
    return report


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _default_summarizer(llm_model: Optional[str]) -> Callable[[str, str], str]:
    """返回一个 summarize(text, kb_name) -> summary 函数。"""
    def _do(text: str, kb_name: str) -> str:
        try:
            from chayuan.server.observability.langfuse_integration import (
                inject_into_callbacks,
            )
            from chayuan.server.utils import get_ChatOpenAI, get_default_llm
            model = (llm_model or get_default_llm()).strip()
            llm = get_ChatOpenAI(
                model_name=model, temperature=0.0, streaming=False,
                callbacks=inject_into_callbacks([]) or None,
            )
            resp = llm.invoke([
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user",
                 "content": f"（所属知识库：{kb_name}）\n\n{text[:6000]}"},
            ])
            return (getattr(resp, "content", None) or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("summarizer LLM 失败：%r", e)
            return ""
    return _do


def _load_level_docs(kb, level: int = 0) -> List[Document]:
    """从 KB 拉出 raptor_level==level 的所有 Document。

    level=0 时 metadata 里一般没有 raptor_level 键（老 chunks）；我们统一认作 0。
    不同向量后端存储细节不同，优先用 docstore._dict（FAISS）；兜底 similarity_search。
    """
    vs = _get_vector_store(kb)
    if vs is None:
        return []
    all_docs: List[Document] = []
    try:
        ds = getattr(vs, "docstore", None)
        inner = getattr(ds, "_dict", None)
        if isinstance(inner, dict):
            all_docs = list(inner.values())
    except Exception:  # noqa: BLE001
        pass
    if not all_docs:
        try:
            all_docs = vs.similarity_search("", k=100000)
        except Exception:  # noqa: BLE001
            return []

    def _match(doc: Document) -> bool:
        lvl = int((doc.metadata or {}).get("raptor_level") or 0)
        return lvl == int(level)
    return [d for d in all_docs if _match(d)]


def _cleanup_old_summaries(kb) -> None:
    """删除所有 metadata.raptor_level>=1 的老摘要。"""
    vs = _get_vector_store(kb)
    if vs is None:
        return
    ids_to_del: List[str] = []
    try:
        ds = getattr(vs, "docstore", None)
        inner = getattr(ds, "_dict", None)
        if isinstance(inner, dict):
            for did, doc in inner.items():
                lvl = int((doc.metadata or {}).get("raptor_level") or 0)
                if lvl >= 1:
                    ids_to_del.append(str(did))
    except Exception:  # noqa: BLE001
        return
    if not ids_to_del:
        return
    try:
        # FAISS / Chroma 的 delete 签名：delete([ids])
        kb.del_doc_by_ids(ids_to_del)
    except Exception as e:  # noqa: BLE001
        logger.debug("清除旧 raptor 摘要失败（可忽略）：%r", e)


def _get_vector_store(kb):
    for attr in ("vs", "vector_store", "_vs", "_vectorstore"):
        v = getattr(kb, attr, None)
        if v is not None:
            return v
    for meth in ("load_vector_store", "_load_vector_store"):
        fn = getattr(kb, meth, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                continue
    return None
