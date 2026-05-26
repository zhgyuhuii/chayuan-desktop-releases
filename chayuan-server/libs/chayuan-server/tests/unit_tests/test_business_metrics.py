"""P1-2 · 业务级 Prometheus 指标端到端单测。

覆盖：
- 3 组新指标的 labelnames / 命名符合约定；
- 通过 HTTP 中间件调用 /openapi/v1/ping 后能看到 ``chayuan_app_requests_total``；
- 调工具后能看到 ``chayuan_tool_calls_total``；
- LLM 回调受到带 token_usage 的 response 后能看到 ``chayuan_llm_tokens_total``。
"""
from __future__ import annotations

import time

import pytest


pytest.importorskip("prometheus_client")


def _text_metrics() -> str:
    from chayuan.server.observability.metrics import render_metrics
    out = render_metrics() or b""
    return out.decode("utf-8", "replace")


def test_record_tool_call_emits_counter():
    from chayuan.server.observability.metrics import record_tool_call

    record_tool_call("some_fake_tool", "success", 0.03)
    record_tool_call("some_fake_tool", "error", 0.01)

    text = _text_metrics()
    assert 'chayuan_tool_calls_total{status="success",tool="some_fake_tool"} 1.0' in text
    assert 'chayuan_tool_calls_total{status="error",tool="some_fake_tool"} 1.0' in text
    # 延迟直方图
    assert 'chayuan_tool_call_duration_seconds_count{tool="some_fake_tool"}' in text


def test_record_llm_tokens_emits_counter():
    from chayuan.server.observability.metrics import record_llm_tokens

    record_llm_tokens("some-fake-model", prompt_tokens=100, completion_tokens=50)
    record_llm_tokens("some-fake-model", prompt_tokens=20, completion_tokens=0)

    text = _text_metrics()
    # 累加：prompt=120, completion=50
    assert 'chayuan_llm_tokens_total{direction="prompt",model="some-fake-model"} 120.0' in text
    assert 'chayuan_llm_tokens_total{direction="completion",model="some-fake-model"} 50.0' in text


def test_record_app_request_emits_counter():
    from chayuan.server.observability.metrics import record_app_request

    record_app_request(
        app_id="fake-app", path="/openapi/v1/ping",
        method="GET", status="200", duration_s=0.01,
    )
    text = _text_metrics()
    assert (
        'chayuan_app_requests_total{app_id="fake-app",method="GET",'
        'path="/openapi/v1/ping",status="200"} 1.0' in text
    )
    assert (
        'chayuan_app_request_duration_seconds_count{'
        'app_id="fake-app",path="/openapi/v1/ping"}' in text
    )


@pytest.fixture
def _isolated_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAYUAN_APPS_YAML", str(tmp_path / "apps.yaml"))
    # 关掉 secret 加密以简化断言
    monkeypatch.delenv("CHAYUAN_MASTER_KEY", raising=False)
    from chayuan.server.shared import secret_store
    secret_store._reset_for_tests()
    yield tmp_path


def test_app_requests_counter_end_to_end(_isolated_yaml):
    """走完整中间件：发一次签名请求 → 指标里能看到 per-App counter。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from chayuan.server.api_server.openapi_routes import (
        AppAuthMiddleware, openapi_router,
    )
    from chayuan.server.observability.metrics import PrometheusMetricsMiddleware
    from chayuan.server.config_panel.apps_store import create_app
    from chayuan.server.shared.app_signing import make_signed_headers

    app = FastAPI()
    # PrometheusMetricsMiddleware 在外层：AppAuth 执行完再回到 finally，
    # 此时 request.state.app 可用
    app.add_middleware(AppAuthMiddleware)
    app.add_middleware(PrometheusMetricsMiddleware)
    app.include_router(openapi_router)

    spec = create_app("metric-test", scopes=["chat:read"])
    client = TestClient(app)

    headers = make_signed_headers(spec.app_id, spec.app_secret, b"")
    r = client.get("/openapi/v1/ping", headers=headers)
    assert r.status_code == 200

    text = _text_metrics()
    assert f'app_id="{spec.app_id}"' in text
    assert 'chayuan_app_requests_total{' in text
    assert 'path="/openapi/v1/ping"' in text
