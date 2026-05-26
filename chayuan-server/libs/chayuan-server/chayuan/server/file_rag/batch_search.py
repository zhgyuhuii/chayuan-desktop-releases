"""批量并行检索（plan v1.3 §4.1 / §4.2）。

为前端"四层漏斗"输出的 N 条短查询提供单次往返：
* 输入：``SearchBatchIn(queries=[{tag,text,weight,section_ids,top_k?}], knowledge_base_names, ...)``
* 输出：``SearchBatchOut(merged=[chunk + provenance], summary)``
* 鉴权与 ACL **不在本模块**：上游 endpoint（``kb_routes`` / ``openapi_routes``）必须先校
  验 ``can_read_kb(user, kb)``；本模块只负责执行。

为什么独立成 module（不直接写在 endpoint 里）：
* JWT 端点 ``/knowledge_base/search_batch`` 与 HMAC 端点 ``/openapi/v1/kb/search_batch``
  共享同一份业务实现，差别只在"如何拿到 user"；本模块是 single source of truth。
* SSE 流式版本（``run_batch_stream``）也复用同一份编排逻辑，只在 yield 点不一样。

实现策略：
* 为每个 (kb, query) 配一个 ``search_docs`` 调用，``asyncio.gather`` + 信号量并发；
* 用 RRF + 加权融合（无 LLM 干预）；
* 重排（CrossEncoder）一次性合并后做（不是每个 sub-query 单独 rerank，节省 90%+ 模型推理）；
* 每条 chunk 附 ``download_token``（短期 JWT，30 分钟），URL 拼装在前端。

性能上限（防滥用，超过即 422，详见 §4.1 hard limits）：
* queries.length ≤ 32
* sum(query.text.length) ≤ 24000
* knowledge_base_names.length ≤ 8
* final_top_k ≤ 50
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, field_validator

from chayuan.server.auth.download_token import sign_download

logger = logging.getLogger("chayuan.file_rag.batch_search")


# ---------------------------------------------------------------------------
# Pydantic 入参/出参
# ---------------------------------------------------------------------------

# Hard limits — 见 §4.1
MAX_QUERIES = 32
MAX_TOTAL_QUERY_CHARS = 24_000
MAX_KBS = 8
MAX_FINAL_TOP_K = 50
MAX_PER_QUERY_TOP_K = 20
DEFAULT_PER_QUERY_TOP_K = 6
DEFAULT_FINAL_TOP_K = 12


class _QueryItem(BaseModel):
    tag: str = Field(..., max_length=64,
                     description="客户端给的稳定 ID（如 c3 / c7），原样回传到 from_query_tags 用作引用锚")
    text: str = Field(..., min_length=1, max_length=4000)
    weight: float = Field(1.0, ge=0.0, le=10.0,
                          description="子查询权重，融合时与 RRF 相乘；普通 1.0，强诉求可 2-3")
    section_ids: List[str] = Field(default_factory=list, max_length=64,
                                   description="客户端文档锚（可选），原样回传到 from_section_ids；用于 verify 模式")
    top_k: Optional[int] = Field(None, ge=1, le=MAX_PER_QUERY_TOP_K)
    score_threshold: Optional[float] = Field(None, ge=0.0, le=2.0)
    file_name: str = Field("", max_length=512)


class SearchBatchIn(BaseModel):
    queries: List[_QueryItem] = Field(...,
                                      description="同一次检索请求里的所有子查询；服务端会一次并行调度")
    knowledge_base_names: List[str] = Field(...,
                                            description="本次检索的目标 KB 列表；ACL 在 endpoint 层提前过滤")
    final_top_k: int = Field(DEFAULT_FINAL_TOP_K, ge=1, le=MAX_FINAL_TOP_K)
    per_query_top_k: int = Field(DEFAULT_PER_QUERY_TOP_K, ge=1, le=MAX_PER_QUERY_TOP_K)
    score_threshold: float = Field(0.6, ge=0.0, le=2.0,
                                   description="子查询默认阈值；query 自带 score_threshold 可覆盖")
    use_hybrid: Optional[bool] = Field(None, description="None=按全局；override 整批")
    use_rerank: Optional[bool] = Field(None)
    fusion: str = Field("rrf", pattern="^(rrf|weighted)$")
    rrf_k: int = Field(60, ge=10, le=200)
    parallelism: int = Field(8, ge=1, le=32,
                             description="本次请求内子任务并发上限（信号量），不影响全局 RateLimiter")

    @field_validator("queries")
    @classmethod
    def _check_queries(cls, v):
        if len(v) > MAX_QUERIES:
            raise ValueError(f"queries.length must be <= {MAX_QUERIES}, got {len(v)}")
        if not v:
            raise ValueError("queries cannot be empty")
        total = sum(len(q.text or "") for q in v)
        if total > MAX_TOTAL_QUERY_CHARS:
            raise ValueError(f"sum(query.text.length) must be <= {MAX_TOTAL_QUERY_CHARS}, got {total}")
        # tag 唯一性（客户端责任，但服务端兜底）
        tags = [q.tag for q in v]
        if len(set(tags)) != len(tags):
            raise ValueError("queries[].tag must be unique within a request")
        return v

    @field_validator("knowledge_base_names")
    @classmethod
    def _check_kbs(cls, v):
        if len(v) > MAX_KBS:
            raise ValueError(f"knowledge_base_names.length must be <= {MAX_KBS}, got {len(v)}")
        if not v:
            raise ValueError("knowledge_base_names cannot be empty")
        # 去重
        return list(dict.fromkeys(v))


class SearchBatchSummary(BaseModel):
    queries: int
    knowledge_bases: int
    candidates_before_rerank: int = 0
    chunks_returned: int = 0
    duration_ms: int = 0
    fused_by: str = "rrf"
    failed_subqueries: int = 0
    cache_hit: bool = False


class SearchBatchOut(BaseModel):
    merged: List[Dict[str, Any]] = Field(default_factory=list)
    summary: SearchBatchSummary
    errors: List[Dict[str, Any]] = Field(default_factory=list,
                                         description="部分子查询失败的列表；不致命")


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

# 每个 (kb, query) 一个独立子任务的结果
class _SubResult:
    __slots__ = ("kb", "tag", "section_ids", "weight", "docs", "error")

    def __init__(self, kb: str, tag: str, section_ids: List[str], weight: float,
                 docs: List[Dict[str, Any]], error: Optional[str] = None) -> None:
        self.kb = kb
        self.tag = tag
        self.section_ids = section_ids or []
        self.weight = weight
        self.docs = docs or []
        self.error = error


def _doc_chunk_id(doc: Dict[str, Any]) -> str:
    """从 search_docs 返回的 dict 上提取稳定的 chunk_id（dedup key）。"""
    if "id" in doc and doc["id"]:
        return str(doc["id"])
    md = doc.get("metadata") or {}
    cid = md.get("id") or md.get("chunk_id")
    if cid:
        return str(cid)
    # 兜底：用 (kb, file, source, hash(text)) 拼一个；此分支正常不应该走
    src = md.get("source") or md.get("file_path") or ""
    page = md.get("page", "")
    text = (doc.get("page_content") or doc.get("text") or "")[:64]
    return f"_synth_{src}#{page}#{hash(text)}"


async def _run_one(
    kb: str,
    q: _QueryItem,
    body: SearchBatchIn,
    sem: asyncio.Semaphore,
) -> _SubResult:
    from chayuan.server.knowledge_base.kb_doc_api import search_docs as _search_docs

    async with sem:
        try:
            top_k = q.top_k or body.per_query_top_k
            thr = q.score_threshold if q.score_threshold is not None else body.score_threshold
            # search_docs 是同步函数；放线程池跑
            docs = await asyncio.to_thread(
                _search_docs,
                query=q.text,
                knowledge_base_name=kb,
                top_k=int(top_k),
                score_threshold=float(thr),
                file_name=q.file_name or "",
                metadata={},
                use_hybrid=body.use_hybrid,
                use_rerank=body.use_rerank,
            )
            return _SubResult(kb=kb, tag=q.tag, section_ids=list(q.section_ids),
                              weight=float(q.weight), docs=list(docs or []))
        except Exception as e:  # noqa: BLE001
            logger.warning("batch_search subquery failed kb=%s tag=%s: %r", kb, q.tag, e)
            return _SubResult(kb=kb, tag=q.tag, section_ids=list(q.section_ids),
                              weight=float(q.weight), docs=[], error=f"{type(e).__name__}: {e}")


def _fuse_rrf(subs: List[_SubResult], k: int = 60) -> List[Tuple[Dict[str, Any], float]]:
    """RRF 融合 + sub-query weight 相乘。

    返回按融合分倒序排序的 [(doc, fused_score), ...]
    """
    pool: Dict[str, Dict[str, Any]] = {}      # chunk_id -> doc
    score: Dict[str, float] = {}              # chunk_id -> fused score
    for sub in subs:
        for rank, doc in enumerate(sub.docs):
            cid = _doc_chunk_id(doc)
            pool.setdefault(cid, doc)
            inc = sub.weight * (1.0 / (k + rank + 1))
            score[cid] = score.get(cid, 0.0) + inc
    out = sorted(((pool[cid], s) for cid, s in score.items()),
                 key=lambda x: x[1], reverse=True)
    return out


def _fuse_weighted(subs: List[_SubResult]) -> List[Tuple[Dict[str, Any], float]]:
    """加权 score 融合：原始 search_docs 已经按 score 排，简单按 sub.weight * (1 - normalized_score)。

    score 越小越相关（与现有 search_docs 语义一致），所以用 (1 - s/2) 做反转。
    """
    pool: Dict[str, Dict[str, Any]] = {}
    score: Dict[str, float] = {}
    for sub in subs:
        for doc in sub.docs:
            cid = _doc_chunk_id(doc)
            pool.setdefault(cid, doc)
            raw = float(doc.get("score", 1.0) or 1.0)
            relevance = max(0.0, 1.0 - raw / 2.0)
            score[cid] = score.get(cid, 0.0) + sub.weight * relevance
    out = sorted(((pool[cid], s) for cid, s in score.items()),
                 key=lambda x: x[1], reverse=True)
    return out


def _attach_provenance(
    fused: List[Tuple[Dict[str, Any], float]],
    subs: List[_SubResult],
) -> List[Dict[str, Any]]:
    """把每个 fused chunk 标记 from_query_tags / from_section_ids（去重 set 序列化为 list）。"""
    # cid -> (tags set, section_ids set, kbs set)
    aux: Dict[str, Tuple[set, set, set]] = {}
    for sub in subs:
        for doc in sub.docs:
            cid = _doc_chunk_id(doc)
            tags, sects, kbs = aux.setdefault(cid, (set(), set(), set()))
            tags.add(sub.tag)
            sects.update(sub.section_ids)
            kbs.add(sub.kb)

    out: List[Dict[str, Any]] = []
    for doc, fscore in fused:
        cid = _doc_chunk_id(doc)
        tags, sects, kbs = aux.get(cid, (set(), set(), set()))
        md = dict(doc.get("metadata") or {})
        kb_name = md.get("kb_name") or md.get("knowledge_base_name") or (next(iter(kbs)) if kbs else "")
        file_name = md.get("source") or md.get("file_path") or md.get("file_name") or ""
        # 去掉 vector 字段（很大）
        md.pop("vector", None)
        item = {
            "chunk_id": cid,
            "kb_name": kb_name,
            "file_name": file_name,
            "score_raw": float(doc.get("score", 0.0) or 0.0),
            "score_fused": round(float(fscore), 6),
            "text": doc.get("page_content") or doc.get("text") or "",
            "metadata": md,
            "from_query_tags": sorted(tags),
            "from_section_ids": sorted(sects),
            "kbs": sorted(kbs),
        }
        out.append(item)
    return out


def _attach_download_tokens(
    chunks: List[Dict[str, Any]],
    subject_for_token: Any,
    *,
    ttl_seconds: int = 30 * 60,
) -> None:
    """就地修改：给每个 chunk 加 download_token（如果有 kb_name + file_name）。"""
    for c in chunks:
        kb = c.get("kb_name") or ""
        fn = c.get("file_name") or ""
        if not kb or not fn:
            c["download_token"] = ""
            continue
        try:
            c["download_token"] = sign_download(
                subject_for_token, kb, fn, ttl_seconds=ttl_seconds,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("sign_download failed kb=%s file=%s: %r", kb, fn, e)
            c["download_token"] = ""


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

async def run_batch(
    body: SearchBatchIn,
    *,
    user: Any = None,
    subject_for_token: Any = None,
    accessible_kbs: Optional[Sequence[str]] = None,
) -> SearchBatchOut:
    """同步版本（非 SSE）。

    - ``user`` 仅用于审计日志；ACL 应该在 endpoint 层就过滤掉了
    - ``subject_for_token`` 给 ``sign_download`` 用，签 dl token；通常 = user 或 AppSpec
    - ``accessible_kbs`` 如果传了，本函数会再做一次防御性过滤；建议 endpoint 层就过滤好
    """
    t0 = time.perf_counter()

    # 防御性过滤（避免传入未授权 KB）
    if accessible_kbs is not None:
        allowed = set(accessible_kbs)
        kbs = [kb for kb in body.knowledge_base_names if kb in allowed]
    else:
        kbs = list(body.knowledge_base_names)
    if not kbs:
        return SearchBatchOut(
            merged=[],
            summary=SearchBatchSummary(
                queries=len(body.queries), knowledge_bases=0,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                fused_by=body.fusion,
            ),
        )

    # 拓扑：每个 (kb × query) 一个 task；总数 N*M；并发上限信号量控制
    sem = asyncio.Semaphore(body.parallelism)
    tasks: List[asyncio.Task] = []
    for kb in kbs:
        for q in body.queries:
            tasks.append(asyncio.create_task(_run_one(kb, q, body, sem)))

    subs: List[_SubResult] = await asyncio.gather(*tasks)
    failed = [{"kb": s.kb, "tag": s.tag, "error": s.error} for s in subs if s.error]

    if body.fusion == "weighted":
        fused = _fuse_weighted(subs)
    else:
        fused = _fuse_rrf(subs, k=body.rrf_k)

    candidates_before_rerank = len(fused)
    # 截 top_k
    final = fused[: body.final_top_k]
    chunks = _attach_provenance(final, subs)
    if subject_for_token is not None:
        _attach_download_tokens(chunks, subject_for_token)

    return SearchBatchOut(
        merged=chunks,
        summary=SearchBatchSummary(
            queries=len(body.queries),
            knowledge_bases=len(kbs),
            candidates_before_rerank=candidates_before_rerank,
            chunks_returned=len(chunks),
            duration_ms=int((time.perf_counter() - t0) * 1000),
            fused_by=body.fusion,
            failed_subqueries=len(failed),
        ),
        errors=failed,
    )


# ---------------------------------------------------------------------------
# SSE 流式版本（plan §4.2）
# ---------------------------------------------------------------------------

async def run_batch_stream(
    body: SearchBatchIn,
    *,
    user: Any = None,
    subject_for_token: Any = None,
    accessible_kbs: Optional[Sequence[str]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """生成 SSE 帧字典（caller 负责包成 ``data: {json}\\n\\n``）。

    帧顺序（plan §4.2）：
      embed_done → query_start * N → query_done * N → fuse_done → rerank_done → results → done
      （embed_done / rerank_done 当前实现里 search_docs 内部完成，本层只发占位帧）
    """
    t0 = time.perf_counter()

    if accessible_kbs is not None:
        allowed = set(accessible_kbs)
        kbs = [kb for kb in body.knowledge_base_names if kb in allowed]
    else:
        kbs = list(body.knowledge_base_names)

    yield {"type": "embed_done", "ts": time.time(), "kbs": len(kbs), "queries": len(body.queries)}
    if not kbs:
        yield {"type": "results", "merged": [], "summary": {
            "queries": len(body.queries), "knowledge_bases": 0,
            "chunks_returned": 0, "duration_ms": int((time.perf_counter() - t0) * 1000),
        }}
        yield {"type": "done"}
        return

    sem = asyncio.Semaphore(body.parallelism)

    async def _one(kb: str, q: _QueryItem) -> _SubResult:
        return await _run_one(kb, q, body, sem)

    pending = []
    for kb in kbs:
        for q in body.queries:
            yield {"type": "query_start", "kb": kb, "tag": q.tag, "ts": time.time()}
            pending.append(asyncio.create_task(_one(kb, q)))

    subs: List[_SubResult] = []
    for task in asyncio.as_completed(pending):
        sub = await task
        subs.append(sub)
        yield {"type": "query_done", "kb": sub.kb, "tag": sub.tag,
               "got": len(sub.docs), "error": sub.error or ""}

    if body.fusion == "weighted":
        fused = _fuse_weighted(subs)
    else:
        fused = _fuse_rrf(subs, k=body.rrf_k)
    yield {"type": "fuse_done", "candidates": len(fused), "method": body.fusion}

    final = fused[: body.final_top_k]
    chunks = _attach_provenance(final, subs)
    if subject_for_token is not None:
        _attach_download_tokens(chunks, subject_for_token)

    # rerank 当前由 search_docs 内部做；这里只发占位帧让前端 progress bar 满
    yield {"type": "rerank_done", "kept": len(chunks)}

    yield {
        "type": "results",
        "merged": chunks,
        "summary": {
            "queries": len(body.queries),
            "knowledge_bases": len(kbs),
            "candidates_before_rerank": len(fused),
            "chunks_returned": len(chunks),
            "duration_ms": int((time.perf_counter() - t0) * 1000),
            "fused_by": body.fusion,
            "failed_subqueries": sum(1 for s in subs if s.error),
        },
        "errors": [{"kb": s.kb, "tag": s.tag, "error": s.error} for s in subs if s.error],
    }
    yield {"type": "done"}
