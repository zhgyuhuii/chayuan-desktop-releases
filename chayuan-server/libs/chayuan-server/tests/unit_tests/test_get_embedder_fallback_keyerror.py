"""hotfix:get_embedder 拿到云 API 模型名时不再 KeyError 死循环。

bug 表现:
  * ``默认图像嵌入`` 设成 ``qwen-vl-max @ bailian`` / ``openai/clip-vit-base-patch32``
  * 这俩都不在 SUPPORTED_MODELS
  * 旧 fallback 是再调一次 ``default_model_name()`` 拿同一个错误名字
  * ``SUPPORTED_MODELS[name]`` KeyError → 422 上传失败
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_get_embedder_falls_back_to_builtin_when_resolved_default_invalid():
    """resolve_default 返云 API 模型 → fallback 到内置 SigLIP2,不再 KeyError。"""
    from chayuan.server.image_source import embedder

    fake_emb = MagicMock()
    fake_emb.is_available = MagicMock(return_value=True)

    fake_supported = {
        embedder._BUILTIN_FALLBACK_MODEL: MagicMock(),
    }
    embedder._invalidate_embedder_cache(None)

    with patch.object(embedder, "default_model_name",
                      return_value="qwen-vl-max"), \
         patch.object(embedder, "SUPPORTED_MODELS", fake_supported), \
         patch("chayuan.server.image_source.loaders.create_embedder",
               return_value=fake_emb):
        emb = embedder.get_embedder()

    # 拿到的是兜底模型实例,而不是 KeyError
    assert emb is fake_emb
    embedder._invalidate_embedder_cache(None)


def test_get_embedder_explicit_arg_unsupported_falls_back():
    """显式传 unsupported model_name → 仍 fallback 到内置默认。"""
    from chayuan.server.image_source import embedder

    fake_emb = MagicMock()
    fake_supported = {embedder._BUILTIN_FALLBACK_MODEL: MagicMock()}
    embedder._invalidate_embedder_cache(None)

    with patch.object(embedder, "default_model_name",
                      return_value=embedder._BUILTIN_FALLBACK_MODEL), \
         patch.object(embedder, "SUPPORTED_MODELS", fake_supported), \
         patch("chayuan.server.image_source.loaders.create_embedder",
               return_value=fake_emb):
        emb = embedder.get_embedder("openai/clip-vit-base-patch32")
    assert emb is fake_emb
    embedder._invalidate_embedder_cache(None)


def test_get_embedder_supported_model_skips_fallback():
    """传一个真在 SUPPORTED_MODELS 里的 model → 直接用,不走 fallback。"""
    from chayuan.server.image_source import embedder

    real_emb = MagicMock()
    fake_supported = {
        "x/real-model": "spec",
        embedder._BUILTIN_FALLBACK_MODEL: "fb-spec",
    }
    embedder._invalidate_embedder_cache(None)

    with patch.object(embedder, "SUPPORTED_MODELS", fake_supported), \
         patch("chayuan.server.image_source.loaders.create_embedder",
               return_value=real_emb) as creator:
        embedder.get_embedder("x/real-model")
    # creator 收到的 spec 应当是 "spec"(真模型的),而非 "fb-spec"
    assert creator.call_args[0][0] == "spec"
    embedder._invalidate_embedder_cache(None)


def test_get_embedder_raises_unavailable_when_even_builtin_missing():
    """连兜底模型都不在 SUPPORTED_MODELS → 抛 EmbedderUnavailable(明确错误)。"""
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    embedder._invalidate_embedder_cache(None)
    with patch.object(embedder, "default_model_name",
                      return_value="qwen-vl-max"), \
         patch.object(embedder, "SUPPORTED_MODELS", {}):
        with pytest.raises(EmbedderUnavailable):
            embedder.get_embedder()


def test_get_embedder_logs_warning_with_helpful_message(caplog):
    """fallback 时 WARN 应该带上原模型名 + 兜底模型名,便于排错。"""
    import logging
    from chayuan.server.image_source import embedder

    fake_supported = {embedder._BUILTIN_FALLBACK_MODEL: MagicMock()}
    embedder._invalidate_embedder_cache(None)

    with caplog.at_level(logging.WARNING,
                         logger="chayuan.image_source.embedder"), \
         patch.object(embedder, "default_model_name",
                      return_value="qwen-vl-max"), \
         patch.object(embedder, "SUPPORTED_MODELS", fake_supported), \
         patch("chayuan.server.image_source.loaders.create_embedder",
               return_value=MagicMock()):
        embedder.get_embedder()

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "qwen-vl-max" in text
    assert embedder._BUILTIN_FALLBACK_MODEL in text
    embedder._invalidate_embedder_cache(None)
