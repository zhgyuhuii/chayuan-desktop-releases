"""Hybrid search 进度封装 + 多路召回(title / section)+ HyDE 重写。

为对接 Block B 反馈 16 的"折叠进度卡片"UX,本模块提供一个**异步生成器**:
  - 输入 query + kb_name + 控制开关
  - 顺序产出 SSE-like JSON 帧:plan / route_start / route_done / fuse / rerank /
    summary / results / done

实现要点:
  - 复用现有 `hybrid_search_docs`(向量+BM25+rerank+expand);本模块只负责"为
    UI 拆分进度阶段并附加 title / section 两条轻量路"。
  - title / section 不进 vector store,而是在 KB 全量 docs 上做 Python 内循环
    匹配:小型 KB 上够用;大型 KB 后续可换 PG `to_tsvector` 索引(成本最小,
    工程量见 Block B §3)。
  - HyDE 改写:用当前 LLM 把 query → "假设答案",编码后并入向量召回。可在
    Settings.kb_settings.USE_HYDE 控制;失败 fail-open。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from langchain_core.documents import Document

from chayuan.settings import Settings
from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory

logger = logging.getLogger("chayuan.file_rag.hybrid_progress")


# ---------------------------------------------------------------------------
# 轻量 title / section 路:在 KB 全量 docs 上做线性扫描
# ---------------------------------------------------------------------------

_TOKEN_SPLIT = re.compile(r"[\s,。、,;;:：()()\[\]【】「」<>《》/\\\-_+=*&^%$#@!~`'\"?？]+")


def _tokenize_zh(text: str) -> List[str]:
    """轻量中英分词:不强制依赖 jieba,失败时退化为正则切。"""
    if not text:
        return []
    try:
        import jieba  # type: ignore
        return [t for t in jieba.lcut_for_search(text) if t and not t.isspace()]
    except Exception:  # noqa: BLE001
        return [t for t in _TOKEN_SPLIT.split(text) if t]


def _all_docs_safely(kb_service) -> List[Document]:
    """从 hybrid_service 复用的 _all_docs_of_kb,失败返回空列表。"""
    try:
        from chayuan.server.file_rag.hybrid_service import _all_docs_of_kb
        return _all_docs_of_kb(kb_service) or []
    except Exception as e:  # noqa: BLE001
        logger.debug("拉取 KB 全量 docs 失败: %r", e)
        return []


def _score_field_match(query_tokens: List[str], field_text: str) -> float:
    """简单覆盖率 score: 命中 token 数 / 查询 token 数。0~1。"""
    if not query_tokens or not field_text:
        return 0.0
    field_lower = field_text.lower()
    hits = sum(1 for t in query_tokens if t.lower() in field_lower)
    return hits / max(1, len(query_tokens))


def title_search(kb_service, query: str, top_k: int) -> List[Document]:
    """文件名 / 文档标题路。命中权重高 — 用户问'X 文档'时直击。"""
    docs = _all_docs_safely(kb_service)
    if not docs:
        return []
    tokens = _tokenize_zh(query)
    scored: List[tuple[float, Document]] = []
    seen_keys: set[str] = set()
    for d in docs:
        meta = d.metadata or {}
        title = str(meta.get("title") or meta.get("file_name") or meta.get("source") or "")
        if not title:
            continue
        s = _score_field_match(tokens, title)
        if s <= 0:
            continue
        # 同名文件去重:同一文件取一条 chunk(随机先到先得,精度上够用)
        key = title
        if key in seen_keys:
            continue
        seen_keys.add(key)
        nd = Document(page_content=d.page_content, metadata={**meta, "route": "title", "route_score": s})
        scored.append((s, nd))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:top_k]]


def section_search(kb_service, query: str, top_k: int) -> List[Document]:
    """章节 / heading 路。命中 metadata.section_path / heading_path。"""
    docs = _all_docs_safely(kb_service)
    if not docs:
        return []
    tokens = _tokenize_zh(query)
    scored: List[tuple[float, Document]] = []
    for d in docs:
        meta = d.metadata or {}
        section = meta.get("section_path") or meta.get("heading_path") or meta.get("heading")
        if not section:
            continue
        text = " / ".join(section) if isinstance(section, list) else str(section)
        s = _score_field_match(tokens, text)
        if s <= 0:
            continue
        nd = Document(page_content=d.page_content, metadata={**meta, "route": "section", "route_score": s})
        scored.append((s, nd))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:top_k]]


# ---------------------------------------------------------------------------
# RRF 融合
# ---------------------------------------------------------------------------

def rrf_fuse(
    results_by_route: Dict[str, List[Document]],
    weights: Dict[str, float],
    k: int = 60,
) -> List[Document]:
    """Reciprocal Rank Fusion: score = Σ w_route / (k + rank_in_route)."""
    scores: Dict[str, float] = {}
    chunk_by_id: Dict[str, Document] = {}
    for route, results in results_by_route.items():
        w = weights.get(route, 1.0)
        for rank, chunk in enumerate(results):
            cid = str((chunk.metadata or {}).get("id") or (chunk.page_content or "")[:80])
            chunk_by_id[cid] = chunk
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank + 1)
    ordered = sorted(scores.items(), key=lambda x: -x[1])
    return [chunk_by_id[cid] for cid, _ in ordered]


# ---------------------------------------------------------------------------
# HyDE 改写
# ---------------------------------------------------------------------------

def _maybe_hyde_rewrite(query: str) -> Optional[str]:
    """HyDE: 让小模型生成一段"假设的答案",再用此文本去召回向量。
    失败 / 关闭返回 None,调用方走原 query。
    """
    if not bool(getattr(Settings.kb_settings, "USE_HYDE", False)):
        return None
    try:
        from chayuan.server.utils import get_default_llm
        llm_name = get_default_llm()
        if not llm_name:
            return None
        from chayuan.server.api_server.openai_routes import _chat_completion_sync  # type: ignore
        # 极简提示:让模型不答'我不知道',强制写一段答案
        prompt = (
            "你是一个写作助理。请根据下面的问题写出一段简短的、像答案一样的中文段落"
            "(2-4 句),用作向量检索的索引。不要说'我不知道'。\n\n问题: " + query
        )
        # 没有现成同步 helper 的项目可改成 langchain LLM 直调;失败 fail-open
        from langchain_core.messages import HumanMessage
        from chayuan.server.utils import get_ChatOpenAI
        llm = get_ChatOpenAI(model_name=llm_name, temperature=0.3, streaming=False)
        resp = llm.invoke([HumanMessage(content=prompt)])
        text = getattr(resp, "content", "") or str(resp)
        text = (text or "").strip()
        return text or None
    except Exception as e:  # noqa: BLE001
        logger.debug("HyDE 重写失败,fail-open: %r", e)
        return None


# ---------------------------------------------------------------------------
# 进度生成器
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: Dict[str, float] = {
    "vector": 1.0,
    "bm25": 0.8,
    "title": 1.5,
    "section": 1.2,
}


async def hybrid_search_with_progress(
    *,
    query: str,
    knowledge_base_name: str,
    top_k: int = 6,
    score_threshold: float = 0.3,
    rerank: bool = True,
    weights: Optional[Dict[str, float]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """异步生成器:产出 SSE 帧字典。调用方 wrap 成 EventSourceResponse 即可。"""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    routes = ["vector", "bm25", "title", "section"]
    yield {"type": "plan", "intent": "查询", "routes": routes}

    kb = KBServiceFactory.get_service_by_name(knowledge_base_name)
    if kb is None:
        yield {"type": "error", "message": f"未找到知识库 {knowledge_base_name}"}
        yield {"type": "done"}
        return

    # 0) 可选 HyDE 改写(覆盖 query 用于 vector 路;BM25 / title / section 仍用原 query)
    hyde_query: Optional[str] = None
    if bool(getattr(Settings.kb_settings, "USE_HYDE", False)):
        hyde_query = _maybe_hyde_rewrite(query)
        if hyde_query:
            yield {"type": "hyde", "rewritten": hyde_query[:200]}

    # 1) 并行跑 4 路召回
    candidate_top_k = max(top_k * 3, 12)

    async def _run_route(name: str, fn) -> List[Document]:
        t0 = time.time()
        yield_start = {"type": "route_start", "route": name}
        try:
            res = await asyncio.to_thread(fn)
        except Exception as e:  # noqa: BLE001
            logger.warning("路 %s 失败: %r", name, e)
            res = []
        dt = int((time.time() - t0) * 1000)
        return res, yield_start, {"type": "route_done", "route": name, "count": len(res or []), "duration_ms": dt}

    # vector 路:走纯向量(不开 hybrid 内的 BM25,避免重复计算)
    def _do_vector():
        return kb.search_docs(
            hyde_query or query, candidate_top_k, score_threshold,
            use_hybrid=False, use_rerank=False,
        ) or []

    def _do_bm25():
        # 复用 hybrid_search_docs 的 BM25 路:打开 hybrid 但关 rerank/expand;
        # 它内部会做 BM25+vector ensemble,这里只取 BM25 部分太复杂 — 简化策略:
        # 直接调内部 _maybe_bm25_for_kb 单跑。
        try:
            from chayuan.server.file_rag.hybrid_service import _maybe_bm25_for_kb
            from chayuan.server.file_rag.retriever_compat import retriever_get_documents
            bm25 = _maybe_bm25_for_kb(kb, candidate_top_k)
            if bm25 is None:
                return []
            return retriever_get_documents(bm25, query) or []
        except Exception as e:  # noqa: BLE001
            logger.debug("BM25 单路失败: %r", e)
            return []

    def _do_title():
        return title_search(kb, query, candidate_top_k)

    def _do_section():
        return section_search(kb, query, candidate_top_k)

    # 顺序发 route_start,然后异步 gather 所有路;每路完成时发 route_done。
    # 简化:并发跑,所有结果回来后一次性发 route_done(用户看到顺序齐发)。
    yield {"type": "route_start", "route": "vector"}
    yield {"type": "route_start", "route": "bm25"}
    yield {"type": "route_start", "route": "title"}
    yield {"type": "route_start", "route": "section"}

    t0_routes = time.time()
    results = await asyncio.gather(
        asyncio.to_thread(_do_vector),
        asyncio.to_thread(_do_bm25),
        asyncio.to_thread(_do_title),
        asyncio.to_thread(_do_section),
        return_exceptions=True,
    )
    dt_routes = int((time.time() - t0_routes) * 1000)

    by_route: Dict[str, List[Document]] = {}
    for name, res in zip(routes, results):
        if isinstance(res, Exception):
            logger.warning("路 %s 抛错: %r", name, res)
            res_list: List[Document] = []
        else:
            res_list = list(res or [])
        by_route[name] = res_list
        yield {"type": "route_done", "route": name, "count": len(res_list), "duration_ms": dt_routes}

    # 2) RRF 融合
    fused = rrf_fuse(by_route, weights)
    yield {"type": "fuse", "total_unique": len(fused)}

    # 3) Rerank
    final = fused[: top_k * 3]
    if rerank and final:
        t0_r = time.time()
        try:
            from chayuan.server.file_rag.hybrid_service import _rerank
            final = _rerank(query, final, top_k * 3)
        except Exception as e:  # noqa: BLE001
            logger.debug("rerank 失败,跳过: %r", e)
        yield {"type": "rerank", "top_k": top_k, "duration_ms": int((time.time() - t0_r) * 1000)}

    final = final[:top_k]

    # 4) 总结(轻量:列出文件名 + 段落数)
    files: Dict[str, int] = {}
    for d in final:
        f = str((d.metadata or {}).get("file_name") or (d.metadata or {}).get("source") or "")
        if f:
            files[f] = files.get(f, 0) + 1
    summary_parts = [f"《{name}》{cnt} 条" for name, cnt in files.items()]
    yield {
        "type": "summary",
        "summary": f"找到 {len(final)} 条相关内容;主要来自 {', '.join(summary_parts) or '多个文件'}",
    }

    # 5) 输出 chunks
    chunks = []
    for d in final:
        meta = d.metadata or {}
        chunks.append({
            "id": meta.get("id"),
            "content": d.page_content,
            "file_name": meta.get("file_name") or meta.get("source"),
            "title": meta.get("title"),
            "section_path": meta.get("section_path") or meta.get("heading_path"),
            "page": meta.get("page"),
            "char_offset_start": meta.get("char_offset_start"),
            "char_offset_end": meta.get("char_offset_end"),
            "rerank_score": meta.get("rerank_score"),
            "route_score": meta.get("route_score"),
        })
    yield {"type": "results", "chunks": chunks}
    yield {"type": "done"}
