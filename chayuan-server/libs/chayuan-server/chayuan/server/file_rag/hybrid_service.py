"""混合检索 + CrossEncoder rerank + 上下文扩展 统一入口（P0-1）。

职责：
1. 把向量召回、BM25 召回通过本地 RRF 融合
2. 对融合结果做 CrossEncoder rerank（可选）
3. 对 rerank 后的 Top-K 做"邻居 chunk 合并"（简化版 Parent-Child）

所有开关独立 on/off；全部关闭时行为与旧 search_docs 完全一致。

在 KBService.search_docs 中调用；也供 VectorKbConnector 复用。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from chayuan.settings import Settings

logger = logging.getLogger("chayuan.file_rag.hybrid")

# CrossEncoder 模型进程内单例（首次加载昂贵，之后毫秒级）
_RERANKER_LOCK = threading.RLock()
_RERANKER_CACHE: Dict[str, Any] = {}


def _reranker_config() -> Tuple[str, str, int, bool, str, Optional[str]]:
    kb = Settings.kb_settings

    # 1) capability_router 优先 ── UI 上"默认模型选择 → 重排"是真源,与
    #    embedding 同一套机制(参见 :func:`server.utils.get_default_embedding`)。
    router_pick = ""
    try:
        from chayuan.server.capability_router import resolve_model
        router_pick = resolve_model("rerank") or ""
    except Exception as e:  # noqa: BLE001
        logger.debug("[reranker] capability_router lookup failed: %r", e)

    raw_name = router_pick or getattr(kb, "RERANKER_MODEL", "") or "BAAI/bge-reranker-v2-m3"

    # 2) 如果是 local_index 风格的 model_id(``models/rerank/BAAI--bge-...``),
    #    翻译成磁盘绝对路径供 CrossEncoder loader 直接吃。
    try:
        from chayuan.server.model_registry.path_resolver import (
            resolve_model_id_to_path,
        )
        model_name = resolve_model_id_to_path(raw_name)
    except Exception as e:  # noqa: BLE001
        logger.debug("[reranker] path_resolver failed: %r", e)
        model_name = raw_name

    device = getattr(kb, "RERANKER_DEVICE", "cpu") or "cpu"
    max_length = int(getattr(kb, "RERANKER_MAX_LENGTH", 512) or 512)
    local_files_only = bool(getattr(kb, "RERANKER_LOCAL_FILES_ONLY", True))
    resolved_model = _local_hf_snapshot(model_name) if local_files_only else model_name
    # cache key 用最终 model_name(已可能被路径翻译),保证 capability_router 一切
    # 模型 reranker 单例就自动重建,不命中旧缓存。
    key = f"{model_name}@{device}@{max_length}@local={local_files_only}"
    return model_name, device, max_length, local_files_only, key, resolved_model


def _reranker_preflight_available() -> bool:
    """Fast check before entering CrossEncoder loading.

    In offline/local-files-only deployments, a missing reranker is expected until
    the model is pre-warmed. Avoid touching sentence-transformers on every query.
    """
    _model_name, _device, _max_length, local_files_only, key, resolved_model = _reranker_config()
    if local_files_only and not resolved_model:
        with _RERANKER_LOCK:
            _RERANKER_CACHE.setdefault(key, None)
        return False
    return True


def _local_hf_snapshot(model_name: str) -> Optional[str]:
    """返回本地 HF snapshot 路径;不存在则 None。全程不联网。"""
    p = Path(model_name).expanduser()
    if p.exists():
        return str(p)
    if "/" not in model_name:
        return None
    cache_name = "models--" + model_name.replace("/", "--")
    roots = [
        Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))) / "hub",
    ]
    cy_root = os.environ.get("CHAYUAN_ROOT")
    if cy_root:
        roots.append(Path(cy_root) / "models" / "huggingface" / "hub")
    for root in roots:
        snap_root = root / cache_name / "snapshots"
        if not snap_root.exists():
            continue
        snapshots = sorted((p for p in snap_root.iterdir() if p.is_dir()), key=lambda x: x.stat().st_mtime, reverse=True)
        if snapshots:
            return str(snapshots[0])
    return None


# ---------------------------------------------------------------------------
# CrossEncoder 单例
# ---------------------------------------------------------------------------

def _get_reranker():
    """懒加载 CrossEncoder；无 sentence_transformers / 下载失败时返回 None（fail-open）。"""
    model_name, device, max_length, local_files_only, key, resolved_model = _reranker_config()
    with _RERANKER_LOCK:
        if key in _RERANKER_CACHE:
            return _RERANKER_CACHE[key]
        if local_files_only and not resolved_model:
            logger.debug(
                "rerank 模型未在本地缓存中找到，已跳过联网下载并降级为透传：%s",
                model_name,
            )
            _RERANKER_CACHE[key] = None
            return None
        try:
            from sentence_transformers import CrossEncoder  # type: ignore
        except Exception as e:  # noqa: BLE001
            logger.warning("sentence_transformers 未安装，rerank 降级为透传：%r", e)
            _RERANKER_CACHE[key] = None
            return None
        try:
            kwargs = {
                "model_name": resolved_model or model_name,
                "max_length": max_length,
                "device": device,
            }
            try:
                ce = CrossEncoder(**kwargs, local_files_only=local_files_only)
            except TypeError:
                ce = CrossEncoder(**kwargs)
            _RERANKER_CACHE[key] = ce
            logger.info("rerank 模型已加载：%s on %s", resolved_model or model_name, device)
            return ce
        except Exception as e:  # noqa: BLE001
            logger.warning("rerank 模型加载失败：%r", e)
            _RERANKER_CACHE[key] = None
            return None


# ---------------------------------------------------------------------------
# BM25 索引按 KB 做缓存（构建有成本，文件增删时失效）
# ---------------------------------------------------------------------------

_BM25_LOCK = threading.RLock()
_BM25_CACHE: Dict[str, Tuple[Any, int]] = {}  # kb_name → (retriever, docs_version)

_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.$:/\\-]{1,}")
_WORD_RE = re.compile(r"[A-Za-z0-9_.$:/\\-]+|[\u4e00-\u9fff]{2,}")
_LEXICAL_STOPWORDS = {
    "是什么", "什么", "多少", "哪个", "哪些", "如何", "怎么", "请问", "请", "帮我",
    "查询", "搜索", "一下", "这个", "那个", "文档", "知识库", "里面", "关于",
    "the", "what", "is", "are", "please",
}


def _norm_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def _compact_identifier(text: str) -> str:
    """把 api_key / API Key / api-key / api.key 统一成 apikey。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", _norm_text(text))


