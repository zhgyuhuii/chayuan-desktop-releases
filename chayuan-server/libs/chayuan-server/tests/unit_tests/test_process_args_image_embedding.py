"""resolve_image_embedding_args 分支测试 (Plan 3D)。"""
from __future__ import annotations

import pytest

from chayuan.server.model_registry import process_args


def _fake_entry(model_id, fmt, path):
    return type("Entry", (), {
        "model_id": model_id,
        "format": fmt,
        "path": path,
        "capability": "image",
    })()


def test_resolve_image_embedding_args_image_embedding_default(monkeypatch):
    """capability=image-embedding(默认)走 image default,args 含 -m + 模块名 + --model。"""
    e = _fake_entry("openai/clip-vit-base-patch32", "transformers", "/tmp/clip")
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_image_embedding_args()
    assert r.process == "infinity"
    assert "-m" in r.args
    assert "chayuan.server.image_source.infinity_server" in r.args
    assert "--model" in r.args
    assert "openai/clip-vit-base-patch32" in r.args
    assert r.resolved_models["image-embedding"] == "openai/clip-vit-base-patch32"


def test_resolve_image_embedding_args_unknown_capability_raises():
    """非 image-embedding 的 capability 抛 ValueError。"""
    with pytest.raises(ValueError, match="capability"):
        process_args.resolve_image_embedding_args(capability="chat")  # type: ignore[arg-type]


def test_resolve_image_embedding_args_missing_model_reports_image_embedding(monkeypatch):
    """模型未解到时 missing 列表里是 'image-embedding'(不是硬编码 chat)。"""
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (None, "no image candidate"))
    r = process_args.resolve_image_embedding_args()
    assert "image-embedding" in r.missing
    assert r.reason == "no image candidate"


def test_resolve_image_embedding_args_frozen_mode(monkeypatch):
    """PyInstaller frozen 模式:args 不带 '-m',改用 --sidecar-mode。"""
    import sys
    from chayuan.server.model_registry import process_args
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    e = type("Entry", (), {
        "model_id": "siglip2-base", "format": "transformers", "path": "/tmp",
        "capability": "image",
    })()
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_image_embedding_args()
    # frozen: 不能 -m,改 --sidecar-mode 参数(让 chayuan-server.exe 自我转化)
    assert "-m" not in r.args
    assert "--sidecar-mode" in r.args
    assert "image-embedding" in r.args
    assert "--model" in r.args
    assert "siglip2-base" in r.args


def test_resolve_image_embedding_args_dev_mode_uses_m(monkeypatch):
    """非 frozen(开发模式):args 用 '-m module'。"""
    import sys
    from chayuan.server.model_registry import process_args
    monkeypatch.delattr(sys, "frozen", raising=False)

    e = type("Entry", (), {
        "model_id": "siglip2-base", "format": "transformers", "path": "/tmp",
        "capability": "image",
    })()
    monkeypatch.setattr(process_args, "_resolve", lambda cap, **kw: (e, ""))
    r = process_args.resolve_image_embedding_args()
    assert "-m" in r.args
    assert "chayuan.server.image_source.infinity_server" in r.args
    assert "--sidecar-mode" not in r.args
