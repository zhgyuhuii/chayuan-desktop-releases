from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return default


def _text_from_hit(hit: Dict[str, Any]) -> str:
    for key in ("text", "content", "page_content", "summary", "answer"):
        value = hit.get(key)
        if value:
            return str(value)
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    for key in ("text", "content", "page_content", "summary"):
        value = metadata.get(key)
        if value:
            return str(value)
    return ""


def _stringify_rows(rows: Any, *, limit: int = 20) -> str:
    if not isinstance(rows, list) or not rows:
        return ""
    rendered: List[str] = []
    for idx, row in enumerate(rows[:limit], start=1):
        if isinstance(row, dict):
            rendered.append(f"{idx}. " + ", ".join(f"{k}={v}" for k, v in row.items()))
        elif isinstance(row, (list, tuple)):
            rendered.append(f"{idx}. " + ", ".join(str(v) for v in row))
        else:
            rendered.append(f"{idx}. {row}")
    if len(rows) > limit:
        rendered.append(f"... 共 {len(rows)} 行，仅展示前 {limit} 行")
    return "\n".join(rendered)


def _structured_text(block: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("text", "summary", "answer"):
        value = str(block.get(key) or "").strip()
        if value:
            parts.append(value)
    sql = str(block.get("sql") or "").strip()
    if sql:
        parts.append(f"SQL:\n{sql}")
    rows_text = _stringify_rows(block.get("rows"))
    if rows_text:
        parts.append(f"结果行:\n{rows_text}")
    return "\n\n".join(parts).strip()


def _diagnostic_message(block: Dict[str, Any], exc: Optional[BaseException] = None) -> str:
    if exc is not None:
        raw = f"{type(exc).__name__}: {exc}"
    else:
        raw = str(block.get("error") or block.get("diagnostic") or "").strip()
    if "model" in raw and "not found" in raw:
        return "结构化检索模型不可用，请检查 Ollama 模型配置。"
    if "qwen" in raw and ("404" in raw or "NotFound" in raw or "not_found" in raw):
        return "结构化检索模型不可用，请检查 Ollama 模型配置。"
    return raw


def result_to_chunk(
    result: Dict[str, Any],
    *,
    ku_id: str,
    tag: str = "",
    section_ids: Optional[Sequence[str]] = None,
    index: int = 0,
    block: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = dict(result.get("metadata") or {})
    source_type = str(result.get("source_type") or result.get("kind") or (block or {}).get("kind") or "")
    metadata.update({
        "ku_id": ku_id,
        "source_type": source_type,
        "kind": source_type,
        "sub_kind": result.get("sub_kind") or (block or {}).get("sub_kind") or metadata.get("sub_kind") or "",
    })
    for key in ("sql", "columns", "rows", "diagnostic"):
        if key in result and key not in metadata:
            metadata[key] = result.get(key)
    citation = result.get("citation") if isinstance(result.get("citation"), dict) else {}
    for key in ("sql", "columns", "rows"):
        if key in citation and key not in metadata:
            metadata[key] = citation.get(key)
    text = _text_from_hit(result)
    if not text and block:
        text = _structured_text(block)
    score = _safe_float(result.get("score", result.get("score_raw", 1.0 if text else 0.0)))
    return {
        "chunk_id": str(result.get("id") or result.get("chunk_id") or f"{ku_id}:{tag or 'q'}:{index}"),
        "kb_name": ku_id,
        "file_name": str(result.get("file_name") or result.get("source") or result.get("title") or ""),
        "score_raw": score,
        "score_fused": score,
        "text": text,
        "metadata": metadata,
        "from_query_tags": [tag] if tag else [],
        "from_section_ids": list(section_ids or []),
        "kbs": [ku_id],
        "download_token": str(result.get("download_token") or ""),
    }


def block_to_chunks(
    block: Dict[str, Any],
    *,
    tag: str = "",
    section_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    ku_id = str(block.get("ku_id") or "")
    hits = block.get("results") if isinstance(block.get("results"), list) else block.get("hits")
    if isinstance(hits, list) and hits:
        return [
            result_to_chunk(hit, ku_id=ku_id, tag=tag, section_ids=section_ids, index=idx, block=block)
            for idx, hit in enumerate(hits)
            if isinstance(hit, dict)
        ]

    text = _structured_text(block)
    diagnostic = _diagnostic_message(block)
    if not text and diagnostic:
        text = diagnostic
    if not text:
        return []
    metadata = {
        "ku_id": ku_id,
        "kind": block.get("kind") or "",
        "source_type": block.get("kind") or "",
        "sub_kind": block.get("sub_kind") or "",
        "sql": block.get("sql") or "",
        "columns": block.get("columns") or [],
        "rows": block.get("rows") or [],
        "diagnostic": block.get("diagnostic") or diagnostic,
    }
    return [{
        "chunk_id": f"{ku_id or 'ku'}:{tag or 'q'}:block",
        "kb_name": ku_id,
        "file_name": str(block.get("display_name") or block.get("name") or ""),
        "score_raw": 1.0,
        "score_fused": 1.0,
        "text": text,
        "metadata": metadata,
        "from_query_tags": [tag] if tag else [],
        "from_section_ids": list(section_ids or []),
        "kbs": [ku_id] if ku_id else [],
        "download_token": "",
    }]


def _process_one(
    ku_id: str,
    query: str,
    top_k: int,
    *,
    use_hybrid: Optional[bool],
    use_rerank: Optional[bool],
    rewrite_strategy: str,
    processor_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from types import SimpleNamespace

    from chayuan.server.retrieval.query.adapters import (
        search_document,
        search_image,
        search_structured,
        search_vector,
    )
    from chayuan.server.retrieval.query.refs import resolve_ref

    kwargs = processor_kwargs or {}
    options = SimpleNamespace(
        top_k=top_k,
        effective_top_k=top_k,
        score_threshold=None,
        use_hybrid=use_hybrid,
        use_rerank=use_rerank,
        rewrite_strategy=rewrite_strategy,
        model=kwargs.get("model"),
        structured_scopes=kwargs.get("structured_scopes") or {},
        vector_scopes=kwargs.get("vector_scopes") or {},
    )
    ref = resolve_ref(ku_id)
    if ref.kind == "document":
        return search_document(ref, query, options)
    if ref.kind == "structured":
        return search_structured(ref, query, options)
    if ref.kind == "vector":
        return search_vector(ref, query, options)
    if ref.kind == "image":
        return search_image(ref, query, options)
    return {"ku_id": ku_id, "kind": ref.kind, "ok": False, "error": f"未知知识类型: {ref.kind}"}


async def search_ku_blocks(
    targets: Iterable[Tuple[str, str, str, Sequence[str]]],
    *,
    top_k: int = 6,
    use_hybrid: Optional[bool] = None,
    use_rerank: Optional[bool] = None,
    rewrite_strategy: str = "passthrough",
    timeout_s: float = 30.0,
    max_concurrency: int = 8,
    processor_kwargs: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    semaphore = asyncio.Semaphore(max(1, int(max_concurrency or 1)))

    async def _run(ku_id: str, query: str, tag: str, section_ids: Sequence[str]) -> Dict[str, Any]:
        started = time.perf_counter()
        async with semaphore:
            try:
                block = await asyncio.wait_for(
                    asyncio.to_thread(
                        _process_one,
                        ku_id,
                        query,
                        int(top_k or 6),
                        use_hybrid=use_hybrid,
                        use_rerank=use_rerank,
                        rewrite_strategy=rewrite_strategy,
                        processor_kwargs=processor_kwargs,
                    ),
                    timeout=max(1.0, float(timeout_s or 30.0)),
                )
                block["_query_tag"] = tag
                block["_section_ids"] = list(section_ids or [])
                block.setdefault("diagnostic", {})
                if isinstance(block.get("diagnostic"), dict):
                    block["diagnostic"]["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
                return block
            except Exception as exc:  # noqa: BLE001
                return {
                    "ku_id": ku_id,
                    "ok": False,
                    "results": [],
                    "error": _diagnostic_message({}, exc),
                    "_query_tag": tag,
                    "_section_ids": list(section_ids or []),
                    "diagnostic": {"elapsed_ms": int((time.perf_counter() - started) * 1000)},
                }

    task_inputs = list(targets)
    if not task_inputs:
        return [], []
    results = await asyncio.gather(*[_run(*item) for item in task_inputs], return_exceptions=False)
    blocks: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for block in results:
        blocks.append(block)
        if not block.get("ok"):
            errors.append({
                "kb": block.get("ku_id") or "",
                "tag": block.get("_query_tag") or "",
                "error": block.get("error") or _diagnostic_message(block) or "search failed",
            })
    return blocks, errors


async def search_ku_chunks(
    targets: Iterable[Tuple[str, str, str, Sequence[str]]],
    **kwargs: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    blocks, errors = await search_ku_blocks(targets, **kwargs)
    chunks: List[Dict[str, Any]] = []
    for block in blocks:
        chunks.extend(block_to_chunks(
            block,
            tag=str(block.get("_query_tag") or ""),
            section_ids=list(block.get("_section_ids") or []),
        ))
    chunks.sort(key=lambda c: _safe_float(c.get("score_fused"), _safe_float(c.get("score_raw"))), reverse=True)
    return chunks, errors, blocks