def _lexical_query_terms(query: str) -> List[str]:
    """抽取关键词/标识符,用于精确召回。

    Dense embedding 对 APIKey、配置项、域名、错误码、函数名这类 token 不稳定；
    生产 RAG 通常会额外做 lexical exact match,并做 snake/camel/space 归一。
    """
    q = _norm_text(query)
    if not q:
        return []
    terms: List[str] = []
    terms.extend(_IDENT_RE.findall(q))
    try:
        import jieba  # type: ignore
        terms.extend(t.strip() for t in jieba.cut_for_search(q) if t.strip())
    except Exception:  # noqa: BLE001
        terms.extend(_WORD_RE.findall(q))

    out: List[str] = []
    seen: set[str] = set()
    for raw in terms:
        term = raw.strip(" \t\r\n,，。？?！!；;：:'\"`")
        if len(term) < 2:
            continue
        low = _norm_text(term)
        compact = _compact_identifier(term)
        if low in _LEXICAL_STOPWORDS or compact in _LEXICAL_STOPWORDS:
            continue
        # 保留原形和 compact 形;例如 "api key" / "api_key" 都能匹配 "apikey"。
        for candidate in (low, compact):
            if len(candidate) < 2 or candidate in seen:
                continue
            seen.add(candidate)
            out.append(candidate)
    return out[:10]


