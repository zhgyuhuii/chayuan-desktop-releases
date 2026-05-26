"""RRF 融合 + 单路退化。"""
from __future__ import annotations


def _hit(id_, src="text_vec"):
    from chayuan.server.image_source.fusion import ImageHit
    return ImageHit(id=id_, filename=f"{id_}.png",
                     thumbnail_url=f"/{id_}", score=0.0, source_path=src)


def test_rrf_two_paths_same_id_promoted():
    from chayuan.server.image_source.fusion import rrf_fuse
    path_a = [_hit("a", "text_vec"), _hit("b", "text_vec"), _hit("c", "text_vec")]
    path_b = [_hit("b", "clip_text"), _hit("a", "clip_text"), _hit("d", "clip_text")]
    fused = rrf_fuse([path_a, path_b], k=60)
    ids = [h.id for h in fused]
    # a (rank0+rank1) 与 b (rank1+rank0) 分数相同 → 排在前;c/d 各只一路
    assert set(ids[:2]) == {"a", "b"}
    assert "c" in ids and "d" in ids
    for h in fused:
        assert h.fused is True
        assert h.source_path == "fused"


def test_rrf_single_path_only():
    from chayuan.server.image_source.fusion import rrf_fuse
    only_a = [_hit("a", "text_vec"), _hit("b", "text_vec")]
    fused = rrf_fuse([only_a], k=60)
    assert [h.id for h in fused] == ["a", "b"]


def test_rrf_empty_returns_empty():
    from chayuan.server.image_source.fusion import rrf_fuse
    assert rrf_fuse([], k=60) == []
    assert rrf_fuse([[], []], k=60) == []


def test_rrf_score_formula():
    """k=60, rank=0 → 1/61; rank=1 → 1/62。"""
    from chayuan.server.image_source.fusion import rrf_fuse
    fused = rrf_fuse([[_hit("a", "text_vec"), _hit("b", "text_vec")]], k=60)
    assert abs(fused[0].score - 1/61) < 1e-9
    assert abs(fused[1].score - 1/62) < 1e-9
