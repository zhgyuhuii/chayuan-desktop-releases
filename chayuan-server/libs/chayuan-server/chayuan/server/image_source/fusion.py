"""Reciprocal Rank Fusion(Cormack et al., 2009)。

适用:多路独立排序的结果归一融合,例如默认文本向量模型路 + CLIP 跨模态路。
公式:score(d) = Σ_path  1 / (k + rank_in_path(d) + 1)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import List, Optional


@dataclass
class ImageHit:
    id: str
    filename: str
    thumbnail_url: str
    score: float
    source_path: str  # "text_vec" | "clip_text" | "clip_image" | "fused"
    fused: bool = False
    ocr_snippet: Optional[str] = None
    has_text_vector: bool = True
    meta: dict = field(default_factory=dict)


def rrf_fuse(rankings: List[List[ImageHit]], k: int = 60) -> List[ImageHit]:
    """对多路 ranking 做 RRF 融合。各路内已按相关性降序。

    返回按融合分数降序的 ImageHit 列表,source_path='fused', fused=True。
    """
    scores: defaultdict[str, float] = defaultdict(float)
    payload: dict[str, ImageHit] = {}
    for ranked in rankings:
        for rank, hit in enumerate(ranked):
            scores[hit.id] += 1.0 / (k + rank + 1)
            payload.setdefault(hit.id, hit)
    return [
        replace(payload[iid], score=s, source_path="fused", fused=True)
        for iid, s in sorted(scores.items(), key=lambda x: -x[1])
    ]
