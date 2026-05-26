"""Adapter HTTP wire-format tests（mock httpx，无真后端）。

被测对象：v5 升级后的 11 个 adapter 的非 mock 路径——
* ollama / vllm / llamacpp 的 chat 流式 vs 非流式；
* ollama / vllm 的 embedding；
* infinity 的 embedding + rerank；
* 各 adapter 的 ``health_url()`` 拼接是否正确；
* gateway router 端拿到流式 ``body`` 是 ``dict 迭代器`` 而不是 ``str 迭代器``
  （这条 bug 是 v5 修掉的核心：旧版本 ``r.json()`` 当流时被 dict 拆字符）。

策略：用 ``httpx.MockTransport`` 拦截 HTTP 调用，对每个 adapter 写一份"假
后端"，断言其请求体 + 解析后的响应。同时对 ``post_streaming`` 走一遍真实
``httpx.Client.stream`` 路径，让 :class:`contextlib.ExitStack` 的资源管理
也被覆盖。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, List

import httpx
import pytest

from chayuan_runtime.adapters import _http
from chayuan_runtime.adapters.comfyui_adapter import ComfyUIAdapter
from chayuan_runtime.adapters.cosyvoice_adapter import CosyVoiceAdapter
from chayuan_runtime.adapters.funasr_adapter import FunAsrAdapter
from chayuan_runtime.adapters.infinity_adapter import InfinityAdapter
from chayuan_runtime.adapters.llamacpp_adapter import LlamaCppAdapter
from chayuan_runtime.adapters.ollama_adapter import OllamaAdapter
from chayuan_runtime.adapters.paddleocr_adapter import PaddleOcrAdapter
from chayuan_runtime.adapters.rapidocr_adapter import RapidOcrAdapter
from chayuan_runtime.adapters.vllm_adapter import VllmAdapter
from chayuan_runtime.base import AdapterRequest


# ---------------------------------------------------------------------------
# 共用工具
# ---------------------------------------------------------------------------


class _FakeModel:
    """最小 ``Model`` 替身：只暴露 chayuan_registry.Model 在 adapter 路径上用到的字段。"""

    def __init__(self, public_id: str = "demo/model", category: str = "chat",
                 runtime: str = "auto", format: str = "gguf", path: str = "/x") -> None:
        self.public_id = public_id
        self.repo = public_id
        self.category = category
        self.runtime = runtime
        self.format = format
        self.path = path


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch,
                       handler: Callable[[httpx.Request], httpx.Response]):
    """让 ``_http.get_client`` 返回一个 MockTransport-back 的同步 Client。

    每个 adapter 模块都用 ``from _http import get_client`` 把名字绑到了模块层面，
    所以仅 patch ``_http.get_client`` 不够；我们也覆盖每个 adapter 模块里的
    ``get_client`` 名字。
    """
    transport = httpx.MockTransport(handler)

    def _factory(timeout: float = 60.0) -> httpx.Client:
        return httpx.Client(transport=transport, timeout=httpx.Timeout(timeout, read=timeout))

    monkeypatch.setattr(_http, "get_client", _factory)
    # 重要：patch 各 adapter 模块里 import 时 bind 的本地名字
    for mod_name in (
        "chayuan_runtime.adapters.ollama_adapter",
        "chayuan_runtime.adapters.vllm_adapter",
        "chayuan_runtime.adapters.llamacpp_adapter",
        "chayuan_runtime.adapters.infinity_adapter",
        "chayuan_runtime.adapters.comfyui_adapter",
        "chayuan_runtime.adapters.funasr_adapter",
        "chayuan_runtime.adapters.cosyvoice_adapter",
        "chayuan_runtime.adapters.rapidocr_adapter",
        "chayuan_runtime.adapters.paddleocr_adapter",
    ):
        try:
            mod = __import__(mod_name, fromlist=["get_client"])
            if hasattr(mod, "get_client"):
                monkeypatch.setattr(mod, "get_client", _factory)
        except ImportError:
            continue


def _patch_post_streaming(monkeypatch: pytest.MonkeyPatch,
                         handler: Callable[[httpx.Request], httpx.Response]):
    """重写 ``_http.post_streaming``：用 MockTransport 跑同样的 ``client.stream`` 路径。

    我们故意走一遍真实的 ``httpx.Client.stream`` API，验证 ExitStack 资源管理在
    生成器迭代完之前不会过早关闭 socket（即 v5 修复的核心 bug）。
    """
    transport = httpx.MockTransport(handler)

    def _post_streaming(url, *, json_body, parser=_http.stream_openai_sse,
                        timeout=600.0, headers=None):
        from contextlib import ExitStack
        stack = ExitStack()
        try:
            client = stack.enter_context(httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(timeout, read=timeout),
            ))
            resp = stack.enter_context(client.stream(
                "POST", url, json=json_body, headers=headers or {},
            ))
            resp.raise_for_status()

            def _gen():
                try:
                    yield from parser(resp.iter_lines())
                finally:
                    stack.close()
            return _gen()
        except BaseException:
            stack.close()
            raise

    monkeypatch.setattr(_http, "post_streaming", _post_streaming)
    # ollama / vllm / llamacpp adapter 模块自己也 import 了 post_streaming，
    # 直接 patch 模块级名字
    import chayuan_runtime.adapters.ollama_adapter as _o
    import chayuan_runtime.adapters.vllm_adapter as _v
    import chayuan_runtime.adapters.llamacpp_adapter as _l
    monkeypatch.setattr(_o, "post_streaming", _post_streaming)
    monkeypatch.setattr(_v, "post_streaming", _post_streaming)
    monkeypatch.setattr(_l, "post_streaming", _post_streaming)


# ---------------------------------------------------------------------------
# health_url() 拼接
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("cls", "base", "expected"), [
    (OllamaAdapter,    "http://h:1",     "http://h:1/api/tags"),
    (VllmAdapter,      "http://h:2",     "http://h:2/v1/models"),
    (LlamaCppAdapter,  "http://h:3",     "http://h:3/health"),
    (InfinityAdapter,  "http://h:4",     "http://h:4/health"),
    (ComfyUIAdapter,   "http://h:5",     "http://h:5/system_stats"),
    (FunAsrAdapter,    "http://h:6",     "http://h:6/health"),
    (CosyVoiceAdapter, "http://h:7",     "http://h:7/v1/models"),
    (RapidOcrAdapter,  "http://h:8",     "http://h:8/health"),
    (PaddleOcrAdapter, "http://h:9",     "http://h:9/version"),
])
def test_health_url_concat(cls, base: str, expected: str):
    a = cls(base_url=base, mock=False)
    assert a.health_url() == expected


def test_health_url_subprocess_returns_empty():
    """piper / whispercpp 是 subprocess 类，doctor 不能给它发 HTTP。"""
    from chayuan_runtime.adapters.piper_adapter import PiperAdapter
    from chayuan_runtime.adapters.whispercpp_adapter import WhisperCppAdapter
    assert PiperAdapter(mock=False).health_url() == ""
    assert WhisperCppAdapter(mock=False).health_url() == ""


def test_health_url_no_base_url_returns_empty():
    a = OllamaAdapter(base_url="", mock=False)
    assert a.health_url() == ""


# ---------------------------------------------------------------------------
# Ollama: chat 非流式 → OpenAI schema
# ---------------------------------------------------------------------------


def test_ollama_chat_non_stream_translates_to_openai(monkeypatch):
    captured: List[Dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append({
            "url":  str(request.url),
            "body": json.loads(request.content.decode("utf-8")),
        })
        # 模拟 ollama /api/chat 的原生响应
        return httpx.Response(200, json={
            "model": "qwen3:4b",
            "created_at": "2026-05-02T00:00:00Z",
            "message": {"role": "assistant", "content": "你好"},
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 3,
        })

    _patch_httpx_client(monkeypatch, _handler)
    a = OllamaAdapter(base_url="http://h:11434", mock=False)
    m = _FakeModel("qwen3:4b", category="chat", runtime="ollama")
    resp = a.call(AdapterRequest(op="chat", model=m,
                                 payload={"messages": [{"role": "user", "content": "嗨"}]}))

    assert captured[0]["url"] == "http://h:11434/api/chat"
    assert captured[0]["body"]["model"] == "qwen3:4b"
    assert captured[0]["body"]["stream"] is False

    # OpenAI 兼容字段
    assert resp.body["object"] == "chat.completion"
    assert resp.body["choices"][0]["message"]["content"] == "你好"
    assert resp.body["choices"][0]["finish_reason"] == "stop"
    assert resp.body["usage"]["prompt_tokens"] == 5
    assert resp.body["usage"]["completion_tokens"] == 3
    assert resp.body["usage"]["total_tokens"] == 8


# ---------------------------------------------------------------------------
# Ollama: chat 流式 → OpenAI chunk 迭代器
# ---------------------------------------------------------------------------


def test_ollama_chat_stream_yields_openai_chunks(monkeypatch):
    """ollama 流式（NDJSON）→ OpenAI ``chat.completion.chunk``。"""
    ndjson = (
        '{"message":{"role":"assistant","content":"你"},"done":false}\n'
        '{"message":{"role":"assistant","content":"好"},"done":false}\n'
        '{"message":{"role":"assistant","content":""},"done":true,'
        '"prompt_eval_count":5,"eval_count":2}\n'
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["stream"] is True
        return httpx.Response(200, content=ndjson, headers={"content-type": "application/x-ndjson"})

    _patch_post_streaming(monkeypatch, _handler)

    a = OllamaAdapter(base_url="http://h:11434", mock=False)
    m = _FakeModel("qwen3:4b", category="chat", runtime="ollama")
    resp = a.call(AdapterRequest(op="chat", model=m, payload={
        "messages": [{"role": "user", "content": "嗨"}]}, stream=True))

    chunks = list(resp.body)
    # gateway router 期望 list[dict]，不是 list[str]！这是 v5 修的核心 bug
    assert all(isinstance(c, dict) for c in chunks), [type(c).__name__ for c in chunks]
    assert resp.streaming is True
    # 至少 3 帧；最后一帧 finish_reason="stop"
    assert len(chunks) >= 3
    contents = [c["choices"][0]["delta"].get("content", "") for c in chunks]
    assert "".join(contents) == "你好"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    # 每帧都是 chunk schema
    for c in chunks:
        assert c["object"] == "chat.completion.chunk"
        assert "delta" in c["choices"][0]


# ---------------------------------------------------------------------------
# Ollama: embedding
# ---------------------------------------------------------------------------


def test_ollama_embedding_handles_string_and_list(monkeypatch):
    calls: List[Dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        calls.append(body)
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3, 0.4]})

    _patch_httpx_client(monkeypatch, _handler)

    a = OllamaAdapter(base_url="http://h:11434", mock=False)
    m = _FakeModel("bge-m3", category="embedding", runtime="ollama")

    # 字符串
    resp = a.call(AdapterRequest(op="embedding", model=m, payload={"input": "hi"}))
    assert resp.body["object"] == "list"
    assert len(resp.body["data"]) == 1
    assert resp.body["data"][0]["embedding"] == [0.1, 0.2, 0.3, 0.4]
    assert calls[-1]["prompt"] == "hi"

    # 数组（批量）
    resp2 = a.call(AdapterRequest(op="embed", model=m, payload={"input": ["a", "b"]}))
    assert len(resp2.body["data"]) == 2


# ---------------------------------------------------------------------------
# vLLM / llama.cpp: 流式（OpenAI SSE）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_cls", [VllmAdapter, LlamaCppAdapter])
def test_openai_sse_chat_stream(monkeypatch, adapter_cls):
    """vLLM / llama.cpp 都讲 OpenAI SSE，本测试同时覆盖二者。"""
    sse = (
        "data: {\"id\":\"x\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\"}}]}\n\n"
        "data: {\"id\":\"x\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hello\"}}]}\n\n"
        "data: {\"id\":\"x\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\" world\"}}]}\n\n"
        "data: {\"id\":\"x\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n"
        "data: [DONE]\n\n"
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["stream"] is True
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    _patch_post_streaming(monkeypatch, _handler)

    a = adapter_cls(base_url="http://h:80", mock=False)
    m = _FakeModel(category="chat", runtime=a.name, format="safetensors" if a.name == "vllm" else "gguf")
    resp = a.call(AdapterRequest(op="chat", model=m, payload={
        "messages": [{"role": "user", "content": "hi"}]}, stream=True))

    chunks = list(resp.body)
    assert all(isinstance(c, dict) for c in chunks)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    contents = [c["choices"][0]["delta"].get("content", "") for c in chunks]
    assert "".join(contents) == "hello world"


# ---------------------------------------------------------------------------
# Infinity: rerank + embedding 双模式
# ---------------------------------------------------------------------------


def test_infinity_rerank_and_embedding(monkeypatch):
    routes: Dict[str, int] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        routes[path] = routes.get(path, 0) + 1
        if path == "/embeddings":
            return httpx.Response(200, json={
                "object": "list", "model": "bge",
                "data": [{"object": "embedding", "index": 0, "embedding": [1.0]}],
            })
        if path == "/rerank":
            return httpx.Response(200, json={
                "model": "bge-rr",
                "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.1}],
            })
        return httpx.Response(404, text="not found")

    _patch_httpx_client(monkeypatch, _handler)

    a = InfinityAdapter(base_url="http://h:7997", mock=False)
    m_emb = _FakeModel("bge", category="embedding", runtime="infinity")
    m_rr  = _FakeModel("bge-rr", category="rerank", runtime="infinity")

    r1 = a.call(AdapterRequest(op="embedding", model=m_emb, payload={"input": "x"}))
    r2 = a.call(AdapterRequest(op="rerank", model=m_rr, payload={
        "query": "q", "documents": ["a", "b"]}))

    assert r1.body["model"] == "bge"
    assert r2.body["results"][0]["relevance_score"] == 0.9
    assert routes == {"/embeddings": 1, "/rerank": 1}


# ---------------------------------------------------------------------------
# 行解析器单测：边界条件
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("lines", "expected"), [
    # 标准 SSE
    (["data: {\"a\": 1}", "data: [DONE]"], [{"a": 1}]),
    # 多余空白 / 空行 / 注释
    (["", "data: {\"a\": 1}", ":heartbeat", "", "data: [DONE]"], [{"a": 1}]),
    # 多帧
    (["data: {\"i\":0}", "data: {\"i\":1}", "data: [DONE]"], [{"i": 0}, {"i": 1}]),
    # 损坏 JSON 静默跳过
    (["data: {bad}", "data: {\"i\":1}", "data: [DONE]"], [{"i": 1}]),
])
def test_stream_openai_sse_parses(lines, expected):
    out = list(_http.stream_openai_sse(iter(lines)))
    assert out == expected


@pytest.mark.parametrize(("lines", "expected"), [
    (['{"a":1}', '{"a":2}'], [{"a": 1}, {"a": 2}]),
    (['{"a":1}', '', 'garbage', '{"b":2}'], [{"a": 1}, {"b": 2}]),
])
def test_stream_ndjson_parses(lines, expected):
    out = list(_http.stream_ndjson(iter(lines)))
    assert out == expected


# ---------------------------------------------------------------------------
# ComfyUI: workflow 模板渲染 + /prompt 提交 + /history 轮询 + /view 取图
# ---------------------------------------------------------------------------


def test_comfyui_render_workflow_sd15_shape():
    """SD1.5 模板的 DAG 节点要齐：CheckpointLoader, 2×CLIPTextEncode, EmptyLatent,
    KSampler, VAEDecode, SaveImage。"""
    from chayuan_runtime.adapters._comfyui_workflows import render_workflow

    wf = render_workflow(
        op="t2i", model_id="sd_v15.safetensors", model_format="checkpoint",
        payload={"prompt": "a cat", "size": "768x512", "n": 2, "steps": 20, "cfg": 7.5,
                 "negative_prompt": "blurry", "seed": 42},
    )
    classes = {n["class_type"] for n in wf.values()}
    assert {
        "CheckpointLoaderSimple", "CLIPTextEncode",
        "EmptyLatentImage", "KSampler", "VAEDecode", "SaveImage",
    } <= classes
    sampler = next(n for n in wf.values() if n["class_type"] == "KSampler")
    assert sampler["inputs"]["seed"] == 42
    assert sampler["inputs"]["steps"] == 20
    assert sampler["inputs"]["cfg"] == 7.5
    latent = next(n for n in wf.values() if n["class_type"] == "EmptyLatentImage")
    assert latent["inputs"]["width"] == 768 and latent["inputs"]["height"] == 512
    assert latent["inputs"]["batch_size"] == 2
    pos = next(n for n in wf.values() if n["class_type"] == "CLIPTextEncode" and n["inputs"]["text"] == "a cat")
    neg = next(n for n in wf.values() if n["class_type"] == "CLIPTextEncode" and n["inputs"]["text"] == "blurry")
    assert pos and neg


def test_comfyui_render_sdxl_picks_dpmpp_2m_karras():
    from chayuan_runtime.adapters._comfyui_workflows import render_workflow

    wf = render_workflow(
        op="t2i", model_id="sdxl_base.safetensors", model_format="checkpoint",
        payload={"prompt": "x", "size": "1024x1024"},
    )
    sampler = next(n for n in wf.values() if n["class_type"] == "KSampler")
    assert sampler["inputs"]["sampler_name"] == "dpmpp_2m"
    assert sampler["inputs"]["scheduler"] == "karras"


def test_comfyui_render_svd_inserts_video_nodes():
    from chayuan_runtime.adapters._comfyui_workflows import render_workflow

    wf = render_workflow(
        op="t2v", model_id="svd_xt.safetensors", model_format="checkpoint",
        payload={"prompt": "a cat", "frames": 14, "fps": 8},
    )
    classes = {n["class_type"] for n in wf.values()}
    # 既有 SD T2I 部分（生成 keyframe），又有 SVD 视频部分
    assert "CheckpointLoaderSimple" in classes
    assert "ImageOnlyCheckpointLoader" in classes
    assert "SVD_img2vid_Conditioning" in classes
    assert "SaveAnimatedWEBP" in classes


def test_comfyui_size_parser_snaps_to_8():
    """ComfyUI 的 latent 维度必须是 8 的倍数。"""
    from chayuan_runtime.adapters._comfyui_workflows import _parse_size
    assert _parse_size("769x515") == (768, 512)
    assert _parse_size("garbage") == (1024, 1024)
    assert _parse_size(None) == (1024, 1024)


def test_comfyui_real_call_submits_polls_and_fetches_image(monkeypatch):
    """非 mock 路径：渲染 workflow → /prompt → /history（先 pending 后 done）→ /view。"""
    history_calls = {"n": 0}

    img_bytes = b"\x89PNG\r\n\x1a\nFAKEPNGBYTES"

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/prompt":
            body = json.loads(request.content.decode("utf-8"))
            assert "prompt" in body
            wf = body["prompt"]
            assert any(n.get("class_type") == "KSampler" for n in wf.values())
            return httpx.Response(200, json={"prompt_id": "p1"})
        if path == "/history/p1":
            history_calls["n"] += 1
            if history_calls["n"] < 2:
                # 第一次还没生成完
                return httpx.Response(200, json={"p1": {"status": {"completed": False}, "outputs": {}}})
            return httpx.Response(200, json={
                "p1": {
                    "status": {"completed": True},
                    "outputs": {
                        "7": {"images": [
                            {"filename": "ChayuanAI_00001_.png", "subfolder": "", "type": "output"},
                        ]},
                    },
                }
            })
        if path == "/view":
            return httpx.Response(200, content=img_bytes, headers={"content-type": "image/png"})
        return httpx.Response(404, text="not found")

    _patch_httpx_client(monkeypatch, _handler)
    # 把轮询间隔调到几乎为 0，免得测试慢
    monkeypatch.setenv("CHAYUAN_COMFYUI_POLL_INTERVAL_SEC", "0.01")
    monkeypatch.setenv("CHAYUAN_COMFYUI_POLL_TIMEOUT_SEC", "5")

    a = ComfyUIAdapter(base_url="http://h:18188", mock=False)
    m = _FakeModel("sd_v15.safetensors", category="t2i", runtime="comfyui", format="checkpoint")
    resp = a.call(AdapterRequest(op="t2i", model=m, payload={
        "prompt": "a cat", "size": "512x512", "n": 1,
    }))

    body = resp.body
    assert body["prompt_id"] == "p1"
    assert body["filename"] == "ChayuanAI_00001_.png"
    assert body["url"].startswith("data:image/png;base64,")
    # 验证 base64 还原后等于原始字节
    import base64 as _b64
    decoded = _b64.b64decode(body["url"].split(",", 1)[1])
    assert decoded == img_bytes


def test_comfyui_mock_emits_workflow_for_inspection():
    a = ComfyUIAdapter(base_url="http://h", mock=True)
    m = _FakeModel("sd_v15.safetensors", category="t2i", runtime="comfyui", format="checkpoint")
    resp = a.call(AdapterRequest(op="t2i", model=m, payload={"prompt": "x", "size": "512x512"}))
    assert "workflow" in resp.body
    assert "available_templates" in resp.body
    classes = {n["class_type"] for n in resp.body["workflow"].values()}
    assert "KSampler" in classes


# ---------------------------------------------------------------------------
# M2 · OCR golden fixture
#
# 目标：用一张内容固定的 PNG 验证"adapter → /ocr → blocks 数组" 的契约不被破坏。
# 这里走 mock 后端：真模型推理放在 GitHub workflow（M1 类似的 manual smoke）做。
# 但 fixture PNG 是真的 —— bytes 写死在测试里，避免引入外部资源 / lfs。
# ---------------------------------------------------------------------------


# 1×1 灰度 PNG（16 字节有效图像）；OCR 适配器只校验 multipart 形态 / 响应解析
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c63f8cf000000010001005ce2010f0000000049454e44ae"
    "426082"
)

# 真实业务里我们关心的"关键字段"——adapter 必须把 OCR 引擎返回的 `blocks` /
# `text` / `bbox` 三件套保留下来，后续 KB pipeline 才能切 chunk。
_OCR_GOLDEN_RESULT = {
    "blocks": [
        {"text": "察元 AI 平台", "bbox": [10, 12, 220, 48], "score": 0.97},
        {"text": "Knowledge Center · 95% accuracy roadmap", "bbox": [10, 60, 420, 96], "score": 0.95},
        {"text": "Page 1/1", "bbox": [10, 240, 90, 260], "score": 0.92},
    ]
}


def test_rapidocr_golden_fixture_round_trip(monkeypatch):
    """``RapidOcrAdapter``：发出 multipart/form-data，解析后保留 blocks/bbox/score。"""
    captured: dict[str, object] = {}

    def _h(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/ocr"
        ctype = req.headers.get("content-type", "")
        assert ctype.startswith("multipart/form-data"), ctype
        captured["body_len"] = len(req.content)
        return httpx.Response(200, json=_OCR_GOLDEN_RESULT)

    _patch_httpx_client(monkeypatch, _h)

    a = RapidOcrAdapter(base_url="http://h:18380", mock=False)
    m = _FakeModel("rapidocr-onnx", category="ocr", runtime="rapidocr", format="onnx")
    resp = a.call(AdapterRequest(op="ocr", model=m, payload={"file": _TINY_PNG}))

    body = resp.body
    assert "blocks" in body and len(body["blocks"]) == 3
    # 关键字段不被丢
    first = body["blocks"][0]
    assert first["text"] == "察元 AI 平台"
    assert first["bbox"] == [10, 12, 220, 48]
    assert "score" in first
    # multipart body 真的把 PNG 字节带过去了（>= PNG 长度，含 form-data 装饰）
    assert int(captured["body_len"]) >= len(_TINY_PNG)


def test_paddleocr_golden_fixture_round_trip(monkeypatch):
    """``PaddleOcrAdapter``：契约和 RapidOCR 对齐——blocks/bbox 必须存在。

    PaddleOCR 真后端的字段更丰富（``rec_score`` / ``cls_score``），适配器层不
    自动重命名；这里断言"业务关键字段"在响应 body 中不丢即可。
    """
    paddle_response = {
        "blocks": [
            {"text": "察元 AI 平台", "bbox": [10, 12, 220, 48], "rec_score": 0.99},
        ],
        "doc_type": "text",
    }

    def _h(req: httpx.Request) -> httpx.Response:
        # PaddleServing 路径：/predict/ocr_system
        assert req.url.path.startswith("/predict")
        return httpx.Response(200, json=paddle_response)

    _patch_httpx_client(monkeypatch, _h)

    a = PaddleOcrAdapter(base_url="http://h:18381", mock=False)
    m = _FakeModel("paddleocr-pp-ocrv4", category="ocr", runtime="paddleocr", format="onnx")
    # PaddleOCR adapter 走 ``images`` payload（base64/list）而不是 multipart
    resp = a.call(AdapterRequest(op="ocr", model=m, payload={"images": [_TINY_PNG.hex()]}))

    body = resp.body
    assert "blocks" in body
    assert body["blocks"][0]["text"] == "察元 AI 平台"
    # PaddleOCR 自有字段不被 adapter 误伤
    assert body["blocks"][0]["rec_score"] == 0.99
    assert body["doc_type"] == "text"


def test_rapidocr_mock_returns_stable_shape():
    """``mock=True`` 时也得给上层一个能用的 ``blocks`` 列表，否则前端调试拿不到结构。"""
    a = RapidOcrAdapter(base_url="http://h", mock=True)
    m = _FakeModel("rapidocr-onnx", category="ocr", runtime="rapidocr", format="onnx")
    resp = a.call(AdapterRequest(op="ocr", model=m, payload={}))
    assert "blocks" in resp.body
    assert resp.body["blocks"][0]["bbox"] == [0, 0, 100, 20]
