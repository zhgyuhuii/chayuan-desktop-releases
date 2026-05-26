"""Pipeline:OCR ‖ CLIP 并行;OCR 失败不阻 ready;CLIP 失败 → failed;
默认文本向量模型不可用 → has_text_vector=false。"""
from __future__ import annotations

import asyncio
import tempfile
import pytest
import numpy as np

from chayuan.server.image_source.ocr_client import OCRResult
from chayuan.server.image_source.text_embed_client import EmbedResult


@pytest.fixture(autouse=True)
def _tmp_root(monkeypatch):
    d = tempfile.mkdtemp(prefix="chayuan_test_pipe_")
    monkeypatch.setenv("CHAYUAN_ROOT", d)
    from chayuan.server.image_source import store as s
    s._STORES.clear()
    yield d


class _Ctx:
    def __init__(self, store_name="kb_pipe"):
        from chayuan.server.image_source.store import get_store
        self.store = get_store(store_name)
        self.store_name = store_name
        self.store.insert_placeholder(
            item_id="img_p1", filename="a.png", mime_type="image/png",
            size_bytes=10, path="/tmp/a.png",
        )


@pytest.mark.asyncio
async def test_pipeline_happy_all_three(monkeypatch):
    """OCR + CLIP + 默认文本向量模型 都成功 → state=ready, has_text_vector=True。"""
    from chayuan.server.image_source import pipeline

    async def _ocr(b, *, port, timeout=30.0):
        return OCRResult(text="hello world", lang="en", confidence=0.95,
                          box_count=2)
    async def _clip(b, *, model_name=""):
        return np.ones(512, dtype="float32") / np.sqrt(512)
    async def _text(t, *, base_url, model, api_key=None, timeout=30.0):
        return EmbedResult(vector=[0.1] * 1024, model=model)

    ctx = _Ctx()
    monkeypatch.setattr(pipeline, "_run_ocr", _ocr)
    monkeypatch.setattr(pipeline, "_run_clip_embed", _clip)
    monkeypatch.setattr(pipeline, "_run_text_embed", _text)
    monkeypatch.setattr(pipeline, "_resolve_ocr_port", lambda: 18380)
    monkeypatch.setattr(pipeline, "_resolve_text_embed_endpoint",
                        lambda: ("http://x", "user-default-embed", None))

    await pipeline.process_item(ctx.store_name, "img_p1", b"fakepng",
                                 model_name="clip-test")
    rec = ctx.store.get("img_p1")
    assert rec["state"] == "ready"
    assert rec["progress"] == 100
    assert rec["ocr_text"] == "hello world"
    assert rec["has_text_vector"] is True
    assert rec["image_vector_id"] == 0


@pytest.mark.asyncio
async def test_pipeline_ocr_fails_still_ready(monkeypatch):
    """OCR 抛错 → 仍然 ready, ocr_text=None, has_text_vector=False。"""
    from chayuan.server.image_source import pipeline

    async def _ocr(b, *, port, timeout=30.0):
        return OCRResult(error="503 rapidocr 未安装")
    async def _clip(b, *, model_name=""):
        return np.ones(512, dtype="float32") / np.sqrt(512)
    async def _text(t, *, base_url, model, api_key=None, timeout=30.0):
        return EmbedResult(error="should not be called")

    ctx = _Ctx()
    monkeypatch.setattr(pipeline, "_run_ocr", _ocr)
    monkeypatch.setattr(pipeline, "_run_clip_embed", _clip)
    monkeypatch.setattr(pipeline, "_run_text_embed", _text)
    monkeypatch.setattr(pipeline, "_resolve_ocr_port", lambda: 18380)
    monkeypatch.setattr(pipeline, "_resolve_text_embed_endpoint",
                        lambda: ("http://x", "user-default-embed", None))

    await pipeline.process_item(ctx.store_name, "img_p1", b"fakepng",
                                 model_name="clip-test")
    rec = ctx.store.get("img_p1")
    assert rec["state"] == "ready"
    assert rec["ocr_text"] is None
    assert rec["has_text_vector"] is False


@pytest.mark.asyncio
async def test_pipeline_clip_fails_is_partial_even_if_ocr_ok(monkeypatch):
    """CLIP 失败 → state=partial,即便 OCR / 文字向量都成功。

    图像 KB 的核心能力是 CLIP 图像向量。CLIP 失败 = 以图搜图不可用 = 没真正
    建好,不能标"成功"(ready);即便 OCR 出了文字、文字向量也建好,也只是
    降级可用 → partial,让用户一眼看出这张图没做图像向量。"""
    from chayuan.server.image_source import pipeline

    async def _ocr(b, *, port, timeout=30.0):
        return OCRResult(text="x", lang="en", confidence=0.9, box_count=1)
    async def _clip(b, *, model_name=""):
        raise RuntimeError("CLIP sidecar dead")
    async def _text(t, *, base_url, model, api_key=None, timeout=30.0):
        return EmbedResult(vector=[0.1] * 1024, model=model)

    ctx = _Ctx()
    monkeypatch.setattr(pipeline, "_run_ocr", _ocr)
    monkeypatch.setattr(pipeline, "_run_clip_embed", _clip)
    monkeypatch.setattr(pipeline, "_run_text_embed", _text)
    monkeypatch.setattr(pipeline, "_resolve_ocr_port", lambda: 18380)
    monkeypatch.setattr(pipeline, "_resolve_text_embed_endpoint",
                        lambda: ("http://x", "user-default-embed", None))

    await pipeline.process_item(ctx.store_name, "img_p1", b"fakepng",
                                 model_name="clip-test")
    rec = ctx.store.get("img_p1")
    assert rec["state"] == "partial"
    assert rec["ocr_text"] == "x"
    assert rec["has_text_vector"] is True
    # image_vector_id 未设(CLIP 失败) — 老 store 可能 default 给 None / 缺字段
    assert rec.get("image_vector_id") is None
    assert "以图搜图不可用" in (rec["error"] or "")


