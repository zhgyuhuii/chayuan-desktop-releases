"""KB / 多源 知识检索 handler。

v2:从"单 KB(kb_name)向量检索"扩展为"多源(ku_ids)聚合检索"。

链路
----
- ``ChatRequest.ku_ids`` 非空 → 走 ``MultiSourceRetriever``:
    并发 ``_process_one_ku``(KU 路由的同款 dispatch),拿回每个 ku_id 的结果块,
    按 kind 转 chunk 喂入 LLM context。
- 否则回落到 legacy ``kb_name`` / ``kb_names`` 单/多 doc KB 路径,
  直接 ``search_docs``。

为什么不直接调 ``/knowledge_universe/ask`` HTTP:
  HTTP 自调用要带鉴权 + 多一层序列化;直接 import 函数复用同进程更快、错误更清晰。

并发
----
- 每个 ku_id 独立线程跑,默认上限由 ``KB_RETRIEVER_MAX_WORKERS`` 控制(默认 4),
  防止用户一次选 10+ KB 时压死 embedding 服务。
- 单 ku_id 内的 query rewrite 多 query 已在 ``_process_one_ku`` 内并发,这里不再重复。
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from fastapi.concurrency import run_in_threadpool

from chayuan.server.chat.handlers.base import BaseModeHandler, register_handler
from chayuan.settings import Settings

logger = logging.getLogger("chayuan.chat.handlers.kb")


# ---------------------------------------------------------------------------
# threshold normalize(双语义自适应,见历史 BUG 注释)
# ---------------------------------------------------------------------------

def _normalize_threshold(raw: float) -> float:
    """ChatRequest.score_threshold(默认 2.0,L2 老语义)→ KB 实际能消化的归一化值。

    经 LangChain similarity_score_threshold 是 [0,1] 归一化语义;
    > 1 视作"未指定",回落到配置默认 SCORE_THRESHOLD。
    """
    th = float(raw or 0)
    if th > 1.0:
        return float(Settings.kb_settings.SCORE_THRESHOLD)
    if th < 0.0:
        return 0.0
    return th


# ---------------------------------------------------------------------------
# helpers:从 KU block 转 chunk(对齐 _doc_to_chunk 的字段约定)
# ---------------------------------------------------------------------------

def _doc_block_to_chunks(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    """document kind 的 block.results(envelope 列表)→ chunk 列表。"""
    out: List[Dict[str, Any]] = []
    kb_name = (block.get("ku_id") or "").removeprefix("doc:")
    for w in block.get("results") or []:
        # result_envelope.to_wire 返回 content/snippet/source 等扁平字段;
        # 旧版才有 page_content/metadata。两种都兼容,避免 LLM 拿到空上下文。
        text = w.get("content") or w.get("page_content") or w.get("text") or w.get("snippet") or ""
        meta = dict(w.get("metadata") or {})
        if not meta:
            meta = {
                "source": w.get("source") or w.get("file_name") or "",
                "file_name": w.get("file_name") or "",
                "page": w.get("page"),
                "chunk_index": w.get("chunk_index"),
                "retrieval_path": w.get("retrieval_path"),
            }
        meta.setdefault("source", w.get("source") or w.get("file_name") or "")
        meta.setdefault("file_name", os.path.basename(str(w.get("file_name") or meta.get("source") or "")))
        out.append({
            "content": text,
            "score": float(w.get("score") or 0.0),
            "source_kind": "vector",
            "citation": {
                "title": meta.get("source") or kb_name,
                "source_kind": "vector",
                "meta": {k: str(v)[:200] for k, v in meta.items() if k != "vector"},
            },
        })
    return out


def _structured_block_to_chunks(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    """structured kind 的 NL→SQL 结果 → 单条上下文 chunk(把 SQL+rows 拼成纯文本喂 LLM)。

    LLM 不能直接吃表格,所以把 columns/rows 序列化成简洁的 markdown table。
    summary 和 sql 也带上,方便 LLM 引用。
    """
    src_id = (block.get("ku_id") or "").removeprefix("src:")
    sql = (block.get("sql") or "").strip()
    summary = (block.get("summary") or "").strip()
    rows = block.get("rows") or []
    columns = block.get("columns") or []
    text_full = (block.get("text") or "").strip()

    parts: List[str] = []
    if summary:
        parts.append(f"【摘要】{summary}")
    if sql:
        parts.append(f"【SQL】\n```sql\n{sql}\n```")
    if rows and columns:
        # 极简 markdown table,最多 30 行,字段截到 80 字
        head = "| " + " | ".join(map(str, columns)) + " |"
        sep = "| " + " | ".join("---" for _ in columns) + " |"
        body = []
        for r in rows[:30]:
            cells = []
            for c in columns:
                v = r.get(c) if isinstance(r, dict) else (r[columns.index(c)] if columns else "")
                s = str(v) if v is not None else ""
                if len(s) > 80:
                    s = s[:80] + "…"
                cells.append(s.replace("|", "\\|"))
            body.append("| " + " | ".join(cells) + " |")
        parts.append("【数据】\n" + "\n".join([head, sep, *body]))
    if not parts and text_full:
        # NL→SQL 没拆出结构化,fallback 用原文
        parts.append(text_full)

    text = "\n\n".join(parts)
    if not text:
        return []
    return [{
        "content": text,
        "score": 1.0,
        "source_kind": "structured",
        "citation": {
            "title": f"数据库 #{src_id}",
            "source_kind": "structured",
            "meta": {"source": f"src:{src_id}", "kind": "structured",
                     "sql": (sql or "")[:200]},
        },
    }]


def _image_block_to_chunks(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    """image kind 的 results → 每张图一条 chunk。

    用户反馈:对话里选了图像 KB 检索,顶部来源附件折叠条没有把命中的图当成附件
    展示。根因是早期实现把所有 caption 拼成单条 umbrella chunk,丢掉了
    per-image 的 image_id / preview_url / download_url。

    新实现:每张命中图 1 个 chunk + 1 条引用,citation 带 kb_name + file_name,
    上层 ``_image_block_to_file_sources`` 会按 file_name 配对回 chunks,前端
    ``chatCitationsToSources`` 据 source_kind=image 渲缩略图 + 下载按钮。
    """
    src_id = (block.get("ku_id") or "").removeprefix("src:")
    kb_label = block.get("title") or f"图库 #{src_id}"
    items = block.get("results") or []
    if not items:
        return []
    out: List[Dict[str, Any]] = []
    for it in items[:20]:
        cap = (it.get("caption") or "").strip()
        score = it.get("score")
        img_id = str(it.get("id") or it.get("path") or "").strip()
        # file_name 用 image_id —— 稳定 + 与下文 _image_block_to_file_sources 配对
        file_name = img_id or (it.get("title") or "未命名图像")
        title = it.get("title") or cap[:60] or file_name
        preview_url = it.get("preview_url") or ""
        download_url = it.get("download_url") or ""
        # LLM 上下文文本:用户希望"被检索出来"的图标识 + caption
        content_lines = [f"【图像】{title}"]
        if cap and cap != title:
            content_lines.append(cap[:300])
        if isinstance(score, (int, float)):
            content_lines.append(f"相似度 {score:.2f}")
        out.append({
            "content": "\n".join(content_lines),
            "score": float(score or 0.0),
            "source_kind": "image",
            # source 用 file_name —— sse-parser 按 file_name/title 把 chunks 配对到 sources
            "source": file_name,
            "file_name": file_name,
            "citation": {
                "title": title,
                "source_kind": "image",
                "kb_name": kb_label,
                "file_name": file_name,
                "meta": {
                    # sse-parser 用 meta.file_name 把 chunks 配对回 sources(优先级
                    # meta.file_name → meta.source → citation.title);两边都用
                    # img_id,才能让前端附件折叠条对得上
                    "file_name": file_name,
                    "source": f"src:{src_id}",
                    "kind": "image",
                    "image_id": img_id,
                    "preview_url": preview_url,
                    "download_url": download_url,
                    "caption": cap,
                },
            },
        })
    return out


def _image_block_to_file_sources(block: Dict[str, Any], start_index: int) -> List[Dict[str, Any]]:
    """image block.results → 每张命中图一条 source(对应顶部"来源附件折叠条"一条)。

    设计跟 ``_document_block_to_file_sources`` 同形:每个 source 带 kb_name
    +file_name+source_kind=image+url(preview_url),前端 chat SSE 解析后,
    `chatCitationsToSources` 按 (kb_name, file_name) 聚合 → Source/Group,
    一张图就是一个可下载/可预览的附件 chip。
    """
    src_id = (block.get("ku_id") or "").removeprefix("src:")
    kb_label = block.get("title") or f"图库 #{src_id}"
    items = block.get("results") or []
    out: List[Dict[str, Any]] = []
    for idx, it in enumerate(items[:20]):
        img_id = str(it.get("id") or it.get("path") or "").strip()
        if not img_id:
            continue
        cap = (it.get("caption") or "").strip()
        title = it.get("title") or cap[:60] or img_id
        out.append({
            "kb_name": kb_label,
            "file_name": img_id,
            "title": title,
            "url": it.get("preview_url") or "",
            "snippet": cap[:200],
            "score": float(it.get("score") or 0.0),
            "chunk_count": 1,
            "source_kind": "image",
            "cite_index": start_index + idx,
            "meta": {
                "source": f"src:{src_id}",
                "kind": "image",
                "image_id": img_id,
                "preview_url": it.get("preview_url") or "",
                "download_url": it.get("download_url") or "",
            },
        })
    return out


def _block_to_sources(block: Dict[str, Any], cite_index: int) -> Optional[Dict[str, Any]]:
    """一个 ku_id 出一条引用元(右栏 chip);doc kind 已展开成多条 file 的话由调用方再拆。"""
    kind = block.get("kind") or "document"
    title = block.get("title") or block.get("ku_id") or ""
    score = 0.0
    chunk_count = 0
    if kind == "document":
        for w in block.get("results") or []:
            chunk_count += 1
            score = max(score, float(w.get("score") or 0.0))
    elif kind == "structured":
        chunk_count = len(block.get("rows") or [])
        score = 1.0
    elif kind == "image":
        chunk_count = len(block.get("results") or [])
        score = 1.0
    if chunk_count == 0 and not block.get("ok"):
        return None
    return {
        "ku_id": block.get("ku_id"),
        "kind": kind,
        "title": title,
        "score": score,
        "chunk_count": chunk_count,
        "source_kind": kind,
        "cite_index": cite_index,
        "error": block.get("error") if not block.get("ok") else None,
        "diagnostic": block.get("diagnostic"),
    }


def _document_block_to_file_sources(block: Dict[str, Any], start_index: int) -> List[Dict[str, Any]]:
    """document block.results → 按文件聚合的引用来源。

    前端引用依据需要具体文件名才能展示原文片段并提供下载按钮。旧实现把一个
    document KB 压成一条来源,会丢失 file_name,导致 UI 只能看到知识库级引用。
    """
    kb_name = (block.get("ku_id") or "").removeprefix("doc:")
    bucket: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for w in block.get("results") or []:
        meta = dict(w.get("metadata") or {})
        raw_name = (
            w.get("file_name")
            or w.get("source")
            or meta.get("file_name")
            or meta.get("source")
            or ""
        )
        file_name = os.path.basename(str(raw_name).strip())
        if not file_name:
            continue
        score = float(w.get("score") or 0.0)
        entry = bucket.get(file_name)
        if entry is None:
            entry = {
                "kb_name": kb_name,
                "file_name": file_name,
                "title": file_name,
                "score": score,
                "chunk_count": 1,
                "source_kind": "kb",
                "cite_index": start_index + len(order),
            }
            bucket[file_name] = entry
            order.append(file_name)
        else:
            entry["chunk_count"] += 1
            if score > float(entry.get("score") or 0.0):
                entry["score"] = score
    return [bucket[name] for name in order]


# ---------------------------------------------------------------------------
# Multi-source retrieval
# ---------------------------------------------------------------------------

def _max_workers() -> int:
    raw = os.getenv("KB_RETRIEVER_MAX_WORKERS", "")
    try:
        n = int(raw) if raw else 4
    except ValueError:
        n = 4
    return max(1, min(n, 16))


def _process_ku_safe(
    ku_id: str, query: str, top_k: int, *,
    use_hybrid: Optional[bool], use_rerank: Optional[bool],
    rewrite_strategy: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """import 在函数内,避免 module 级循环引用(KU routes 反向 import handlers)。"""
    from chayuan.server.api_server.knowledge_universe_routes import _process_one_ku
    try:
        return _process_one_ku(
            ku_id, query, top_k,
            use_hybrid=use_hybrid, use_rerank=use_rerank,
            rewrite_strategy=rewrite_strategy, model=model,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("kb handler: _process_one_ku %s 失败: %r", ku_id, e)
        return {"ku_id": ku_id, "ok": False, "error": str(e)}


class MultiSourceRetriever:
    """多 ku_id 并发检索 + 转 chunk + 转 sources_meta。"""

    def __init__(self, ku_ids: List[str], query: str, top_k: int,
                 use_hybrid: Optional[bool] = None, use_rerank: Optional[bool] = None,
                 rewrite_strategy: Optional[str] = None, model: Optional[str] = None):
        self.ku_ids = list(ku_ids)
        self.query = query
        self.top_k = max(1, top_k)
        self.use_hybrid = use_hybrid
        self.use_rerank = use_rerank
        self.rewrite_strategy = rewrite_strategy
        self.model = model

    def run(self) -> Dict[str, Any]:
        t0 = time.time()
        chunks: List[Dict[str, Any]] = []
        sources: List[Dict[str, Any]] = []
        if not self.ku_ids:
            return {"chunks": chunks, "sources": sources, "elapsed_ms": 0}

        with ThreadPoolExecutor(max_workers=_max_workers()) as pool:
            futures = [
                pool.submit(
                    _process_ku_safe, kid, self.query, self.top_k,
                    use_hybrid=self.use_hybrid, use_rerank=self.use_rerank,
                    rewrite_strategy=self.rewrite_strategy, model=self.model,
                )
                for kid in self.ku_ids
            ]
            blocks = [f.result() for f in futures]

        cite = 0
        for block in blocks:
            kind = block.get("kind") or ""
            if kind == "document":
                ck = _doc_block_to_chunks(block)
                doc_sources = _document_block_to_file_sources(block, cite + 1)
                sources.extend(doc_sources)
                cite += max(1, len(doc_sources))
                chunks.extend(ck)
                continue
            elif kind == "image":
                # image 与 document 同形:per-image 展开成多条 source(每张图一条
                # 可下载/可预览的附件 chip),不再压成 umbrella 单条
                ck = _image_block_to_chunks(block)
                img_sources = _image_block_to_file_sources(block, cite + 1)
                if img_sources:
                    sources.extend(img_sources)
                    cite += len(img_sources)
                    chunks.extend(ck)
                    continue
                # 命中为空时退回 umbrella source(让用户看到"这库查了但没结果")
                chunks.extend(ck)
                cite += 1
                src = _block_to_sources(block, cite)
                if src is not None:
                    sources.append(src)
                continue
            elif kind == "structured":
                ck = _structured_block_to_chunks(block)
            else:
                ck = []  # vector / 未知 / error
            chunks.extend(ck)
            cite += 1
            src = _block_to_sources(block, cite)
            if src is not None:
                sources.append(src)

        return {
            "chunks": chunks,
            "sources": sources,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }


# ---------------------------------------------------------------------------
# legacy 单 / 多 doc KB 路径(无 ku_ids 时回落)
# ---------------------------------------------------------------------------

def _legacy_doc_search(
    kb_names: List[str], query: str, top_k: int, score_threshold: float,
) -> List[Dict[str, Any]]:
    """旧路径:多个 doc KB 并发 search_docs,返回原始 doc 列表。
    后面再统一走 _doc_to_chunk 转换。
    """
    from chayuan.server.knowledge_base.kb_doc_api import search_docs
    if not kb_names:
        return []

    def _one(name: str) -> List[Any]:
        try:
            return search_docs(
                query=query, knowledge_base_name=name, top_k=top_k,
                score_threshold=score_threshold, file_name="", metadata={},
            ) or []
        except Exception as e:  # noqa: BLE001
            logger.warning("legacy doc search %s 失败: %r", name, e)
            return []

    with ThreadPoolExecutor(max_workers=_max_workers()) as pool:
        results = list(pool.map(_one, kb_names))
    flat: List[Any] = []
    for r in results:
        flat.extend(r)
    return flat


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class KBHandler(BaseModeHandler):
    mode = "kb"
    needs_retrieval = True

    async def retrieve(self, state: Dict[str, Any]) -> Dict[str, Any]:
        from chayuan.server.chat.graph.nodes import _doc_to_chunk

        req = state["request"]
        top_k = int(getattr(req, "top_k", None) or Settings.kb_settings.VECTOR_SEARCH_TOP_K)
        ku_ids: List[str] = list(getattr(req, "ku_ids", None) or [])

        # 走 ku_ids:多源聚合
        if ku_ids:
            # P2 检索模式三件套直接从 ChatRequest 透传(前端 SearchModePill 已经按档位预设)
            mr = MultiSourceRetriever(
                ku_ids=ku_ids, query=req.query, top_k=top_k,
                use_hybrid=getattr(req, "use_hybrid", None),
                use_rerank=getattr(req, "use_rerank", None),
                rewrite_strategy=getattr(req, "rewrite_strategy", None),
                model=(getattr(req, "model", "") or "").strip() or None,
            )
            res = await run_in_threadpool(mr.run)
            return {
                "retrieved_chunks": res["chunks"],
                "retrieved_sources_meta": res["sources"],
                "retrieval_elapsed_ms": res["elapsed_ms"],
            }

        # legacy doc-only 路径(单 / 多 KB);用 kb_names + kb_name 合并去重
        names: List[str] = []
        if getattr(req, "kb_names", None):
            names = [n for n in (req.kb_names or []) if n]
        if (not names) and getattr(req, "kb_name", None):
            names = [req.kb_name]
        if not names:
            return {
                "retrieved_chunks": [], "retrieved_sources_meta": [],
                "retrieval_elapsed_ms": 0,
            }

        t0 = time.time()
        docs = await run_in_threadpool(
            _legacy_doc_search,
            names, req.query, top_k, _normalize_threshold(req.score_threshold),
        )
        # 兼容原 _doc_to_chunk(它接的是单条 doc 对象);source 用第一个 KB name 作显示
        primary_kb = names[0]
        chunks = [_doc_to_chunk(d, source=f"kb:{primary_kb}") for d in (docs or [])]

        # 按文件名聚合 → sources_meta(同 v1 行为)
        bucket: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for ch in chunks:
            cite = (ch.get("citation") or {})
            meta = (cite.get("meta") or {})
            raw_name = (meta.get("source") or meta.get("file_name") or cite.get("title") or "").strip()
            if not raw_name:
                continue
            fname = os.path.basename(raw_name)
            score = float(ch.get("score") or 0.0)
            entry = bucket.get(fname)
            if entry is None:
                idx = len(bucket) + 1
                bucket[fname] = {
                    "kb_name": primary_kb,
                    "file_name": fname,
                    "title": fname,
                    "score": score,
                    "chunk_count": 1,
                    "source_kind": "kb",
                    "cite_index": idx,
                }
                order.append(fname)
            else:
                entry["chunk_count"] += 1
                if score > float(entry.get("score") or 0.0):
                    entry["score"] = score
        sources_meta = sorted(bucket.values(), key=lambda x: int(x.get("cite_index") or 0))
        return {
            "retrieved_chunks": chunks,
            "retrieved_sources_meta": sources_meta,
            "retrieval_elapsed_ms": int((time.time() - t0) * 1000),
        }


register_handler(KBHandler())
