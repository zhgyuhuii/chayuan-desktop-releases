"""70 题:游客模式 — AUTH_REQUIRED=false 时,get_current_user 返 GUEST_USER。

所有依赖 get_current_user 的路由(包括严格模式 ai_space_routes 等)在游客
模式下不再 401,允许用户单机使用 chayuan-client 全部功能。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from chayuan.server.auth.deps import (
    GUEST_USER,
    get_current_user,
    get_current_user_optional,
    require_auth_enabled,
)


def _make_request(state_user=None):
    """构造一个 FastAPI Request 替身,只关心 request.state.user。"""
    req = MagicMock()
    req.state.user = state_user
    return req


# ---------------------------------------------------------------------------
# 已登录:不论 AUTH_REQUIRED,直接返真实 user
# ---------------------------------------------------------------------------


def test_logged_in_returns_real_user():
    real = {"id": 42, "username": "alice", "role": "admin"}
    req = _make_request(state_user=real)
    out = get_current_user(req)
    assert out == real


# ---------------------------------------------------------------------------
# 未登录 + AUTH_REQUIRED=true:抛 401(原行为)
# ---------------------------------------------------------------------------


def test_unauth_with_auth_required_raises_401():
    req = _make_request(state_user=None)
    fake_settings = MagicMock()
    fake_settings.basic_settings.AUTH_REQUIRED = True
    with patch("chayuan.settings.Settings", fake_settings):
        with pytest.raises(HTTPException) as excinfo:
            get_current_user(req)
    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# 未登录 + AUTH_REQUIRED=false:返 GUEST_USER(70 题新行为)
# ---------------------------------------------------------------------------


def test_unauth_with_auth_disabled_returns_guest():
    """单机应用 / 非强制登录场景:游客身份透明放行。"""
    req = _make_request(state_user=None)
    fake_settings = MagicMock()
    fake_settings.basic_settings.AUTH_REQUIRED = False
    with patch("chayuan.settings.Settings", fake_settings):
        out = get_current_user(req)
    assert out["id"] == -1
    assert out["username"] == "guest"
    assert out["role"] == "user"
    assert out["is_guest"] is True


def test_guest_user_returned_is_a_copy():
    """每次调用返新 dict,避免某路由意外篡改全局 GUEST_USER。"""
    req = _make_request(state_user=None)
    fake_settings = MagicMock()
    fake_settings.basic_settings.AUTH_REQUIRED = False
    with patch("chayuan.settings.Settings", fake_settings):
        out1 = get_current_user(req)
        out2 = get_current_user(req)
    assert out1 == out2
    assert out1 is not out2  # 不同对象,修改 out1 不影响下次
    out1["evil"] = "injected"
    assert "evil" not in GUEST_USER


def test_unauth_settings_unavailable_treats_as_disabled():
    """Settings import 失败 → 视作 AUTH_REQUIRED=false,返 guest 不抛。"""
    req = _make_request(state_user=None)
    # 让 Settings 抛
    import builtins
    real_import = builtins.__import__

    def _no_settings(name, *a, **kw):
        if "chayuan.settings" in name:
            raise ImportError("simulated")
        return real_import(name, *a, **kw)

    with patch.object(builtins, "__import__", _no_settings):
        out = get_current_user(req)
    assert out["is_guest"] is True


# ---------------------------------------------------------------------------
# get_current_user_optional 行为不受影响(它本来就是 None-tolerant)
# ---------------------------------------------------------------------------


def test_optional_returns_none_for_unauth():
    req = _make_request(state_user=None)
    assert get_current_user_optional(req) is None


def test_optional_returns_real_user():
    real = {"id": 5, "username": "bob", "role": "user"}
    req = _make_request(state_user=real)
    assert get_current_user_optional(req) == real


# ---------------------------------------------------------------------------
# require_auth_enabled 与 get_current_user 一致性
# ---------------------------------------------------------------------------


def test_require_auth_enabled_with_disabled_returns_optional_or_none():
    """AUTH_REQUIRED=false 时,require_auth_enabled 走 optional 路径。

    注意:require_auth_enabled 的 None 路径返 None(不是 GUEST_USER) — 这是
    历史行为,与 get_current_user 在 70 题新行为不同。如未来要统一可在此 test
    锁住。
    """
    fake_settings = MagicMock()
    fake_settings.basic_settings.AUTH_REQUIRED = False
    dep = require_auth_enabled()
    req = _make_request(state_user=None)
    with patch("chayuan.settings.Settings", fake_settings):
        out = dep(req)
    assert out is None  # require_auth_enabled 走 optional


def test_require_auth_enabled_with_required_strict():
    """AUTH_REQUIRED=true 时,require_auth_enabled 走 strict get_current_user。

    未登录会抛(因为现在的 strict 路径上面已 patch 成 guest 兜底,但
    AUTH_REQUIRED=true 时仍 401)。
    """
    fake_settings = MagicMock()
    fake_settings.basic_settings.AUTH_REQUIRED = True
    dep = require_auth_enabled()
    req = _make_request(state_user=None)
    with patch("chayuan.settings.Settings", fake_settings):
        with pytest.raises(HTTPException) as excinfo:
            dep(req)
    assert excinfo.value.status_code == 401