@pytest.mark.asyncio
async def test_pipeline_both_fail_state_partial(monkeypatch):
    """CLIP 和 OCR 都失败 → state=partial(降级 ready),图保留可见,
    error 拼两边原因。用户修完环境后可"重新索引"。"""
    from chayuan.server.image_source import pipeline

    async def _ocr(b, *, port, timeout=30.0):
        return OCRResult(error="503 rapidocr 未安装")
    async def _clip(b, *, model_name=""):
        raise RuntimeError("CLIP sidecar dead")
    async def _text(t, *, base_url, model, api_key=None, timeout=30.0):
        return EmbedResult(error="not called")

    ctx = _Ctx()
    monkeypatch.setattr(pipeline, "_run_ocr", _ocr)
    monkeypatch.setattr(pipeline, "_run_clip_embed", _clip)
    monkeypatch.setattr(pipeline, "_run_text_embed", _text)
    monkeypatch.setattr(pipeline, "_resolve_ocr_port", lambda: 18380)
    monkeypatch.setattr(pipeline, "_resolve_text_embed_endpoint",
                        lambda: ("http://x", "user-default-embed", None))

    await pipeline.process_item(ctx.store_name, "img_p1", b"fakepng",
                                 model_name="clip-test")
    rec = ctx.store.get("img_p1")
    assert rec["state"] == "partial"
    assert rec["progress"] == 100
    assert "CLIP sidecar dead" in (rec["error"] or "")
    assert "rapidocr" in (rec["error"] or "").lower()


@pytest.mark.asyncio
async def test_pipeline_transformers_error_friendly_message(monkeypatch):
    """CLIP 失败原因含 AutoProcessor → error 字段是产品级提示而不是裸 stack。"""
    from chayuan.server.image_source import pipeline

    async def _ocr(b, *, port, timeout=30.0):
        return OCRResult(error="no text")
    async def _clip(b, *, model_name=""):
        raise RuntimeError(
            "embed failed: cannot import name 'AutoProcessor' from 'transformers'"
        )
    async def _text(t, *, base_url, model, api_key=None, timeout=30.0):
        return EmbedResult(error="not called")

    ctx = _Ctx()
    monkeypatch.setattr(pipeline, "_run_ocr", _ocr)
    monkeypatch.setattr(pipeline, "_run_clip_embed", _clip)
    monkeypatch.setattr(pipeline, "_run_text_embed", _text)
    monkeypatch.setattr(pipeline, "_resolve_ocr_port", lambda: 18380)
    monkeypatch.setattr(pipeline, "_resolve_text_embed_endpoint",
                        lambda: (None, None, None))

    await pipeline.process_item(ctx.store_name, "img_p1", b"fakepng",
                                 model_name="clip-test")
    rec = ctx.store.get("img_p1")
    assert rec["state"] == "partial"
    err = rec["error"] or ""
    assert "transformers" in err
    assert "pip install -U transformers" in err
    # 裸 'cannot import name' 不该出现在友好文案里(被翻译掉了)
    assert "AutoProcessor" not in err or "升级" in err


@pytest.mark.asyncio
async def test_pipeline_text_embed_unavailable_soft_degrade(monkeypatch):
    """默认文本向量模型不可用 → 仍 ready,has_text_vector=False。"""
    from chayuan.server.image_source import pipeline

    async def _ocr(b, *, port, timeout=30.0):
        return OCRResult(text="hi", lang="en", confidence=0.9, box_count=1)
    async def _clip(b, *, model_name=""):
        return np.ones(512, dtype="float32") / np.sqrt(512)

    ctx = _Ctx()
    monkeypatch.setattr(pipeline, "_run_ocr", _ocr)
    monkeypatch.setattr(pipeline, "_run_clip_embed", _clip)
    monkeypatch.setattr(pipeline, "_resolve_ocr_port", lambda: 18380)
    monkeypatch.setattr(pipeline, "_resolve_text_embed_endpoint",
                        lambda: (None, None, None))

    await pipeline.process_item(ctx.store_name, "img_p1", b"fakepng",
                                 model_name="clip-test")
    rec = ctx.store.get("img_p1")
    assert rec["state"] == "ready"
    assert rec["ocr_text"] == "hi"
    assert rec["has_text_vector"] is False
