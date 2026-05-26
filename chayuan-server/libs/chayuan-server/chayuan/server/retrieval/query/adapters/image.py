"""image 知识源(kind=image)在统一检索服务中的适配器。

知识中心首页"按内容搜索"会把所有勾选 KB 走 search_ku_blocks → _process_one;
之前只识别 document/structured/vector,image 直接落到"未知知识类型: image"。
这里把 image 委托给 routes 里已经实现的 _process_one_ku(它内部 dispatch
sub_kind=='image' 走 ImageConnector._search_sync 文本→图跨模态查图),再把
{id,path,preview_url,download_url,title,caption,score} 转成统一 hit 形状。
"""
from __future__ import annotations

from typing import Any, Dict, List

from chayuan.server.retrieval.query.refs import KnowledgeRef


def _hit_to_result(hit: Dict[str, Any], *, ref: KnowledgeRef, idx: int) -> Dict[str, Any]:
    image_id = hit.get("id") or hit.get("image_id") or idx
    caption = str(hit.get("caption") or "")
    title = str(hit.get("title") or "")
    text = caption or title or str(hit.get("path") or "")
    metadata: Dict[str, Any] = {
        "image_id": image_id,
        "path": hit.get("path") or "",
        "preview_url": hit.get("preview_url") or "",
        "download_url": hit.get("download_url") or "",
        "caption": caption,
        "title": title,
    }
    return {
        "hit_id": f"{ref.raw_id}:{image_id}",
        "source_type": "image",
        "score": float(hit.get("score") or 0.0),
        "text": text,
        "retrieval_path": "image",
        "metadata": metadata,
        "citation": {
            "kb_id": ref.kb_id,
            "source_id": int(ref.raw_id) if str(ref.raw_id).isdigit() else ref.raw_id,
            "source_name": ref.name,
            "title": title or ref.display_name or ref.name,
            "preview_url": hit.get("preview_url") or "",
            "download_url": hit.get("download_url") or "",
            "metadata": metadata,
        },
    }


def search_image(ref: KnowledgeRef, query: str, options: Any) -> Dict[str, Any]:
    from chayuan.server.api_server.knowledge_universe_routes import _process_one_ku

    top_k = int(getattr(options, "effective_top_k", options.top_k) or 5)
    block = _process_one_ku(
        ref.kb_id,
        query,
        top_k,
        use_hybrid=getattr(options, "use_hybrid", None),
        use_rerank=getattr(options, "use_rerank", None),
        rewrite_strategy=getattr(options, "rewrite_strategy", "auto"),
    )
    if not block.get("ok"):
        return {
            "ku_id": ref.kb_id,
            "kind": "image",
            "ok": False,
            "results": [],
            "error": block.get("error") or "image search failed",
            "diagnostic": {"route": "image", "error": block.get("error") or ""},
        }
    raw_hits = block.get("results") if isinstance(block.get("results"), list) else []
    results: List[Dict[str, Any]] = []
    for idx, hit in enumerate(raw_hits or []):
        if isinstance(hit, dict):
            results.append(_hit_to_result(hit, ref=ref, idx=idx))
    diagnostic: Dict[str, Any] = {
        "route": "image",
        "retrieval_path": "image",
        "hit_count": len(results),
    }
    return {
        **block,
        "ku_id": ref.kb_id,
        "kind": "image",
        "ok": True,
        "results": results,
        "diagnostic": diagnostic,
    }
