"""文字搜图:两路 RRF + 单路降级 + 双路不可用。"""
from __future__ import annotations

import tempfile
import pytest
import numpy as np

from chayuan.server.image_source.text_embed_client import EmbedResult


@pytest.fixture(autouse=True)
def _tmp_root(monkeypatch):
    d = tempfile.mkdtemp(prefix="chayuan_test_search_")
    monkeypatch.setenv("CHAYUAN_ROOT", d)
    from chayuan.server.image_source import store as s
    s._STORES.clear()
    yield d


def _seed_store(name="kb_search"):
    from chayuan.server.image_source.store import get_store
    s = get_store(name)
    for i, txt in enumerate(["发票", "猫", "山"]):
        iid = f"img_t{i}"
        s.insert_placeholder(
            item_id=iid, filename=f"{iid}.png", mime_type="image/png",
            size_bytes=1, path=f"/tmp/{iid}",
        )
        # 不同 CLIP 向量
        v_img = np.zeros(512, dtype="float32")
        v_img[i] = 1.0
        s.add_image_vector(iid, v_img)
        # 不同文本向量
        v_txt = np.zeros(1024, dtype="float32")
        v_txt[i] = 1.0
        s.add_text_vector(iid, v_txt)
        s.update(iid, state="ready", progress=100, ocr_text=txt,
                  has_text_vector=True)
    return s, name


@pytest.mark.asyncio
async def test_search_two_paths_returns_diagnostics_ok(monkeypatch):
    from chayuan.server.image_source import text_search as ts
    _, name = _seed_store()

    async def _clip_text(q, *, model_name=""):
        v = np.zeros(512, dtype="float32"); v[0] = 1.0
        return v
    async def _text(q, *, base_url, model, api_key=None, timeout=30.0):
        v = [0.0] * 1024; v[1] = 1.0
        return EmbedResult(vector=v, model=model)

    monkeypatch.setattr(ts, "_clip_embed_text", _clip_text)
    monkeypatch.setattr(ts, "_text_embed", _text)
    monkeypatch.setattr(ts, "_resolve_text_embed_endpoint",
                        lambda: ("http://x", "user-default-embed", None))

    out = await ts.text_search(name, "发票", top_k=20)
    assert out["diagnostics"]["text_path"] == "ok"
    assert out["diagnostics"]["image_path"] == "ok"
    ids = [h["id"] for h in out["hits"]]
    assert "img_t1" in ids[:2]
    assert "img_t0" in ids[:2]
    for h in out["hits"]:
        assert h["source_path"] == "fused"


@pytest.mark.asyncio
async def test_search_text_embed_unavail_falls_back_to_clip_only(monkeypatch):
    from chayuan.server.image_source import text_search as ts
    _, name = _seed_store()

    async def _clip_text(q, *, model_name=""):
        v = np.zeros(512, dtype="float32"); v[2] = 1.0
        return v

    monkeypatch.setattr(ts, "_clip_embed_text", _clip_text)
    monkeypatch.setattr(ts, "_resolve_text_embed_endpoint",
                        lambda: (None, None, None))

    out = await ts.text_search(name, "山", top_k=20)
    assert "unavailable" in out["diagnostics"]["text_path"]
    assert out["diagnostics"]["image_path"] == "ok"
    assert out["hits"][0]["id"] == "img_t2"


@pytest.mark.asyncio
async def test_search_both_unavail(monkeypatch):
    from chayuan.server.image_source import text_search as ts
    _, name = _seed_store()

    async def _clip_text(q, *, model_name=""):
        raise RuntimeError("clip dead")

    monkeypatch.setattr(ts, "_clip_embed_text", _clip_text)
    monkeypatch.setattr(ts, "_resolve_text_embed_endpoint",
                        lambda: (None, None, None))

    # 用一个 OCR 不含的 query "xy",确保 keyword 路也没命中,这样三路全空
    out = await ts.text_search(name, "xy", top_k=20)
    assert out["hits"] == []
    assert "unavailable" in out["diagnostics"]["text_path"]
    assert "unavailable" in out["diagnostics"]["image_path"]


@pytest.mark.asyncio
async def test_search_keyword_path_pins_literal_match(monkeypatch):
    """关键词路径让字面命中绝对优先:OCR 文本含'冠军'的图永远排第 1,
    向量召回的'看着像冠军但 OCR 没字'的图最多排第 2+。"""
    from chayuan.server.image_source import text_search as ts
    from chayuan.server.image_source.store import get_store
    s = get_store("kb_keyword")
    # img_a: OCR 没"冠军";clip 向量在 0 维(查 0 维 query 会命中它)
    # img_b: OCR 有"冠军";clip 向量在 1 维(查 0 维 query 完全无关)
    for iid, ocr, idx in (("img_a", "无关文字", 0), ("img_b", "篮球冠军", 1)):
        s.insert_placeholder(item_id=iid, filename=f"{iid}.png",
                              mime_type="image/png", size_bytes=1,
                              path=f"/tmp/{iid}")
        v_img = np.zeros(512, dtype="float32"); v_img[idx] = 1.0
        s.add_image_vector(iid, v_img)
        s.update(iid, state="ready", progress=100, ocr_text=ocr)

    async def _clip_text(q, *, model_name=""):
        # query "冠军" 在 CLIP 嵌入空间被映到 0 维(=img_a 的图像向量),
        # 模拟"视觉像冠军但 OCR 没字"的误召回场景
        v = np.zeros(512, dtype="float32"); v[0] = 1.0
        return v

    monkeypatch.setattr(ts, "_clip_embed_text", _clip_text)
    monkeypatch.setattr(ts, "_resolve_text_embed_endpoint",
                        lambda: (None, None, None))  # 跳过 text vec 路简化测试

    out = await ts.text_search("kb_keyword", "冠军", top_k=20)
    assert out["diagnostics"]["keyword_path"] == "matched 1"
    assert out["hits"], "should return at least the keyword-matched img"
    # img_b 字面命中,必排第 1
    assert out["hits"][0]["id"] == "img_b"
    assert out["hits"][0]["keyword_match"] is True
    # img_a 向量召回,排第 2 但 keyword_match=False(用户 UI 据此打标签)
    ids = [h["id"] for h in out["hits"]]
    if "img_a" in ids:
        a_hit = next(h for h in out["hits"] if h["id"] == "img_a")
        assert a_hit["keyword_match"] is False


@pytest.mark.asyncio
async def test_search_short_query_skips_keyword_path(monkeypatch):
    """单字 query 不跑 keyword path(误命中太多),只走向量路。"""
    from chayuan.server.image_source import text_search as ts
    _, name = _seed_store()

    async def _clip_text(q, *, model_name=""):
        v = np.zeros(512, dtype="float32"); v[0] = 1.0
        return v

    monkeypatch.setattr(ts, "_clip_embed_text", _clip_text)
    monkeypatch.setattr(ts, "_resolve_text_embed_endpoint",
                        lambda: (None, None, None))

    out = await ts.text_search(name, "x", top_k=20)
    assert out["diagnostics"]["keyword_path"] == "matched 0"
