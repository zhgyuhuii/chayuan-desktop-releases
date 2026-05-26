"""89-12:embedder 降级矩阵 trace + 监控指标。"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


def test_emit_client_metric_silent_when_metrics_unavailable():
    """指标系统不可用 → 静默。"""
    from chayuan.server.image_source.embedder import _emit_client_metric

    with patch(
        "chayuan.server.observability.metrics._ensure_metrics",
        side_effect=ImportError("prometheus_client missing"),
    ):
        _emit_client_metric("inproc", "ok")  # 不抛


def test_emit_client_metric_calls_counter():
    """指标可用 → 调 .labels().inc()。"""
    from chayuan.server.image_source.embedder import _emit_client_metric

    fake_counter = MagicMock()
    fake_label = MagicMock()
    fake_counter.labels.return_value = fake_label

    with patch(
        "chayuan.server.observability.metrics._ensure_metrics",
        return_value={"image_embedder_calls_total": fake_counter},
    ):
        _emit_client_metric("infinity", "ok")
    fake_counter.labels.assert_called_once_with(client_kind="infinity", status="ok")
    fake_label.inc.assert_called_once()


def test_emit_fallback_metric_records_transition():
    from chayuan.server.image_source.embedder import _emit_fallback_metric

    fake_counter = MagicMock()
    fake_label = MagicMock()
    fake_counter.labels.return_value = fake_label

    with patch(
        "chayuan.server.observability.metrics._ensure_metrics",
        return_value={"image_embedder_fallback_total": fake_counter},
    ):
        _emit_fallback_metric("infinity", "inproc")
    fake_counter.labels.assert_called_once_with(from_kind="infinity", to_kind="inproc")
    fake_label.inc.assert_called_once()


def test_get_client_emits_ok_on_primary_success():
    """primary 成功 → status=ok 计数 +1。"""
    from chayuan.server.image_source import embedder

    embedder._invalidate_client_cache(None)
    fake = MagicMock()
    fake.healthcheck = MagicMock(return_value=True)
    fake.kind = "infinity"
    fake.model_id = "j/c"
    fake.close = MagicMock()

    emit = MagicMock()
    with patch.object(embedder, "resolve_default",
                      return_value=("j/c", "infinity-local")), \
         patch.object(embedder, "pick_client", return_value=fake), \
         patch.object(embedder, "_emit_client_metric", emit):
        embedder.get_client()
    emit.assert_any_call("infinity", "ok")
    embedder._invalidate_client_cache(None)


def test_get_client_emits_error_then_fallback_on_infinity_fail():
    """infinity 失败 + inproc 成功 → 应该有 primary error + fallback inc + fallback metric。"""
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    embedder._invalidate_client_cache(None)
    fake_inproc = MagicMock()
    fake_inproc.healthcheck = MagicMock(return_value=True)
    fake_inproc.kind = "inproc"
    fake_inproc.model_id = "j/c"
    fake_inproc.close = MagicMock()

    def _pick(model_id, platform):
        if platform and platform.startswith("infinity"):
            raise EmbedderUnavailable("Infinity unreachable")
        return fake_inproc

    emit_client = MagicMock()
    emit_fb = MagicMock()
    with patch.object(embedder, "resolve_default",
                      return_value=("j/c", "infinity-local")), \
         patch.object(embedder, "pick_client", side_effect=_pick), \
         patch("chayuan.server.image_source.embedder_clients.inproc."
               "InProcEmbedderClient", return_value=fake_inproc), \
         patch.object(embedder, "_emit_client_metric", emit_client), \
         patch.object(embedder, "_emit_fallback_metric", emit_fb):
        embedder.get_client()

    emit_client.assert_any_call("infinity", "error")
    emit_client.assert_any_call("inproc", "fallback")
    emit_fb.assert_called_once_with("infinity", "inproc")
    embedder._invalidate_client_cache(None)


def test_get_client_logs_warning_on_fallback(caplog):
    """fallback 路径必须 WARN 日志,UI 才能拿到提示。"""
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    embedder._invalidate_client_cache(None)
    fake_inproc = MagicMock()
    fake_inproc.healthcheck = MagicMock(return_value=True)
    fake_inproc.kind = "inproc"
    fake_inproc.close = MagicMock()

    def _pick(model_id, platform):
        if platform and platform.startswith("infinity"):
            raise EmbedderUnavailable("infinity unreachable")
        return fake_inproc

    with caplog.at_level(logging.WARNING, logger="chayuan.image_source.embedder"), \
         patch.object(embedder, "resolve_default",
                      return_value=("j/c", "infinity-local")), \
         patch.object(embedder, "pick_client", side_effect=_pick), \
         patch("chayuan.server.image_source.embedder_clients.inproc."
               "InProcEmbedderClient", return_value=fake_inproc):
        embedder.get_client()

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "降级 inproc" in text or "fallback" in text.lower()
    embedder._invalidate_client_cache(None)


def test_get_client_logs_error_when_all_fail(caplog):
    """全失败 → ERROR 级别日志。"""
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    embedder._invalidate_client_cache(None)

    with caplog.at_level(logging.ERROR, logger="chayuan.image_source.embedder"), \
         patch.object(embedder, "resolve_default",
                      return_value=("ghost/x", "infinity-local")), \
         patch.object(embedder, "pick_client",
                      side_effect=EmbedderUnavailable("primary down")), \
         patch("chayuan.server.image_source.embedder_clients.inproc."
               "InProcEmbedderClient",
               side_effect=EmbedderUnavailable("inproc down")):
        with pytest.raises(EmbedderUnavailable):
            embedder.get_client()

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "全部失败" in text
    embedder._invalidate_client_cache(None)


def test_fallback_result_cached_under_primary_key_no_repeat_warning(caplog):
    """hotfix:fallback 命中后,下次同 (model_id, platform) 直接走缓存,
    不再重新执行 fallback 路径,日志不再刷 WARN。
    """
    import logging
    from chayuan.server.image_source import embedder
    from chayuan.server.image_source.embedder_clients.base import (
        EmbedderUnavailable,
    )

    embedder._invalidate_client_cache(None)

    fake_inproc = MagicMock()
    fake_inproc.healthcheck = MagicMock(return_value=True)
    fake_inproc.kind = "inproc"
    fake_inproc.model_id = "google/siglip2-base-patch16-224"
    fake_inproc.close = MagicMock()

    def _pick(model_id, platform):
        if platform and not platform.startswith("infinity"):
            raise EmbedderUnavailable(f"云 API platform={platform} 不是图像嵌入")
        return fake_inproc

    with patch.object(embedder, "resolve_default",
                      return_value=("qwen-vl-max", "bailian")), \
         patch.object(embedder, "pick_client", side_effect=_pick), \
         patch("chayuan.server.image_source.embedder_clients.inproc."
               "InProcEmbedderClient", return_value=fake_inproc):
        # 第一次:走 fallback,打 WARN
        with caplog.at_level(logging.WARNING,
                             logger="chayuan.image_source.embedder"):
            cli1 = embedder.get_client()
            warns_after_first = [r for r in caplog.records
                                 if r.levelno >= logging.WARNING]
            caplog.clear()
            # 第二次:应该缓存命中,不再 fallback,不打 WARN
            cli2 = embedder.get_client()
            warns_after_second = [r for r in caplog.records
                                  if r.levelno >= logging.WARNING]

    assert cli1 is cli2  # 同实例
    assert len(warns_after_first) >= 1, "首次 fallback 应有 WARN"
    assert len(warns_after_second) == 0, (
        f"二次调用不应再有 WARN,实测: {[r.getMessage() for r in warns_after_second]}"
    )
    embedder._invalidate_client_cache(None)


def test_metrics_module_registers_image_embedder_counters():
    """metrics 模块 _ensure_metrics 应该注册 image embedder 三个指标。"""
    try:
        import prometheus_client  # noqa: F401
    except ImportError:
        pytest.skip("prometheus_client not installed")
    from chayuan.server.observability.metrics import _ensure_metrics

    m = _ensure_metrics()
    expected = [
        "image_embedder_calls_total",
        "image_embedder_fallback_total",
        "image_embedder_call_duration",
        "image_embedder_batch_size",
    ]
    for k in expected:
        assert k in m, f"metric {k} missing"
