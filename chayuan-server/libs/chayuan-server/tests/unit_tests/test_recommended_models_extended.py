"""90-2:recommended.py 增补本期推荐清单。

新增模型:
  - qwen2.5:7b              (Standard 套餐对话主推)
  - qwen2.5:1.5b            (Lite 套餐对话)
  - qwen2.5-coder:7b        (代码助手)
  - qwen2.5-14b-awq         (Pro 套餐 vLLM)
  - chinese-clip-vit-base-patch16  (中文专图像嵌入)
  - siglip2-base-patch16-224       (SigLIP2 升级)
  - bge-reranker-base       (Lite 重排)
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 新增项必须存在
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model_id,capability,runtime", [
    ("qwen2.5:7b", "chat", "ollama"),
    ("qwen2.5:1.5b", "chat", "ollama"),
    ("qwen2.5-coder:7b", "chat", "ollama"),
    ("qwen2.5-14b-awq", "chat", "vllm"),
    ("chinese-clip-vit-base-patch16", "image-embedding", "infinity"),
    ("siglip2-base-patch16-224", "image-embedding", "infinity"),
    ("bge-reranker-base", "rerank", "infinity"),
])
def test_new_recommended_present(model_id, capability, runtime):
    from chayuan_modelmgr.recommended import get_recommended
    items = {m.id: m for m in get_recommended()}
    assert model_id in items, f"recommended.py 缺 {model_id}"
    m = items[model_id]
    assert m.capability == capability
    assert m.runtime == runtime


def test_qwen25_7b_size_around_4_5gb():
    from chayuan_modelmgr.recommended import get_recommended
    m = {x.id: x for x in get_recommended()}["qwen2.5:7b"]
    # Q4_K_M 量化通常 4.5GB
    assert 3_000 < m.size_mb < 6_000


def test_qwen25_14b_awq_marked_offline_unfriendly():
    """vLLM 模型需 GPU,不在 lite 套餐自动装。"""
    from chayuan_modelmgr.recommended import get_recommended
    m = {x.id: x for x in get_recommended()}["qwen2.5-14b-awq"]
    assert m.offline_friendly is False
    assert m.optional_for_lite is True


def test_lite_image_embedding_chinese_clip_under_500mb():
    """Lite 套餐:中文图像嵌入应当小于 500MB(CPU 兜底)。"""
    from chayuan_modelmgr.recommended import get_recommended
    m = {x.id: x for x in get_recommended()}["chinese-clip-vit-base-patch16"]
    assert m.size_mb < 500


def test_lite_reranker_under_1gb():
    """Lite 重排应当 < 1GB,优于 v2-m3 的 2.3GB。"""
    from chayuan_modelmgr.recommended import get_recommended
    m = {x.id: x for x in get_recommended()}["bge-reranker-base"]
    assert m.size_mb < 1_000


def test_standard_套餐能凑齐对话_嵌入_图像_重排():
    """Standard 套餐至少应能从清单里凑出 4 类完整 capability。"""
    from chayuan_modelmgr.recommended import get_recommended
    by_cap: dict = {}
    for m in get_recommended():
        by_cap.setdefault(m.capability, []).append(m.id)
    assert "qwen2.5:7b" in by_cap.get("chat", [])
    assert "bge-m3" in by_cap.get("text-embedding", [])
    assert "jina-clip-v1" in by_cap.get("image-embedding", [])
    assert "bge-reranker-v2-m3" in by_cap.get("rerank", [])


def test_lite_套餐能凑齐_4_类_capability_全部小于_2gb():
    """Lite 套餐:每个推荐都应 < 2GB(8GB 内存机限制)。"""
    from chayuan_modelmgr.recommended import get_recommended
    lite_picks = {
        "chat":            "qwen2.5:1.5b",
        "text-embedding":  "bge-small-zh-v1.5",
        "image-embedding": "chinese-clip-vit-base-patch16",
        "rerank":          "bge-reranker-base",
    }
    by_id = {m.id: m for m in get_recommended()}
    for cap, mid in lite_picks.items():
        assert mid in by_id, f"Lite 套餐缺 {cap}={mid}"
        assert by_id[mid].size_mb < 2_000, (
            f"Lite {mid} 太大 ({by_id[mid].size_mb}MB)"
        )


def test_pro_套餐有_vllm_chat_选项():
    """Pro 套餐需要至少一个 vLLM 对话模型。"""
    from chayuan_modelmgr.recommended import get_recommended
    vllm_chats = [
        m for m in get_recommended()
        if m.capability == "chat" and m.runtime == "vllm"
    ]
    assert vllm_chats, "Pro 套餐至少要 1 个 vLLM 对话推荐"


def test_no_duplicate_ids_in_recommended_list():
    """id 不能重复(下游 lifecycle.start 用 id 索引)。"""
    from chayuan_modelmgr.recommended import get_recommended
    ids = [m.id for m in get_recommended()]
    assert len(ids) == len(set(ids)), f"重复 id: {ids}"


def test_image_embedding_runtime_all_infinity_post_88():
    """88 题修复后,image-embedding 推荐都标 runtime=infinity。"""
    from chayuan_modelmgr.recommended import get_recommended
    img = [m for m in get_recommended() if m.capability == "image-embedding"]
    for m in img:
        assert m.runtime == "infinity", (
            f"{m.id} runtime={m.runtime},期望 infinity"
        )
