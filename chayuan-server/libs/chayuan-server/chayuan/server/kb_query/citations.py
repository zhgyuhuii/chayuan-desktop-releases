from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlencode


def document_hit_to_result(hit: Dict[str, Any], *, kb_id: str, kb_name: str) -> Dict[str, Any]:
    file_name = str(hit.get("file_name") or hit.get("source") or "")
    query = {"knowledge_base_name": kb_name, "file_name": file_name}
    download_url = f"/knowledge_base/download_doc?{urlencode(query)}" if file_name else ""
    preview_url = f"/knowledge_base/preview_doc?{urlencode(query)}" if file_name else ""
    chunk_id = hit.get("id") or hit.get("chunk_index") or ""

    return {
        "hit_id": str(chunk_id or ""),
        "source_type": "document",
        "score": hit.get("score", 0.0),
        "text": hit.get("content") or hit.get("snippet") or "",
        "snippet": hit.get("snippet") or "",
        "highlight_spans": hit.get("highlight_spans") or [],
        "retrieval_path": hit.get("retrieval_path") or "vector",
        "citation": {
            "kb_id": kb_id,
            "kb_name": kb_name,
            "file_name": file_name,
            "file_id": hit.get("source") or file_name,
            "doc_id": str(hit.get("id") or ""),
            "page": hit.get("page"),
            "chunk_id": str(chunk_id or ""),
            "chunk_index": hit.get("chunk_index"),
            "download_url": download_url,
            "preview_url": preview_url,
            "metadata": {
                "rerank_score": hit.get("rerank_score"),
                "source": hit.get("source") or "",
            },
        },
    }


def structured_block_to_results(block: Dict[str, Any], *, kb_id: str, source_id: int, source_name: str) -> List[Dict[str, Any]]:
    rows = block.get("rows") or []
    columns = block.get("columns") or []
    sql = block.get("sql") or ""
    text = block.get("summary") or block.get("text") or block.get("answer") or ""
    if not text and rows:
        text = f"命中 {len(rows)} 行结构化数据"
    row_preview = ""
    if isinstance(rows, list) and rows:
        rendered: List[str] = []
        for idx, row in enumerate(rows[:20], start=1):
            if isinstance(row, dict):
                rendered.append(f"{idx}. " + ", ".join(f"{k}={v}" for k, v in row.items()))
            elif isinstance(row, (list, tuple)):
                rendered.append(f"{idx}. " + ", ".join(str(v) for v in row))
            else:
                rendered.append(f"{idx}. {row}")
        if len(rows) > 20:
            rendered.append(f"... 共 {len(rows)} 行，仅展示前 20 行")
        row_preview = "\n".join(rendered)
    parts = [str(text).strip()]
    if sql:
        parts.append(f"SQL:\n{sql}")
    if row_preview:
        parts.append(f"结果行:\n{row_preview}")
    text = "\n\n".join(part for part in parts if part).strip()

    table = ""
    primary_keys: List[Any] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                if not table:
                    table = str(row.get("table") or row.get("_table") or "")
                for key in ("id", "ID", "pk", "uuid"):
                    if key in row:
                        primary_keys.append(row[key])
                        break

    return [
        {
            "hit_id": f"{source_id}:{idx}",
            "source_type": "structured",
            "score": 1.0,
            "text": text,
            "retrieval_path": "text2sql",
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "diagnostic": block.get("diagnostic") or "",
            "citation": {
                "kb_id": kb_id,
                "source_id": source_id,
                "source_name": source_name,
                "database": "",
                "schema": "",
                "table": table,
                "primary_key": {"id": primary_keys[0]} if primary_keys else {},
                "row_ids": primary_keys,
                "sql": sql,
                "columns": columns,
                "rows": rows,
            },
        }
        for idx in range(1 if (text or rows or sql) else 0)
    ]

