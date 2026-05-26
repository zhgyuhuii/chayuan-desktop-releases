"""GuidanceCard NiceGUI 端纯函数测试.

不渲染 UI(NiceGUI 需要 client 上下文),只测纯函数:tone token / 平台检测 /
schema 数据类。
"""
from __future__ import annotations

import platform as _platform

import pytest

from chayuan.server.config_panel._guidance_card import (
    GuidancePlatformBlock,
    GuidanceStep,
    _detect_server_platform,
    _PLATFORM_LABELS,
    _TONE_TOKENS,
)


def test_all_four_tones_have_tokens():
    for tone in ("warning", "info", "success", "danger"):
        assert tone in _TONE_TOKENS
        toks = _TONE_TOKENS[tone]
        for key in ("bg", "border", "icon", "icon_color", "title_color"):
            assert key in toks
            assert toks[key]


def test_platform_labels_cover_all():
    for p in ("macos", "windows", "linux", "all"):
        assert _PLATFORM_LABELS[p]


def test_detect_server_platform_consistent():
    p = _detect_server_platform()
    assert p in ("macos", "windows", "linux", "all")
    sys_name = _platform.system().lower()
    if "darwin" in sys_name:
        assert p == "macos"
    if "linux" in sys_name:
        assert p == "linux"
    if "windows" in sys_name:
        assert p == "windows"


def test_step_dataclass_only_text_required():
    s = GuidanceStep(text="just a step")
    assert s.command is None


def test_block_dataclass_can_carry_doc_href():
    b = GuidancePlatformBlock(
        platform="all",
        steps=[GuidanceStep(text="x")],
        doc_href="https://example.com",
    )
    assert b.doc_href == "https://example.com"
    assert len(b.steps) == 1