def _doc_text_and_meta(doc: Any) -> Tuple[str, Dict[str, Any]]:
    if hasattr(doc, "page_content"):
        return (getattr(doc, "page_content", "") or ""), dict(getattr(doc, "metadata", None) or {})
    if isinstance(doc, dict):
        return (doc.get("page_content") or doc.get("content") or ""), dict(doc.get("metadata") or {})
    return "", {}


def lexical_search_docs(kb_service: Any, query: str, top_k: int) -> List[Document]:
    """标识符/关键词精确召回兜底。

    目标场景:apikey、api_key、API Key、aidooo.com、错误码、函数名、公司名等
    低频字面证据。它不替代向量检索,只作为 hybrid 的一条高精度候选路。
    """
    terms = _lexical_query_terms(query)
    if not terms:
        return []
    docs = _all_docs_of_kb(kb_service)
    if not docs:
        try:
            docs = kb_service.list_docs() or []
        except Exception as e:  # noqa: BLE001
            logger.debug("lexical list_docs failed: %r", e)
            return []

    scored: List[Tuple[float, Document]] = []
    for doc in docs:
        content, meta = _doc_text_and_meta(doc)
        if not content:
            continue
        hay = _norm_text(content + "\n" + " ".join(str(v) for v in meta.values()))
        hay_compact = _compact_identifier(hay)
        hits = []
        for term in terms:
            if term in hay or _compact_identifier(term) in hay_compact:
                hits.append(term)
        if not hits:
            continue
        exact_identifier_hits = sum(1 for h in hits if re.search(r"[a-z]", h) and _compact_identifier(h) in hay_compact)
        score = 0.7 + min(0.25, len(set(hits)) / max(1, len(terms)) * 0.25) + min(0.2, exact_identifier_hits * 0.1)
        clean_meta = dict(meta)
        clean_meta.setdefault("source", clean_meta.get("file_name") or clean_meta.get("filename") or "")
        clean_meta["retrieval_path"] = "keyword"
        clean_meta["score"] = min(score, 1.0)
        out = Document(page_content=content, metadata=clean_meta)
        scored.append((float(clean_meta["score"]), out))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in scored[: max(1, top_k)]]


