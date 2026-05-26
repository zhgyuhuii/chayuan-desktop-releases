"""检索命中文档 → 前端富结果信封 的统一映射。

为什么单独成文件:
- 多个端点(/knowledge_universe/ask, /ask/stream, /retrieve)都要返一致的字段。
- 不同 KB backend(FAISS / Milvus / Chroma / PG)写进 metadata 的 key 名风格不统一
  (source vs file_name, chunk_index vs idx, id vs pk),这里做一次归一。
- 客户端按 RAG 行业惯例需要的最小有用字段:
    id, kb_name, file_name, page, chunk_index, score, rerank_score?,
    snippet, content, embed_model_used, retrieval_path
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlencode
from typing import Any, Dict, Iterable, List, Optional

from langchain_core.documents import Document

# ---------------------------------------------------------------------------
# Snippet / 高亮
# ---------------------------------------------------------------------------

# 最大片段窗口(字符数)。120–200 是 ChatGPT / Notion / Perplexity 的常见区间;
# 200 在中文场景能塞下两三句完整话,既不显得空也不会撑爆卡片。
SNIPPET_WINDOW = 200


def _split_query_terms(query: str) -> List[str]:
    """切 query 为高亮关键词。

    优先 jieba(中文友好);未装时退化为按非汉字/字母数字符切分。
    所有 token 去重 + 长度 ≥2(单字高亮太碎)。
    """
    query = (query or "").strip()
    if not query:
        return []
    tokens: List[str] = []
    try:
        import jieba  # type: ignore

        # cut_for_search 比 cut 更激进,适合做高亮
        tokens = [t for t in jieba.cut_for_search(query) if t.strip()]
    except Exception:  # noqa: BLE001
        tokens = re.split(r"[^\w一-鿿]+", query)
    seen: set = set()
    out: List[str] = []
    for t in tokens:
        t = t.strip()
        if len(t) < 2:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    # 兜底:若分词出来一个都没有,把整个 query 作为关键词(短 query 场景)
    if not out and len(query) >= 2:
        out.append(query)
    return out


def make_snippet(
    content: str,
    query: str,
    *,
    window: int = SNIPPET_WINDOW,
) -> Dict[str, Any]:
    """围绕匹配关键词截一段 ±window/2 字符的窗口,并返回相对的 highlight spans。

    返回:
      {
        "snippet": "...月度报销总额不得超过 [本人月薪的 30%]...",
        "highlight_spans": [[start1, end1], [start2, end2], ...],  # 相对 snippet
      }
    没有任何关键词命中时,取 content 前 window 字。
    """
    if not content:
        return {"snippet": "", "highlight_spans": []}
    terms = _split_query_terms(query)
    if not terms:
        return {"snippet": content[:window], "highlight_spans": []}

    # 找所有命中位置
    matches: List[tuple] = []  # (start, end, term)
    for term in terms:
        start = 0
        while True:
            idx = content.find(term, start)
            if idx < 0:
                break
            matches.append((idx, idx + len(term), term))
            start = idx + len(term)
    if not matches:
        return {"snippet": content[:window], "highlight_spans": []}

    # 选"密度最高的一段":以 window 为滑窗,挑命中数最多的中心点
    matches.sort(key=lambda x: x[0])
    best_center = matches[0][0]
    best_hits = 0
    for i, (s, _, _) in enumerate(matches):
        center = s
        # 落在 [center - window/2, center + window/2] 的 match 数
        lo = center - window // 2
        hi = center + window // 2
        hits = sum(1 for (ms, me, _) in matches if ms >= lo and me <= hi)
        if hits > best_hits:
            best_hits = hits
            best_center = center

    snippet_lo = max(0, best_center - window // 2)
    snippet_hi = min(len(content), snippet_lo + window)
    snippet_lo = max(0, snippet_hi - window)  # 反向修正,贴近右边界
    snippet = content[snippet_lo:snippet_hi]

    # 在 snippet 范围内重新定位 highlight spans(转相对偏移)
    spans: List[List[int]] = []
    for ms, me, _ in matches:
        if ms >= snippet_lo and me <= snippet_hi:
            spans.append([ms - snippet_lo, me - snippet_lo])
    spans.sort()
    # 合并相邻/重叠 span,避免 UI 渲染重叠
    merged: List[List[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    # 美化:左/右补省略号(若不是边界)
    prefix = "…" if snippet_lo > 0 else ""
    suffix = "…" if snippet_hi < len(content) else ""
    if prefix:
        snippet = prefix + snippet
        merged = [[s + len(prefix), e + len(prefix)] for s, e in merged]
    if suffix:
        snippet = snippet + suffix
    return {"snippet": snippet, "highlight_spans": merged}


# ---------------------------------------------------------------------------
# Metadata 归一
# ---------------------------------------------------------------------------

def _meta(doc) -> Dict[str, Any]:
    """既 hold langchain Document 又 hold dict 形态的安全 metadata 取值。"""
    if doc is None:
        return {}
    if hasattr(doc, "metadata"):
        return dict(doc.metadata or {})
    if isinstance(doc, dict):
        return dict(doc.get("metadata") or {})
    return {}


def _content(doc) -> str:
    if doc is None:
        return ""
    if hasattr(doc, "page_content"):
        return doc.page_content or ""
    if isinstance(doc, dict):
        return doc.get("page_content") or doc.get("content") or ""
    return ""


def _file_name_from(meta: Dict[str, Any]) -> str:
    """各种命名风格归一到 file_name。

    优先级:
      file_name > filename > source(若是路径,取 basename) > title > ""
    """
    for k in ("file_name", "filename"):
        v = meta.get(k)
        if v:
            return str(v)
    src = meta.get("source")
    if src:
        s = str(src)
        # source 一般是相对/绝对路径;UI 上文件名最有用
        return os.path.basename(s) or s
    title = meta.get("title")
    if title:
        return str(title)
    return ""


def _download_file_name_from(meta: Dict[str, Any]) -> str:
    """下载接口需要 KB 内原始 file_name,尽量不要 basename 截断目录。"""
    for k in ("file_name", "filename", "source"):
        v = meta.get(k)
        if v:
            return str(v)
    title = meta.get("title")
    return str(title) if title else ""


def _kb_file_urls(kb_name: str, file_name: str) -> Dict[str, str]:
    if not kb_name or not file_name:
        return {"download_url": "", "preview_url": ""}
    common = {"knowledge_base_name": kb_name, "file_name": file_name}
    return {
        "download_url": f"/knowledge_base/download_doc?{urlencode(common)}",
        "preview_url": f"/knowledge_base/preview_doc?{urlencode(common)}",
    }


def _page_from(meta: Dict[str, Any]) -> Optional[int]:
    """PDF / Word 加载器写的页码字段不统一,这里都接住。"""
    for k in ("page", "page_number", "page_no", "pageno", "page_idx"):
        v = meta.get(k)
        if v is None or v == "":
            continue
        try:
            # langchain PDF loader 是 0-based;UI 显示 1-based,这里只透传不偏移
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def _chunk_index_from(meta: Dict[str, Any]) -> Optional[int]:
    for k in ("chunk_index", "chunk_idx", "chunk_id", "idx"):
        v = meta.get(k)
        if v is None or v == "":
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return None


def _id_from(meta: Dict[str, Any]) -> str:
    for k in ("id", "doc_id", "pk", "vs_id"):
        v = meta.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _score_from(meta: Dict[str, Any], doc) -> float:
    """优先取 metadata.score,次取 doc.score 属性,再次 0.0。"""
    v = meta.get("score")
    if v in (None, ""):
        v = getattr(doc, "score", None) if doc is not None else None
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# 入口:Document → wire dict
# ---------------------------------------------------------------------------

def to_wire(
    doc,
    *,
    query: str,
    kb_name: str = "",
    embed_model_used: str = "",
    retrieval_path: str = "vector",
) -> Dict[str, Any]:
    """将单个命中 Document/dict 序列化为前端用的富结果。

    保留原始 ``content`` 让前端可"展开";UI 默认只渲染 ``snippet`` + 高亮。
    """
    meta = _meta(doc)
    content = _content(doc)
    snip = make_snippet(content, query)
    score = _score_from(meta, doc)
    resolved_kb_name = kb_name or str(meta.get("kb_name") or "")
    raw_file_name = _download_file_name_from(meta)
    urls = _kb_file_urls(resolved_kb_name, raw_file_name)
    rerank_score = meta.get("rerank_score")
    try:
        rerank_score = float(rerank_score) if rerank_score not in (None, "") else None
    except (TypeError, ValueError):
        rerank_score = None
    return {
        "id": _id_from(meta),
        "kb_name": resolved_kb_name,
        "file_name": _file_name_from(meta),
        "page": _page_from(meta),
        "chunk_index": _chunk_index_from(meta),
        "score": score,
        "rerank_score": rerank_score,
        "snippet": snip["snippet"],
        "highlight_spans": snip["highlight_spans"],
        "content": content,
        "embed_model_used": embed_model_used,
        "retrieval_path": retrieval_path,
        # 透传 source 让"跳转原文"按钮还能拿到原始路径
        "source": meta.get("source") or "",
        "download_url": urls["download_url"],
        "preview_url": urls["preview_url"],
    }


def to_wire_list(
    docs: Iterable,
    *,
    query: str,
    kb_name: str = "",
    embed_model_used: str = "",
    retrieval_path: str = "vector",
) -> List[Dict[str, Any]]:
    return [
        to_wire(
            d,
            query=query,
            kb_name=kb_name,
            embed_model_used=embed_model_used,
            retrieval_path=retrieval_path,
        )
        for d in (docs or [])
    ]
