"""多源并行检索编排 + SSE 事件流。

对外暴露两组接口：

- ``multi_search_stream`` —— 产生 SSE 事件 dict，供 FastAPI EventSourceResponse 使用
- ``multi_search_sync`` —— 同步聚合，返回 (chunks, sources_meta)，供 kb_chat 管道调用

SSE 事件形状（data 均为 JSON 字符串）：

    {event: "stage", data: {"stage": "planning"}}
    {event: "source_started", data: {"source_id":1,"kind":"vector","name":"samples"}}
    {event: "source_query",   data: {"source_id":3,"generated_query":"SELECT ..."}}
    {event: "source_chunks",  data: {"source_id":3,"chunks":[...],"elapsed_ms":420}}
    {event: "source_failed",  data: {"source_id":5,"error":"..."}}
    {event: "final",          data: {"sources":[...], "aggregated":[...]}}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from chayuan.server.knowledge_source.base import (
    BaseConnector,
    ConnectionSpec,
    ConnectorError,
)
from chayuan.server.knowledge_source.registry import build_connector, normalize_dialect
from chayuan.server.knowledge_source.types import (
    NLQuery,
    RetrievalChunk,
    SourceKind,
)
from chayuan.server.knowledge_source.vector_adapter import VectorKbConnector

logger = logging.getLogger("chayuan.knowledge_source.orchestrator")


# ---------------------------------------------------------------------------
# Connector 构造
# ---------------------------------------------------------------------------

def _connector_for_source(src: Dict[str, Any]) -> BaseConnector:
    """src 来自 repository.list_sources / get_source（字典形态）。"""
    kind = src.get("kind") or "vector"
    sid = int(src.get("id") or 0)
    if kind == SourceKind.VECTOR.value:
        spec = ConnectionSpec(dialect="vector", database=src.get("name") or "")
        return VectorKbConnector(spec=spec, source_id=sid)
    # 非受管向量源：repository 联查出 spec
    from chayuan.server.db.repository.knowledge_source_repository import (
        connection_spec_for_source,
    )
    resolved = connection_spec_for_source(sid)
    if resolved is None:
        raise ConnectorError(
            f"知识源 #{sid} 未绑定连接信息",
            code="no_connection",
            dialect=kind,
        )
    _, spec = resolved
    # kind 一路透传，让 registry 在 dialect 冲突时能够消歧
    # （如 kind=vs + dialect=es 路由到 ExternalVsConnector；
    #   kind=es + dialect=es 路由到文本检索 EsConnector）
    return build_connector(spec=spec, source_id=sid, kind=kind)


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

async def multi_search_stream(
    query: str,
    sources: List[Dict[str, Any]],
    top_k: int = 5,
    per_source_timeout: float = 30.0,
    llm_model: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    use_router: bool = True,
    user_id: Optional[int] = None,
    user_role: str = "",
) -> AsyncIterator[Dict[str, Any]]:
    """按源并行检索；按完成顺序推 SSE 事件（更贴近"思考进度"体验）。

    每个事件都是 {"event": <str>, "data": <json-str>}，调用方直接包成
    EventSourceResponse 即可。

    ``use_router=True`` 时在源数 ≥4 时用小模型先做相关性预筛，省 token 与耗时；
    失败时 fail-open 回退为全量并行。
    """
    t0 = time.time()
    # metric：扇出度 bucket
    try:
        from chayuan.server.observability.ks_metrics import (
            KS_MULTI_SOURCE_FANOUT, KS_MULTI_SOURCE_TOTAL_SECONDS,
            KS_MULTI_SOURCE_TIMEOUT_TOTAL,
        )
        KS_MULTI_SOURCE_FANOUT.observe(float(len(sources)))
    except Exception:  # noqa: BLE001
        KS_MULTI_SOURCE_FANOUT = KS_MULTI_SOURCE_TOTAL_SECONDS = None
        KS_MULTI_SOURCE_TIMEOUT_TOTAL = None
    yield _evt("stage", {"stage": "planning", "total_sources": len(sources)})

    # 路由：在多源且允许时先做一次粗选
    routed = list(sources)
    if use_router and sources:
        try:
            from chayuan.server.knowledge_source.router import route_sources
            routed = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: route_sources(query=query, sources=sources, llm_model=llm_model),
            )
            if len(routed) != len(sources):
                yield _evt("stage", {
                    "stage": "routed",
                    "before": len(sources), "after": len(routed),
                    "kept_ids": [int(s["id"]) for s in routed if s.get("id") is not None],
                })
        except Exception as _e:  # noqa: BLE001
            logger.warning("router fail-open: %r", _e)
            routed = list(sources)

    nl = NLQuery(
        query=query, history=history or [], top_k=top_k,
        llm_model=llm_model, timeout=per_source_timeout,
        user_id=user_id, user_role=user_role,
    )

    # Connector 构造：失败的源立刻 source_failed 不参与后续
    active: List[Tuple[Dict[str, Any], BaseConnector]] = []
    for src in routed:
        try:
            conn = _connector_for_source(src)
            active.append((src, conn))
            yield _evt("source_started", {
                "source_id": src.get("id"),
                "kind": src.get("kind"),
                "name": src.get("display_name") or src.get("name"),
            })
        except ConnectorError as e:
            yield _evt("source_failed", {
                "source_id": src.get("id"), "error": str(e), "code": e.code,
            })
        except Exception as e:  # noqa: BLE001
            yield _evt("source_failed", {
                "source_id": src.get("id"), "error": f"{type(e).__name__}: {e}",
            })

    if not active:
        yield _evt("final", {"sources": [], "aggregated": [],
                             "elapsed_ms": int((time.time() - t0) * 1000)})
        return

    # asyncio 任务：每源独立超时
    async def _run(src_dict, connector) -> Tuple[Dict[str, Any], List[RetrievalChunk], Optional[str], float]:
        st = time.time()
        try:
            chunks = await asyncio.wait_for(connector.search(nl), timeout=per_source_timeout)
            return src_dict, list(chunks or []), None, time.time() - st
        except asyncio.TimeoutError:
            return src_dict, [], f"超时 >{per_source_timeout}s", time.time() - st
        except ConnectorError as e:
            return src_dict, [], f"{e.code}: {e}", time.time() - st
        except Exception as e:  # noqa: BLE001
            return src_dict, [], f"{type(e).__name__}: {e}", time.time() - st

    tasks = [asyncio.create_task(_run(src, conn)) for src, conn in active]
    sources_meta: List[Dict[str, Any]] = []
    all_chunks: List[RetrievalChunk] = []

    for fut in asyncio.as_completed(tasks):
        src, chunks, err, elapsed = await fut
        if err:
            yield _evt("source_failed", {
                "source_id": src.get("id"), "error": err,
                "elapsed_ms": int(elapsed * 1000),
            })
            sources_meta.append({
                "source_id": src.get("id"), "name": src.get("name"),
                "kind": src.get("kind"), "ok": False, "error": err,
                "elapsed_ms": int(elapsed * 1000), "chunks": [],
            })
            # metric：超时专门计数
            try:
                if "超时" in err and KS_MULTI_SOURCE_TIMEOUT_TOTAL is not None:
                    KS_MULTI_SOURCE_TIMEOUT_TOTAL.labels(
                        kind=src.get("kind") or "unknown",
                        dialect=src.get("kind") or "unknown",  # 粒度：用 kind 代 dialect
                    ).inc()
            except Exception:  # noqa: BLE001
                pass
            continue
        # 推送本源生成的查询（仅结构化源有）
        gen_q = None
        if chunks:
            gen_q = chunks[0].citation.generated_query
            if gen_q:
                yield _evt("source_query", {
                    "source_id": src.get("id"), "generated_query": gen_q,
                })
        wire_chunks = [c.to_wire() for c in chunks]
        yield _evt("source_chunks", {
            "source_id": src.get("id"), "kind": src.get("kind"),
            "chunks": wire_chunks, "elapsed_ms": int(elapsed * 1000),
        })
        sources_meta.append({
            "source_id": src.get("id"), "name": src.get("name"),
            "kind": src.get("kind"), "ok": True,
            "elapsed_ms": int(elapsed * 1000), "chunks": wire_chunks,
            "generated_query": gen_q,
        })
        all_chunks.extend(chunks)

    yield _evt("aggregating", {"total_chunks": len(all_chunks)})
    aggregated = _rerank(all_chunks, top_k=top_k * max(1, len(active)))
    elapsed_total = time.time() - t0
    # metric：多源检索总时长
    try:
        if KS_MULTI_SOURCE_TOTAL_SECONDS is not None:
            status = "ok" if active else "no_source"
            KS_MULTI_SOURCE_TOTAL_SECONDS.labels(status=status).observe(elapsed_total)
    except Exception:  # noqa: BLE001
        pass
    yield _evt("final", {
        "sources": sources_meta,
        "aggregated": [c.to_wire() for c in aggregated],
        "elapsed_ms": int(elapsed_total * 1000),
    })


async def multi_search_sync(
    query: str,
    sources: List[Dict[str, Any]],
    top_k: int = 5,
    per_source_timeout: float = 30.0,
    llm_model: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
    user_id: Optional[int] = None,
    user_role: str = "",
) -> Tuple[List[RetrievalChunk], List[Dict[str, Any]]]:
    """不走 SSE 的同步聚合版本。用于 kb_chat 内部。"""
    nl = NLQuery(
        query=query, history=history or [], top_k=top_k,
        llm_model=llm_model, timeout=per_source_timeout,
        user_id=user_id, user_role=user_role,
    )
    tasks = []
    metas: List[Dict[str, Any]] = []
    for src in sources:
        try:
            conn = _connector_for_source(src)
        except ConnectorError as e:
            metas.append({"source_id": src.get("id"), "ok": False, "error": str(e)})
            continue
        tasks.append((src, asyncio.create_task(
            asyncio.wait_for(conn.search(nl), timeout=per_source_timeout)
        )))
    all_chunks: List[RetrievalChunk] = []
    for src, t in tasks:
        try:
            chunks = await t
        except Exception as e:  # noqa: BLE001
            metas.append({"source_id": src.get("id"), "ok": False,
                          "error": f"{type(e).__name__}: {e}"})
            continue
        gen_q = None
        if chunks:
            gen_q = chunks[0].citation.generated_query
        metas.append({
            "source_id": src.get("id"), "name": src.get("name"),
            "kind": src.get("kind"), "ok": True,
            "chunks": [c.to_wire() for c in chunks],
            "generated_query": gen_q,
        })
        all_chunks.extend(chunks)
    aggregated = _rerank(all_chunks, top_k=top_k * max(1, len(sources)))
    return aggregated, metas


# ---------------------------------------------------------------------------
# 简化版 rerank：按 kind 做 round-robin + score 排序，避免向量源洪水
# ---------------------------------------------------------------------------

def _rerank(chunks: List[RetrievalChunk], top_k: int) -> List[RetrievalChunk]:
    if not chunks:
        return []
    # 按 source_kind 分组，每组内按 score 降序
    buckets: Dict[str, List[RetrievalChunk]] = {}
    for c in chunks:
        buckets.setdefault(c.source_kind or "other", []).append(c)
    for k in buckets:
        buckets[k].sort(key=lambda x: (-float(x.score or 0.0), len(x.content)))
    # round-robin 取
    out: List[RetrievalChunk] = []
    idx = 0
    while len(out) < top_k:
        added = 0
        for k in list(buckets.keys()):
            lst = buckets[k]
            if idx < len(lst):
                out.append(lst[idx])
                added += 1
                if len(out) >= top_k:
                    break
        if added == 0:
            break
        idx += 1
    return out


def _evt(event: str, data: Any) -> Dict[str, str]:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}
