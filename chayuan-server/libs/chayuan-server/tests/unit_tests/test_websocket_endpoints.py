"""WebSocket 端点：存储、鉴权注入、动态工具注册 的端到端单测。

不做真实网络请求：鉴权逻辑可以纯离线验；动态注册只看 ``_TOOLS_REGISTRY``
是否被正确填充 / 清理；真实 ``probe_websocket`` 需要一个测试 ws server，
复杂度高，不在单测范围。
"""
from __future__ import annotations

import pytest


@pytest.fixture
def _isolated_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CHAYUAN_WS_ENDPOINTS_YAML", str(tmp_path / "websocket_endpoints.yaml"),
    )
    yield tmp_path


def test_validate_spec_rejects_bad_url(_isolated_yaml):
    from chayuan.server.config_panel.websocket_endpoints_store import (
        WSEndpointSpec, validate_spec,
    )
    s = WSEndpointSpec(name="x", description="d", url="http://not-ws")
    errs = validate_spec(s)
    assert any("ws://" in e or "wss://" in e for e in errs)


def test_auth_applies_to_url_and_headers():
    from chayuan.server.shared.websocket_client import AuthSpec, _apply_auth

    # query
    url, hdrs = _apply_auth("wss://e.com/ws", AuthSpec(type="query", key="token", value="abc"))
    assert "token=abc" in url and hdrs == {}

    # header
    url, hdrs = _apply_auth("wss://e.com/ws", AuthSpec(type="header", key="X-K", value="v"))
    assert url == "wss://e.com/ws" and hdrs == {"X-K": "v"}

    # bearer
    url, hdrs = _apply_auth("wss://e.com/ws", AuthSpec(type="bearer", value="tk"))
    assert hdrs["Authorization"] == "Bearer tk"


def test_frame_body_roundtrip():
    from chayuan.server.shared.websocket_client import _frame_body, _extract_path

    kind, body = _frame_body('{"data":{"price":42.5}}')
    assert kind == "json" and body == {"data": {"price": 42.5}}
    assert _extract_path(body, "data.price") == 42.5
    assert _extract_path(body, "data.nope") is None

    # 纯文本
    kind, body = _frame_body("hello")
    assert kind == "text" and body == "hello"

    # bytes
    kind, body = _frame_body(b'{"a":1}')
    assert kind == "json" and body == {"a": 1}


def test_crud_and_dynamic_registration(_isolated_yaml):
    from chayuan.server.config_panel.websocket_endpoints_store import (
        WSAuth, WSEndpointSpec, WSParamSpec, list_endpoints, save_endpoint,
    )
    from chayuan.server.agent.tools_factory import websocket_tools_runtime
    from chayuan.server.agent.tools_factory.tools_registry import _TOOLS_REGISTRY

    spec = WSEndpointSpec(
        name="ws_unit_test",
        title="UT WS",
        description="unit test",
        url="wss://echo.example.com/ws",
        auth=WSAuth(type="bearer", value="tok"),
        on_connect=['{"op":"subscribe","symbol":"{sym}"}'],
        request_template='{"action":"get","symbol":"{sym}"}',
        message_format="json",
        response_path="data.price",
        max_messages=3,
        receive_timeout=5,
        close_after_first=False,
        params=[WSParamSpec(name="sym", type="string", required=True)],
        enabled=True,
    )
    save_endpoint(spec)
    assert [e.name for e in list_endpoints()] == ["ws_unit_test"]

    websocket_tools_runtime.load_and_register()
    tool = _TOOLS_REGISTRY.get("ws_unit_test")
    assert tool is not None
    assert tool.args_schema.model_fields["sym"].is_required()

    # 禁用 → reload 后从注册表消失
    spec.enabled = False
    save_endpoint(spec)
    websocket_tools_runtime.load_and_register()
    assert "ws_unit_test" not in _TOOLS_REGISTRY
