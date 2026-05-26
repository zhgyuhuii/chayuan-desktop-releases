"""文字搜图 service:三路并行 + RRF 融合。

  1. **关键词路径**(``_run_keyword_path``):字面 substring 匹配 OCR 文本 / 文件名
     — 用户报"输入'冠军'返回了不含'冠军'的图",这条路保证字面命中**永远前置**。
  2. **文本向量路径**(``_run_text_vec_path``):用户默认文本嵌入(bge-m3 等)
     query → OCR text 向量,召回语义相近的 OCR 文本。
  3. **CLIP 跨模态路径**(``_run_clip_text_path``):CLIP 把 query 直接映到图像
     空间,召回视觉上相似的图(即使 OCR 没文字)。

  RRF 融合时关键词路径放第一,让字面命中绝对优先;后两路按近邻分数排。
  diagnostics 字段告诉前端每路命中多少,可分组展示。
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from chayuan.server.image_source.text_embed_client import (
    EmbedResult, embed_text as _text_embed_impl,
    resolve_endpoint as _resolve_text_embed_endpoint_impl,
)
from chayuan.server.image_source.fusion import ImageHit, rrf_fuse
from chayuan.server.image_source.store import get_store

logger = logging.getLogger("chayuan.image_source.text_search")


@dataclass
class _PathResult:
    hits: List[ImageHit]
    error: Optional[str] = None


# ---- seams(可 monkeypatch) ----

async def _clip_embed_text(query: str, *, model_name: str = ""):
    """CLIP 文本编码器把 query 文本映到图像向量空间。"""
    from chayuan.server.image_source.embedder import get_embedder

    def _sync():
        emb = get_embedder(model_name) if model_name else get_embedder(None)
        return np.asarray(emb.embed_text(query), dtype="float32").reshape(-1)
    return await asyncio.to_thread(_sync)


async def _text_embed(query: str, *, base_url: str, model: str,
                      api_key: Optional[str] = None,
                      timeout: float = 30.0) -> EmbedResult:
    return await _text_embed_impl(
        query, base_url=base_url, model=model,
        api_key=api_key, timeout=timeout,
    )


def _resolve_text_embed_endpoint() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    return _resolve_text_embed_endpoint_impl()


# ---- 主流程 ----

async def text_search(
    store_name: str, query: str, *, top_k: int = 20, k_rrf: int = 60,
    source_id: int = 0, model_name: str = "",
) -> Dict[str, Any]:
    """三路文字搜图。返 {"hits": [..], "diagnostics": {..}}。

    用户报"搜'冠军'返回了不含'冠军'的图" — 加 keyword 路径让字面命中绝对优先;
    向量路在它之后做语义补充。
    """
    store = get_store(store_name)
    # 诊断用中文人话,前端 ImageKbDetail 直接展示 — 之前 "ok / unavailable: ..."
    # 是工程黑话,用户看不懂。三路统一用"已命中 N 张" / "未启用:<原因>" 结构。
    diagnostics: Dict[str, str] = {
        "keyword_path": "未命中",
        "text_path": "未启用",
        "image_path": "未启用",
    }

    total = store.count()
    if total == 0:
        # 索引空 — 给个清晰提示,不让前端只看到 "未找到相关图片"
        return {
            "hits": [],
            "diagnostics": {
                **diagnostics,
                "summary": "知识库还没有图片,请先上传后再搜索。",
            },
        }

    # 关键词路是 in-memory substring 扫描,~ O(N) 不耗时间;同步跑就行
    keyword_hits = _run_keyword_path(store, query, top_k, source_id)
    diagnostics["keyword_path"] = (
        f"按关键词命中 {len(keyword_hits)} 张" if keyword_hits else "按关键词未命中"
    )

    text_task = asyncio.create_task(
        _run_text_vec_path(store, query, top_k, source_id)
    )
    clip_task = asyncio.create_task(
        _run_clip_text_path(store, query, top_k, source_id, model_name)
    )
    text_res, image_res = await asyncio.gather(text_task, clip_task)

    # **rankings 顺序决定 RRF 融合时各路的隐含权重** — 关键词路第一,
    # 字面命中永远抢在向量召回前面。后两路按 (text_vec, clip) 自然顺序。
    rankings: List[List[ImageHit]] = []
    if keyword_hits:
        rankings.append(keyword_hits)
    if text_res.error:
        diagnostics["text_path"] = f"文本向量搜索不可用:{text_res.error}"
    elif text_res.hits:
        diagnostics["text_path"] = f"按 OCR 文字命中 {len(text_res.hits)} 张"
        rankings.append(text_res.hits)
    else:
        diagnostics["text_path"] = "按 OCR 文字未命中"
    if image_res.error:
        diagnostics["image_path"] = f"视觉跨模态搜索不可用:{image_res.error}"
    elif image_res.hits:
        diagnostics["image_path"] = f"按视觉相似命中 {len(image_res.hits)} 张"
        rankings.append(image_res.hits)
    else:
        diagnostics["image_path"] = "按视觉相似未命中"

    if not rankings:
        return {
            "hits": [],
            "diagnostics": {
                **diagnostics,
                "summary": f"知识库共 {total} 张图,但三种搜索方式都没命中。"
                            "建议:换关键词试试,或检查 KB 详情页有无「未索引」图。",
            },
        }
    fused = rrf_fuse(rankings, k=k_rrf)[:top_k]
    # 关键词命中的 id 集合 — 融合后 source_path 都会被改成 "fused",前端没法
    # 凭 ImageHit 区分哪些是字面命中。这里把这一信息单独传给前端,UI 可加
    # "字面" 角标 / 分组。
    keyword_ids = {h.id for h in keyword_hits}
    return {
        "hits": [
            {**_hit_to_wire(h), "keyword_match": h.id in keyword_ids}
            for h in fused
        ],
        "diagnostics": diagnostics,
    }


def _run_keyword_path(store, query: str, top_k: int, source_id: int) -> List[ImageHit]:
    """OCR 文本 / 文件名 substring 匹配。

    设计:
      - 仅在 query.strip() >= 2 字符时跑(单字误命中太多;空白 query 不跑)
      - 大小写不敏感;命中位置不影响排序(只按"是否命中"二值化进 RRF)
      - 同时匹配 OCR 文本和 filename(用户报"图名带'冠军'"也算命中)
      - **不**做切词 / 模糊;字面就是字面 — 用户想要"含这俩字"的图

    出 ImageHit.source_path="keyword",前端 UI 可分组显示"字面命中"。
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    q_lower = q.lower()
    # list_items 拿全量 meta;ImageStore 顺序 = 入库顺序,够稳定
    items = store.list_items(limit=10_000)
    hits: List[ImageHit] = []
    for rec in items:
        ocr = (rec.get("ocr_text") or "").lower()
        fname = (rec.get("filename") or "").lower()
        if q_lower in ocr or q_lower in fname:
            hits.append(_rec_to_hit(rec, score=1.0, source_path="keyword",
                                      source_id=source_id))
            if len(hits) >= top_k * 3:  # 关键词命中给 3 倍 top_k 空间,够 RRF 用
                break
    return hits