def _build_bm25(kb_name: str, docs: List[Document], top_k: int):
    try:
        from langchain_community.retrievers import BM25Retriever
    except Exception as e:  # noqa: BLE001
        logger.warning("langchain_community.BM25Retriever 不可用：%r", e)
        return None
    try:
        import jieba
        bm25 = BM25Retriever.from_documents(
            docs,
            preprocess_func=jieba.lcut_for_search,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("BM25 使用 jieba 失败，回退空格分词：%r", e)
        bm25 = BM25Retriever.from_documents(docs)
    bm25.k = top_k
    return bm25


def _get_bm25(kb_name: str, docs: List[Document], top_k: int, docs_version: int):
    """按 docs_version 做缓存；版本号不同时重建。"""
    with _BM25_LOCK:
        cached = _BM25_CACHE.get(kb_name)
        if cached is not None and cached[1] == docs_version:
            try:
                cached[0].k = top_k
            except Exception:  # noqa: BLE001
                pass
            return cached[0]
        bm25 = _build_bm25(kb_name, docs, top_k)
        if bm25 is not None:
            _BM25_CACHE[kb_name] = (bm25, docs_version)
        return bm25


def invalidate_bm25_cache(kb_name: str = "") -> None:
    """KB 有文件新增 / 删除时调用，清除 BM25 索引缓存。"""
    with _BM25_LOCK:
        if not kb_name:
            _BM25_CACHE.clear()
        else:
            _BM25_CACHE.pop(kb_name, None)


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------

def _rerank(query: str, docs: List[Document], top_n: int) -> List[Document]:
    if not docs or top_n <= 0:
        return docs[:top_n] if top_n > 0 else []
    ce = _get_reranker()
    if ce is None:
        return docs[:top_n]
    try:
        pairs = [[query, (d.page_content or "")[:4000]] for d in docs]
        scores = ce.predict(pairs, batch_size=16, convert_to_numpy=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("rerank predict 失败，fail-open 保留原顺序：%r", e)
        return docs[:top_n]
    scored = sorted(
        zip(scores, docs), key=lambda x: float(x[0]), reverse=True,
    )
    out: List[Document] = []
    for sc, d in scored[:top_n]:
        d.metadata["rerank_score"] = float(sc)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# 上下文扩展（轻量版 Parent-Child）：把命中 chunk 的前后邻居合并到 page_content
# ---------------------------------------------------------------------------

def _expand_context(
    docs: List[Document],
    all_docs: List[Document],
    neighbors: int,
) -> List[Document]:
    if neighbors <= 0 or not docs:
        return docs
    # 按 (source, chunk_index) 建索引；没有 chunk_index 的退化为 (source, 原序号)
    by_src: Dict[str, List[Tuple[int, Document]]] = {}
    for i, d in enumerate(all_docs):
        src = str(((d.metadata or {}).get("source")) or ((d.metadata or {}).get("file_name")) or "")
        idx = int((d.metadata or {}).get("chunk_index") or i)
        by_src.setdefault(src, []).append((idx, d))
    # 排序
    for k in by_src:
        by_src[k].sort(key=lambda x: x[0])
    out: List[Document] = []
    for hit in docs:
        src = str(((hit.metadata or {}).get("source")) or ((hit.metadata or {}).get("file_name")) or "")
        bucket = by_src.get(src) or []
        if not bucket:
            out.append(hit)
            continue
        pos = -1
        for i, (_, d) in enumerate(bucket):
            if d is hit or ((d.metadata or {}).get("id") and
                            (hit.metadata or {}).get("id") == (d.metadata or {}).get("id")):
                pos = i
                break
        if pos < 0:
            out.append(hit)
            continue
        lo = max(0, pos - int(neighbors))
        hi = min(len(bucket), pos + int(neighbors) + 1)
        merged = "\n\n".join(bucket[j][1].page_content or "" for j in range(lo, hi))
        meta = dict(hit.metadata or {})
        meta["expanded"] = True
        meta["expanded_neighbors"] = int(neighbors)
        out.append(Document(page_content=merged, metadata=meta))
    return out


# ---------------------------------------------------------------------------
# 对外：hybrid_search_docs
# ---------------------------------------------------------------------------

def hybrid_search_docs(
    kb_service,
    query: str,
    top_k: int,
    score_threshold: float,
    *,
    use_hybrid: Optional[bool] = None,
    use_rerank: Optional[bool] = None,
    use_expand: Optional[bool] = None,
) -> List[Document]:
    """混合检索 + rerank + 上下文扩展统一入口。

    - kb_service:KBService 子类实例;我们从它的内部 vectorstore 拿 docstore
    - 三个 use_* 开关:None=用 settings 默认;True/False=按请求覆盖
    - 全部关闭时行为与原 do_search 等价
    """
    import time as _t
    _t0 = _t.time()
    kb = Settings.kb_settings
    if use_hybrid is None:
        use_hybrid = bool(getattr(kb, "USE_HYBRID_RETRIEVER", False))
    if use_rerank is None:
        use_rerank = bool(getattr(kb, "USE_RERANKER", False))
    if use_expand is None:
        use_expand = bool(getattr(kb, "USE_CONTEXT_EXPANSION", False))
    if use_rerank and not _reranker_preflight_available():
        use_rerank = False
    candidate_top_k = max(int(top_k), int(getattr(kb, "HYBRID_CANDIDATE_TOP_K", 20)))
    bm25_weight = float(getattr(kb, "HYBRID_BM25_WEIGHT", 0.4) or 0.4)
    expand_neighbors = int(getattr(kb, "CONTEXT_EXPANSION_NEIGHBORS", 1) or 1)

    # 懒加载 metric 指针（避免模块间循环导入延迟）
    try:
        from chayuan.server.observability.ks_metrics import (
            RAG_HYBRID_LATENCY, RAG_RERANK_LATENCY, RAG_CANDIDATE_POOL,
        )
    except Exception:  # noqa: BLE001
        RAG_HYBRID_LATENCY = RAG_RERANK_LATENCY = RAG_CANDIDATE_POOL = None

    # 纯透传：与旧 do_search 行为完全一致
    if not (use_hybrid or use_rerank or use_expand):
        return kb_service.do_search(query, top_k, score_threshold)

    # 1) 向量召回（候选池放大）
    try:
        vector_docs = kb_service.do_search(query, candidate_top_k, score_threshold)
    except Exception as e:  # noqa: BLE001
        logger.warning("vector_docs 召回失败，走 fallback：%r", e)
        vector_docs = []

    keyword_docs = lexical_search_docs(kb_service, query, candidate_top_k)
    hybrid_candidates: List[Document] = list(keyword_docs or []) + list(vector_docs)

    # 2) 可选 BM25 融合。不依赖 LangChain EnsembleRetriever,避免版本路径差异。
    if use_hybrid:
        try:
            bm25 = _maybe_bm25_for_kb(kb_service, candidate_top_k)
            if bm25 is not None:
                from chayuan.server.file_rag.retrieval_pipeline.fuser import (
                    dedup_by_chunk_key, rrf_fuse,
                )
                from chayuan.server.file_rag.retriever_compat import retriever_get_documents

                bm25_docs = retriever_get_documents(bm25, query) or []
                for d in bm25_docs:
                    try:
                        d.metadata = {**(d.metadata or {}), "retrieval_path": "bm25"}
                    except Exception:  # noqa: BLE001
                        pass
                for d in vector_docs:
                    try:
                        d.metadata = {**(d.metadata or {}), "retrieval_path": "vector"}
                    except Exception:  # noqa: BLE001
                        pass
                for d in keyword_docs:
                    try:
                        d.metadata = {**(d.metadata or {}), "retrieval_path": "keyword"}
                    except Exception:  # noqa: BLE001
                        pass
                hybrid_candidates = dedup_by_chunk_key(
                    rrf_fuse(
                        [keyword_docs, bm25_docs, vector_docs],
                        weights=[1.3, bm25_weight, 1.0 - bm25_weight],
                        top_n=candidate_top_k,
                    )
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("BM25 融合失败，回退纯向量：%r", e)

    # 去重：按 (source, id/page_content hash) 幂等
    dedup: Dict[str, Document] = {}
    for d in hybrid_candidates:
        key = str((d.metadata or {}).get("id")) or (d.page_content or "")[:80]
        dedup.setdefault(key, d)
    hybrid_candidates = list(dedup.values())

    # 观测：候选池大小
    try:
        if RAG_CANDIDATE_POOL is not None:
            RAG_CANDIDATE_POOL.observe(float(len(hybrid_candidates)))
    except Exception:  # noqa: BLE001
        pass

    # 2.4) Retriever 插件（ColBERT / 自定义）：每个可用插件贡献一批候选
    try:
        from chayuan.server.shared.retriever_plugins import active_plugins
        for plugin in active_plugins():
            try:
                extra_docs = plugin.retrieve(query, kb_service, top_k=candidate_top_k)
                if extra_docs:
                    hybrid_candidates = extra_docs + hybrid_candidates
            except Exception as e:  # noqa: BLE001
                logger.debug("retriever plugin %s 失败：%r", plugin.name, e)
    except Exception:  # noqa: BLE001
        pass

    # 2.5) RAPTOR 多层级配额平衡：仅在 build 过 RAPTOR 且全局开关 ON 时生效
    if bool(getattr(kb, "USE_RAPTOR", False)):
        try:
            from chayuan.server.file_rag.raptor.retriever import (
                balance_raptor_levels,
            )
            # 候选里若存在 raptor_level>=1 的摘要，自动做配额平衡
            if any(int((d.metadata or {}).get("raptor_level") or 0) >= 1
                   for d in hybrid_candidates):
                hybrid_candidates = balance_raptor_levels(
                    hybrid_candidates, top_k=candidate_top_k,
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("balance_raptor_levels 失败（忽略）：%r", e)

    # 2.6) GraphRAG 社区摘要召回（若开启）
    if bool(getattr(kb, "USE_GRAPHRAG", False)):
        try:
            from chayuan.server.file_rag.graphrag.retriever import (
                graphrag_augment,
            )
            extra = graphrag_augment(kb_service=kb_service, query=query,
                                      top_k=max(3, top_k))
            if extra:
                hybrid_candidates = extra + hybrid_candidates
        except Exception as e:  # noqa: BLE001
            logger.debug("GraphRAG 增强失败（忽略）：%r", e)

    # 3) rerank（独立计时）
    if use_rerank:
        _rt0 = _t.time()
        try:
            reranked = _rerank(query, hybrid_candidates, top_n=top_k)
            _r_status = "ok"
        except Exception:
            reranked = hybrid_candidates[:top_k]
            _r_status = "error"
        try:
            if RAG_RERANK_LATENCY is not None:
                RAG_RERANK_LATENCY.labels(status=_r_status).observe(_t.time() - _rt0)
        except Exception:  # noqa: BLE001
            pass
    else:
        reranked = hybrid_candidates[:top_k]

    # 4) 上下文扩展：需要全量 docs 做邻居定位
    if use_expand:
        try:
            all_docs = _all_docs_of_kb(kb_service)
            reranked = _expand_context(reranked, all_docs, expand_neighbors)
        except Exception as e:  # noqa: BLE001
            logger.debug("上下文扩展失败（忽略）：%r", e)

    # 总体 hybrid 耗时
    try:
        if RAG_HYBRID_LATENCY is not None:
            RAG_HYBRID_LATENCY.labels(status="ok").observe(_t.time() - _t0)
    except Exception:  # noqa: BLE001
        pass

    return reranked[:top_k]


# ---------------------------------------------------------------------------
# 工具函数：从 KBService 取到底层 vectorstore / 全量 docs
# ---------------------------------------------------------------------------

def _get_vector_store(kb_service):
    """兼容 FAISS / Milvus / Chroma / PG 等不同 KBService 子类的 vectorstore 获取。"""
    # FAISSKBService 内部通常是 ``_load_vector_store`` 方法或 ``vs`` 属性
    for attr in ("vs", "vector_store", "_vs", "_vectorstore"):
        v = getattr(kb_service, attr, None)
        if v is not None:
            return v
    # 尝试 load
    for meth in ("load_vector_store", "_load_vector_store"):
        fn = getattr(kb_service, meth, None)
        if callable(fn):
            try:
                v = fn()
                if v is not None:
                    return v
            except Exception:  # noqa: BLE001
                continue
    return None


def _all_docs_of_kb(kb_service) -> List[Document]:
    """尽量从底层取全量 Document；失败返回空列表。"""
    vs = _get_vector_store(kb_service)
    if vs is None:
        return []
    # FAISS 的 docstore
    try:
        ds = getattr(vs, "docstore", None)
        inner = getattr(ds, "_dict", None) if ds is not None else None
        if isinstance(inner, dict) and inner:
            return list(inner.values())
    except Exception:  # noqa: BLE001
        pass
    # Chroma / 其它：退而求其次，用 similarity_search 大 k 拉一批作为近似
    try:
        # 只做一次（调用方已对 BM25 做了 per-KB 缓存）
        return vs.similarity_search("", k=10000)
    except Exception:  # noqa: BLE001
        return []


def _maybe_bm25_for_kb(kb_service, top_k: int):
    kb_name = getattr(kb_service, "kb_name", "")
    docs = _all_docs_of_kb(kb_service)
    if not docs:
        return None
    # docs_version：简单用 len(docs) 做签名；文件增减就会失配 → 重建
    version = len(docs)
    return _get_bm25(kb_name, docs, top_k, version)


def _as_vector_retriever(kb_service, top_k: int, score_threshold: float):
    vs = _get_vector_store(kb_service)
    if vs is None:
        return None
    try:
        return vs.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"score_threshold": score_threshold, "k": top_k},
        )
    except Exception:  # noqa: BLE001
        try:
            return vs.as_retriever(search_kwargs={"k": top_k})
        except Exception as e:  # noqa: BLE001
            logger.debug("vectorstore.as_retriever 失败：%r", e)
            return None
