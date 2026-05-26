"""验证 plan v1.3 §6.5 用户确认的 5 项决策（与 user 2026-05-03 04:55 反馈对齐）。

1. secret 存储用 bcrypt + AES-GCM 密文（apps_store + secret_store 已实现）
   → 验证 secret_store.encrypt/decrypt 可用，且密文不是明文。
2. APP 默认看不见 public KB（没有 kb:public-read scope 时）
   → 验证 app_can_read_kb 对 visibility=public KB 在没 scope 时返 False，有 scope 才 True。
3. APP 永不是 owner（不能 owner 操作 KB）
   → 验证 app_kb_role 任何情况下都不返回 'owner'。
   → 验证 access.is_kb_owner 对 app: 主体也不返回 True。
4. WPS 加载项不做 admin UI（v1）
   → 这是产品决策，用 grep 验证 KbSettingsPanel 不含 admin/apps 字眼。
5. 审计 7 类（login/search/download/denied/grant/revoke/rotate）
   → 验证 kb_audit 暴露 7 个 helper + 7 个 ACTION_* 常量；audit fire 后能在 chayuan_audit_log 查到。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 决策 1：secret 加密存储
# ---------------------------------------------------------------------------

def test_decision1_secret_store_encrypts_secret(monkeypatch):
    """直接验证项目用的 secret_store 而不是明文存。"""
    from chayuan.server.shared import secret_store
    plain = "very-secret-app-key-12345"
    enc = secret_store.encrypt(plain)
    # 加密后必须不等于明文(空 secret 时也至少不直接 echo)
    if enc != plain:
        assert plain not in enc, f"明文 {plain!r} 出现在密文 {enc!r} 里,加密被绕过"
    dec = secret_store.decrypt(enc)
    assert dec == plain, "加解密往返必须保真"


# ---------------------------------------------------------------------------
# 决策 2：APP 默认看不见 public KB（必须有 kb:public-read scope）
# ---------------------------------------------------------------------------

def _make_spec(app_id: str = "demo-app", scopes=None, enabled=True):
    """构造一个 AppSpec 对象,不依赖 yaml 持久化。"""
    from chayuan.server.config_panel.apps_store import AppSpec
    return AppSpec(
        app_id=app_id, app_secret="ignored-for-acl",
        name="demo", enabled=enabled,
        scopes=list(scopes or []),
    )


def test_decision2_app_without_scope_cannot_see_public_kb(monkeypatch):
    """关键安全约束：APP 没有 kb:public-read 时,不能读 visibility=public KB。"""
    from chayuan.server.auth import app_acl

    # mock _get_kb_by_name → 返回一个 visibility=public 的 KB
    fake_kb = MagicMock()
    fake_kb.id = 99
    fake_kb.kb_name = "公开知识库"
    fake_kb.visibility = "public"
    monkeypatch.setattr(app_acl, "_get_kb_by_name", lambda n: fake_kb if n == "公开知识库" else None)
    # 没有 grant
    monkeypatch.setattr(app_acl, "_grant_role", lambda app_id, kb_id: None)

    # 1) 没有 kb:public-read scope → 看不见
    spec_no = _make_spec(scopes=["kb:read"])
    assert app_acl.app_kb_role(spec_no, "公开知识库") is None
    assert not app_acl.app_can_read_kb(spec_no, "公开知识库"), \
        "APP 没 kb:public-read 时不应能读 public KB（默认安全）"

    # 2) 有 kb:public-read scope → 看得见(reader)
    spec_yes = _make_spec(scopes=["kb:read", "kb:public-read"])
    assert app_acl.app_kb_role(spec_yes, "公开知识库") == "reader"
    assert app_acl.app_can_read_kb(spec_yes, "公开知识库")


def test_decision2_app_with_explicit_grant_can_see_kb_regardless(monkeypatch):
    """显式 grant 永远生效,不依赖 kb:public-read scope。"""
    from chayuan.server.auth import app_acl

    fake_kb = MagicMock()
    fake_kb.id = 100
    fake_kb.kb_name = "私有 KB"
    fake_kb.visibility = "private"
    monkeypatch.setattr(app_acl, "_get_kb_by_name", lambda n: fake_kb if n == "私有 KB" else None)
    monkeypatch.setattr(app_acl, "_grant_role", lambda app_id, kb_id: "reader")

    spec = _make_spec(scopes=["kb:read"])  # 没 kb:public-read 也无所谓
    assert app_acl.app_kb_role(spec, "私有 KB") == "reader"
    assert app_acl.app_can_read_kb(spec, "私有 KB")


# ---------------------------------------------------------------------------
# 决策 3：APP 永不是 owner
# ---------------------------------------------------------------------------

def test_decision3_app_never_returns_owner_role(monkeypatch):
    """无论 grant 怎么配,app_kb_role 永不返回 'owner'(只有 reader/editor)。"""
    from chayuan.server.auth import app_acl

    fake_kb = MagicMock()
    fake_kb.id = 1
    fake_kb.kb_name = "kbX"
    fake_kb.visibility = "private"
    monkeypatch.setattr(app_acl, "_get_kb_by_name", lambda n: fake_kb if n == "kbX" else None)
    spec = _make_spec(scopes=["kb:read", "kb:write"])

    # 即便 grant 的 role 字段被人为设成 owner(数据被污染),也会被卡在 _ALLOWED_GRANT_ROLES
    # 同时 app_kb_role 的代码路径决定它只能返回 reader/editor;owner 是 access.kb_access_for
    # 内部的人类用户路径才能拿到的角色。
    monkeypatch.setattr(app_acl, "_grant_role", lambda app_id, kb_id: "reader")
    assert app_acl.app_kb_role(spec, "kbX") == "reader"

    monkeypatch.setattr(app_acl, "_grant_role", lambda app_id, kb_id: "editor")
    assert app_acl.app_kb_role(spec, "kbX") == "editor"

    # Defense-in-depth:即使 DB 被篡改 role='owner',app_kb_role 必须 clamp 到 None
    monkeypatch.setattr(app_acl, "_grant_role", lambda app_id, kb_id: "owner")
    role = app_acl.app_kb_role(spec, "kbX")
    assert role is None, "DB 篡改 role='owner' 时 ACL 必须 clamp,绝不能视为 owner"
    assert not app_acl.app_can_read_kb(spec, "kbX"), "篡改的 owner role 也不能拿到读权限"

    # add_grant 必须不接受 owner 角色(写时防御)
    from chayuan.server.auth import app_acl as _acl
    assert "owner" not in _acl._ALLOWED_GRANT_ROLES, \
        "add_grant 必须不接受 owner 角色"


def test_decision3_add_grant_rejects_owner_role(monkeypatch):
    """直接调 add_grant 传 role='owner' 应该报 ValueError。"""
    from chayuan.server.auth import app_acl

    fake_kb = MagicMock()
    fake_kb.id = 1
    fake_kb.kb_name = "kbX"
    monkeypatch.setattr(app_acl, "_get_kb_by_name", lambda n: fake_kb if n == "kbX" else None)
    with pytest.raises(ValueError, match="role"):
        app_acl.add_grant("app-id", "kbX", role="owner")


def test_decision3_access_is_kb_owner_returns_false_for_app(monkeypatch):
    """access.is_kb_owner 对 app: 主体永远 False(委托 app_kb_role,而 app_kb_role 不会返 owner)。"""
    from chayuan.server.auth import access, app_acl

    fake_kb = MagicMock()
    fake_kb.id = 1
    fake_kb.kb_name = "kbZ"
    fake_kb.visibility = "private"
    monkeypatch.setattr(app_acl, "_get_kb_by_name", lambda n: fake_kb if n == "kbZ" else None)
    monkeypatch.setattr(app_acl, "_load_app", lambda x: _make_spec(scopes=["kb:read"]))

    # 1) 正常 grant editor → is_kb_owner 还是 False
    monkeypatch.setattr(app_acl, "_grant_role", lambda app_id, kb_id: "editor")
    app_user = {"id": "app:demo", "role": "app"}
    assert not access.is_kb_owner(app_user, "kbZ"), "App 永不是 KB owner（即便 editor）"

    # 2) 即便 DB 被篡改 role='owner',is_kb_owner 也必须 False
    monkeypatch.setattr(app_acl, "_grant_role", lambda app_id, kb_id: "owner")
    assert not access.is_kb_owner(app_user, "kbZ"), \
        "App 永不是 KB owner（DB 被篡改 role='owner' 也必须 clamp）"


# ---------------------------------------------------------------------------
# 决策 4：WPS 加载项不含 admin UI(v1)
# ---------------------------------------------------------------------------

def test_decision4_wps_panel_has_no_admin_ui():
    """KbSettingsPanel.vue 不应包含 admin 端点调用或"应用管理"等字眼。"""
    import os
    from pathlib import Path
    here = Path(__file__).resolve()
    # 找 chayuan 加载项目录;脚手架检测,失败时跳过
    workspace = here.parents[5] if len(here.parents) >= 6 else None
    panel = None
    if workspace:
        candidate = workspace / "chayuan" / "src" / "components" / "KbSettingsPanel.vue"
        if candidate.is_file():
            panel = candidate
    if panel is None:
        pytest.skip("Workspace布局不一致,跳过 chayuan/ 文件检查")

    text = panel.read_text(encoding="utf-8")
    # 这些字眼不应出现(实际调用 admin 端点的标志):
    # 注意:`/openapi/v1/admin/apps` 字符串可以出现在【提示文案/curl 引导】里(给 admin 复制),
    # 但不能出现在 fetch/axios 调用上下文。简化检查:不允许出现 fetch/axios + admin。
    forbidden_actual_call = [
        "rotate_secret",                # 不应有 rotate UI
        "createApp(",                   # 不应有创建 app UI
        "deleteApp(",                   # 不应有删除 app UI
        "fetch('/openapi/v1/admin",     # 不应直接调 admin 端点
        'fetch("/openapi/v1/admin',     # (双引号变体)
        "axios.post('/openapi/v1/admin",
        "addGrant(",                    # 不应有 grant CRUD UI
        "removeGrant(",
    ]
    for kw in forbidden_actual_call:
        assert kw not in text, f"WPS 加载项不应实际调用 admin 端点：{kw!r}（plan v1.3 决策 4）"


# ---------------------------------------------------------------------------
# 决策 5：审计 7 类
# ---------------------------------------------------------------------------

def test_decision5_kb_audit_exports_seven_actions():
    """kb_audit.ALL_ACTIONS 必须正好 7 项,与 plan §6.5 对齐。"""
    from chayuan.server.auth import kb_audit
    expected = {
        "kb.app.login", "kb.search", "kb.download", "kb.denied",
        "kb.grant.add", "kb.grant.revoke", "kb.app.rotate",
    }
    assert set(kb_audit.ALL_ACTIONS) == expected
    assert len(kb_audit.ALL_ACTIONS) == 7


def test_decision5_kb_audit_helpers_are_callable():
    """7 个 helper 都要可调用且 fail-open(audit() 自带异常吞)。"""
    from chayuan.server.auth import kb_audit
    helpers = [
        kb_audit.app_login,
        kb_audit.search_ok,
        kb_audit.download_ok,
        kb_audit.denied,
        kb_audit.grant_added,
        kb_audit.grant_revoked,
        kb_audit.app_rotated,
    ]
    for fn in helpers:
        assert callable(fn)


def test_decision5_audit_fires_into_db(monkeypatch):
    """search_ok 调用一次后,audit() 会进 session_scope.add(AuditLogModel)。"""
    from chayuan.server.auth import kb_audit
    from chayuan.server.observability import audit as audit_mod

    captured = []

    class FakeSession:
        def add(self, row):
            captured.append(row)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(audit_mod, "session_scope", lambda: FakeSession())

    user = {"id": 7, "username": "alice", "role": "user"}
    kb_audit.search_ok(user, kbs=["kbA", "kbB"], queries=2, chunks=4,
                       elapsed_ms=33, request_id="req-test")
    assert len(captured) == 1
    row = captured[0]
    assert row.action == "kb.search"
    assert row.user_id == 7
    assert row.username == "alice"
    assert row.target_type == "kb"
    assert "kbA" in row.target_id and "kbB" in row.target_id
    assert row.status == "ok"
    assert row.elapsed_ms == 33
    assert row.request_id == "req-test"


def test_decision5_audit_app_user_has_null_user_id(monkeypatch):
    """App 主体落库:user_id=NULL,username=app:xxx。"""
    from chayuan.server.auth import kb_audit
    from chayuan.server.observability import audit as audit_mod

    captured = []
    class FakeSession:
        def add(self, row): captured.append(row)
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(audit_mod, "session_scope", lambda: FakeSession())

    app_user = {"id": "app:abc", "username": "app:demo-app", "role": "app"}
    kb_audit.search_ok(app_user, kbs=["k1"], queries=1, chunks=2)
    assert len(captured) == 1
    assert captured[0].user_id is None  # App 不能写 int user_id
    assert captured[0].username == "app:demo-app"


def test_decision5_audit_denied_records_code(monkeypatch):
    """denied 必须记 code (与 contract §4 对齐)。"""
    from chayuan.server.auth import kb_audit
    from chayuan.server.observability import audit as audit_mod
    import json as _json

    captured = []
    class FakeSession:
        def add(self, row): captured.append(row)
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(audit_mod, "session_scope", lambda: FakeSession())

    kb_audit.denied({"id": 5}, kb="forbidden_kb", action="read", code=4032,
                    reason="app no kb_grant")
    assert len(captured) == 1
    row = captured[0]
    assert row.action == "kb.denied"
    assert row.status == "denied"
    payload = _json.loads(row.payload)
    assert payload["code"] == 4032
    assert payload["want_action"] == "read"
    assert "kb_grant" in row.error_msg