async def _run_text_vec_path(store, query, top_k, source_id) -> _PathResult:
    base_url, model, api_key = _resolve_text_embed_endpoint()
    if not base_url or not model:
        return _PathResult(
            hits=[],
            error="default text embedding model not configured",
        )
    res = await _text_embed(query, base_url=base_url, model=model,
                              api_key=api_key)
    if res.vector is None:
        return _PathResult(hits=[], error=res.error or "no vector")
    hits = store.search_text(res.vector, top_k=top_k)
    return _PathResult(
        hits=[_rec_to_hit(rec, score, "text_vec", source_id)
              for rec, score in hits],
    )


async def _run_clip_text_path(store, query, top_k, source_id, model_name) -> _PathResult:
    try:
        vec = await _clip_embed_text(query, model_name=model_name)
    except Exception as e:  # noqa: BLE001
        # 复用 pipeline 的产品级错误翻译(transformers AutoProcessor 缺失等),
        # 让"视觉跨模态路 unavailable" 给到的不是裸 stack 而是修复指引。
        from chayuan.server.image_source.pipeline import _friendly_clip_error
        raw = str(e)
        friendly = _friendly_clip_error(raw)
        msg = friendly or f"clip text encode failed: {raw}"
        logger.info("CLIP text encode failed: %r", e)
        return _PathResult(hits=[], error=msg)
    hits = store.search_image(vec, top_k=top_k)
    return _PathResult(
        hits=[_rec_to_hit(rec, score, "clip_text", source_id)
              for rec, score in hits],
    )


def _rec_to_hit(rec: Dict[str, Any], score: float, source_path: str,
                 source_id: int) -> ImageHit:
    iid = rec.get("id") or ""
    fname = rec.get("filename") or (
        os.path.basename(rec.get("path") or "") or iid
    )
    thumb = (
        f"/knowledge_source/{int(source_id)}/image/{iid}"
        if source_id else f"/{iid}"
    )
    return ImageHit(
        id=iid, filename=fname, thumbnail_url=thumb, score=float(score),
        source_path=source_path,
        ocr_snippet=(rec.get("ocr_text") or "")[:200] or None,
        has_text_vector=bool(rec.get("has_text_vector")),
        meta={
            "path": rec.get("path"),
            "md5": rec.get("md5"),
            "size_bytes": rec.get("size_bytes"),
            "created_at": rec.get("created_at"),
        },
    )


def _hit_to_wire(h: ImageHit) -> Dict[str, Any]:
    return {
        "id": h.id, "filename": h.filename,
        "thumbnail_url": h.thumbnail_url, "score": h.score,
        "source_path": h.source_path, "fused": h.fused,
        "ocr_snippet": h.ocr_snippet,
        "has_text_vector": h.has_text_vector,
        "meta": h.meta,
    }
