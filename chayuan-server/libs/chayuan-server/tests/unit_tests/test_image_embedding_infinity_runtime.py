"""88 题:image-embedding capability 应允许 infinity 作为 target runtime。

Infinity (michaelfeil/infinity) 原生支持 CLIP/SigLIP 等跨模态 embedding,
recommended.py 也已把 jina-clip-v1 / siglip 标为 runtime="infinity"。
若 _CAPABILITY_RUNTIMES["image-embedding"] 不含 infinity,模型广场过滤
"图像嵌入" 时下拉里就没有 infinity 选项 → 用户其实下不到 Infinity。
"""
from __future__ import annotations


def test_image_embedding_runtimes_include_infinity():
    from chayuan.server.model_registry.marketplace import (
        _CAPABILITY_RUNTIMES,
    )
    assert "infinity" in _CAPABILITY_RUNTIMES["image-embedding"], (
        "image-embedding 必须把 infinity 列入候选 runtime,"
        "否则 jina-clip-v1 / siglip 等推荐模型在 UI 上无法选 Infinity。"
    )


def test_text_embedding_runtimes_still_include_infinity():
    """回归保护:text-embedding 仍包含 infinity。"""
    from chayuan.server.model_registry.marketplace import (
        _CAPABILITY_RUNTIMES,
    )
    assert "infinity" in _CAPABILITY_RUNTIMES["text-embedding"]


def test_rerank_runtimes_still_include_infinity():
    """回归保护:rerank 仍包含 infinity。"""
    from chayuan.server.model_registry.marketplace import (
        _CAPABILITY_RUNTIMES,
    )
    assert "infinity" in _CAPABILITY_RUNTIMES["rerank"]


def test_recommended_image_embedding_runtime_matches_capability_map():
    """recommended.py 里 image-embedding 模型的 runtime 都得在
    _CAPABILITY_RUNTIMES["image-embedding"] 候选里;否则下载流程会拒绝。
    """
    from chayuan.server.model_registry.marketplace import _CAPABILITY_RUNTIMES
    from chayuan_modelmgr.recommended import get_recommended

    allowed = set(_CAPABILITY_RUNTIMES["image-embedding"])
    img_models = get_recommended("image-embedding")
    assert img_models, "recommended.py 至少应有一个 image-embedding 模型"
    for m in img_models:
        assert m.runtime in allowed, (
            f"{m.id} 标 runtime={m.runtime}, 但 _CAPABILITY_RUNTIMES "
            f"image-embedding 候选 {allowed} 不含它"
        )
