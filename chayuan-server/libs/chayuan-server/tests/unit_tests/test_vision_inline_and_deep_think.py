"""单测:vision_inline + deep_think。

vision_inline:
  - 本地 http://127.0.0.1 / localhost URL → base64 data URL
  - 相对 /v1/files/<id>/content → base64 data URL
  - 公网 https://... → 原样
  - 已经是 data: URL → 原样
  - 找不到本地文件 → 原 URL 兜底(让上层报错好定位)

deep_think:
  - qwen3-* / qwen-plus / qwen-max → extra_body.enable_thinking=true
  - claude-opus-4 / claude-sonnet-4 → extra_body.thinking={...}
  - o1-/o3-/deepseek-reasoner → 空 dict(模型自带推理)
  - 其它模型 → 空 dict + 一条 INFO 日志
  - deep_think=False → 永远空 dict
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import pytest

from chayuan.server.chat.deep_think import compute_deep_think_kwargs
from chayuan.server.chat.vision_inline import (
    _is_local_url,
    inline_local_image_url,
)


# ──────────────────────────────────────────────────────────────
# vision_inline
# ──────────────────────────────────────────────────────────────


def test_is_local_url():
    assert _is_local_url("http://127.0.0.1:62581/x")
    assert _is_local_url("http://localhost:62581/x")
    assert _is_local_url("/v1/files/abc/content")
    assert not _is_local_url("https://cdn.example.com/x.png")
    assert not _is_local_url("data:image/png;base64,xxxx")
    assert not _is_local_url("")


def test_inline_passthrough_for_public_url():
    url = "https://cdn.example.com/foo.png"
    assert inline_local_image_url(url) == url


def test_inline_passthrough_for_data_url():
    url = "data:image/png;base64,iVBORw0KGgoAAA=="
    assert inline_local_image_url(url) == url


def test_inline_passthrough_when_local_file_missing():
    """本地解析失败 → 返原 URL,让上层报错而不是悄悄改成空。"""
    # 一个根本不存在的 file_id
    bad_url = "http://127.0.0.1:62581/v1/files/nonexistent_file_id/content"
    assert inline_local_image_url(bad_url) == bad_url


def test_inline_artifacts_url(tmp_path, monkeypatch):
    """/v1/artifacts/<sha>.<ext> 路径接 modality.router.artifacts 反查。"""
    import chayuan.server.modality.router.artifacts as art

    fake = tmp_path / "abc123.png"
    fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    monkeypatch.setattr(art, "find_by_filename", lambda name: fake if name == "abc123.png" else None)

    url = "http://127.0.0.1:62581/v1/artifacts/abc123.png"
    out = inline_local_image_url(url)
    assert out.startswith("data:image/png;base64,")
    expected = base64.b64encode(fake.read_bytes()).decode("ascii")
    assert out.endswith(expected)


# ──────────────────────────────────────────────────────────────
# deep_think
# ──────────────────────────────────────────────────────────────


def test_deep_think_off_always_empty():
    assert compute_deep_think_kwargs("qwen-plus", False) == {}
    assert compute_deep_think_kwargs("claude-opus-4", False) == {}


def test_deep_think_qwen_family():
    expected = {"extra_body": {"enable_thinking": True}}
    for name in (
        "qwen-plus", "qwen-max-latest", "qwen-turbo",
        "qwen3-coder-plus", "qwen3-omni-flash",
        "qwen-flash", "qwen-omni-turbo",
        "Qwen-Max",                    # 大小写不敏感
        "bailian::qwen3-coder-plus",  # platform 前缀剥掉
    ):
        assert compute_deep_think_kwargs(name, True) == expected, name


def test_deep_think_claude_family():
    out = compute_deep_think_kwargs("claude-opus-4-20251022", True)
    assert out == {"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 8000}}}

    out = compute_deep_think_kwargs("claude-sonnet-4-1206", True)
    assert "thinking" in out["extra_body"]
    assert out["extra_body"]["thinking"]["type"] == "enabled"


def test_deep_think_reasoning_models_noop():
    """o-series / deepseek-reasoner 推理是模型内置,API 调用不需要传额外参数。"""
    assert compute_deep_think_kwargs("o1-mini", True) == {}
    assert compute_deep_think_kwargs("o3-mini-2025-01-31", True) == {}
    assert compute_deep_think_kwargs("deepseek-reasoner", True) == {}


def test_deep_think_unknown_model_logs_info(caplog):
    caplog.set_level(logging.INFO, logger="chayuan.chat.deep_think")
    out = compute_deep_think_kwargs("gpt-4o-mini", True)
    assert out == {}
    assert any(
        "deep_think" in rec.message and "gpt-4o-mini" in rec.message
        for rec in caplog.records
    )
