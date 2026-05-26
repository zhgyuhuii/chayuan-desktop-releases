"""验证 plan v1.3 §5.5 KB 指标的存在 + helper 行为正确（fail-open + label 准确）。"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_metrics_module_exposes_kb_helpers():
    """三个 KB-related helper 必须存在且可调用。"""
    from chayuan.server.observability import metrics as m
    assert callable(m.record_kb_search)
    assert callable(m.record_kb_denied)
    assert callable(m.record_kb_download)


def test_kb_search_metric_records_when_enabled(monkeypatch):
    """开启 metrics 时调用必触发 Counter.inc + Histogram.observe。"""
    pytest.importorskip("prometheus_client")
    from chayuan.server.observability import metrics as m

    monkeypatch.setattr(m, "is_metrics_enabled", lambda: True)
    # 确保指标对象被构造一次
    m_dict = m._ensure_metrics()
    assert "kb_search_total" in m_dict
    assert "kb_search_duration" in m_dict
    assert "kb_denied_total" in m_dict
    assert "kb_download_total" in m_dict

    # 直接 sample 当前 counter 值
    counter = m_dict["kb_search_total"]
    sample_before = sum(s.value for fam in counter.collect() for s in fam.samples
                        if s.name.endswith("_total")
                        and s.labels.get("subject_kind") == "user"
                        and s.labels.get("fusion") == "rrf"
                        and s.labels.get("status") == "ok")
    m.record_kb_search("user", "rrf", "ok", duration_s=0.123,
                       sub_queries=3, chunks_returned=7)
    sample_after = sum(s.value for fam in counter.collect() for s in fam.samples
                       if s.name.endswith("_total")
                       and s.labels.get("subject_kind") == "user"
                       and s.labels.get("fusion") == "rrf"
                       and s.labels.get("status") == "ok")
    assert sample_after == sample_before + 1.0


def test_kb_denied_metric_records_code_label(monkeypatch):
    pytest.importorskip("prometheus_client")
    from chayuan.server.observability import metrics as m

    monkeypatch.setattr(m, "is_metrics_enabled", lambda: True)
    m._ensure_metrics()
    m.record_kb_denied("app", 4032, "read")
    counter = m._ensure_metrics()["kb_denied_total"]
    samples = [s for fam in counter.collect() for s in fam.samples if s.name.endswith("_total")]
    matched = [s for s in samples if s.labels.get("subject_kind") == "app" and s.labels.get("code") == "4032"]
    assert matched, "kb_denied_total 必须按 subject/code 维度可查"


def test_kb_download_metric_records_via_dl_token(monkeypatch):
    pytest.importorskip("prometheus_client")
    from chayuan.server.observability import metrics as m

    monkeypatch.setattr(m, "is_metrics_enabled", lambda: True)
    m._ensure_metrics()
    m.record_kb_download("dl_token", True, "ok")
    counter = m._ensure_metrics()["kb_download_total"]
    samples = [s for fam in counter.collect() for s in fam.samples if s.name.endswith("_total")]
    matched = [s for s in samples
               if s.labels.get("subject_kind") == "dl_token"
               and s.labels.get("via_dl_token") == "1"
               and s.labels.get("status") == "ok"]
    assert matched, "via_dl_token=1 应正确编码为 label '1'"


def test_kb_metrics_no_op_when_disabled(monkeypatch):
    """metrics_enabled=False 时所有 record 必须 no-op + 不抛异常。"""
    from chayuan.server.observability import metrics as m
    monkeypatch.setattr(m, "is_metrics_enabled", lambda: False)
    # 这些调用不应抛异常,也不应触发 _ensure_metrics()
    m.record_kb_search("user", "rrf", "ok", duration_s=0.5)
    m.record_kb_denied("app", 4032, "read")
    m.record_kb_download("user", False, "ok")


def test_subject_kind_helper_matches_metrics_labels():
    """kb_audit.subject_kind_of 必须返回 metrics labels 用的 user/app/dl_token 词汇。"""
    from chayuan.server.auth import kb_audit

    assert kb_audit.subject_kind_of(None) == "unknown"
    assert kb_audit.subject_kind_of({"id": 5, "username": "alice", "role": "user"}) == "user"
    assert kb_audit.subject_kind_of({"id": 5, "username": "alice", "role": "admin"}) == "user"
    assert kb_audit.subject_kind_of({"id": "app:abc", "role": "app"}) == "app"
    assert kb_audit.subject_kind_of({"id": "anything", "role": "dl_token"}) == "dl_token"

    class FakeAppSpec:
        app_id = "demo"
        name = "demo-app"
        role = ""
    assert kb_audit.subject_kind_of(FakeAppSpec()) == "app"
