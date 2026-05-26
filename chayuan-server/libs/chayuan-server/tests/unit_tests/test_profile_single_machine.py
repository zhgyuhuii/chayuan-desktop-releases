"""``chayuan.server.profiles`` 单元测试(Phase 5)。

覆盖:
    * ``active_profile`` 解析 env 字符串 + 别名归一化
    * ``apply_profile`` 派发到对应 profile;未识别 / 未设 → no-op
    * ``apply_single_machine`` 在 Settings 上重写预期开关 + 设默认 env
    * ``LOCAL_USER`` 字段稳定 + ``local_user()`` 返副本
    * 幂等:重复 apply 不破坏状态
    * ``SupportedVSType.SQLITE_VEC`` 存在 + 工厂归一化 ``"sqlite-vec"`` 命中
"""
from __future__ import annotations

import os
from typing import Iterator

import pytest


# ──────────────────────────────────────────────────────────────────────
# 通用 fixture:每个测试结束后清理 env / Settings 改动,避免 cross-test 污染
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k in (
        "CHAYUAN_PROFILE",
        "CHAYUAN_AUTH",
        "CHAYUAN_REDIS",
        "CHAYUAN_QUEUE",
        "CHAYUAN_VECTOR_STORE",
    ):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def restore_settings() -> Iterator[None]:
    """快照 + 恢复 Settings 受影响字段。"""
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    kbs = Settings.kb_settings

    saved = {
        "AUTH_REQUIRED": getattr(bs, "AUTH_REQUIRED", None),
        "DEFAULT_VS_TYPE": getattr(kbs, "DEFAULT_VS_TYPE", None),
        "OBSERVABILITY_ENABLED": getattr(bs, "OBSERVABILITY_ENABLED", None),
        "LANGFUSE_ENABLED": getattr(bs, "LANGFUSE_ENABLED", None),
        "TELEMETRY_REMOTE": getattr(bs, "TELEMETRY_REMOTE", None),
    }
    yield
    if saved["AUTH_REQUIRED"] is not None and hasattr(bs, "AUTH_REQUIRED"):
        try:
            setattr(bs, "AUTH_REQUIRED", saved["AUTH_REQUIRED"])
        except Exception:
            pass
    if saved["DEFAULT_VS_TYPE"] is not None and hasattr(kbs, "DEFAULT_VS_TYPE"):
        try:
            setattr(kbs, "DEFAULT_VS_TYPE", saved["DEFAULT_VS_TYPE"])
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────
# active_profile / is_single_machine
# ──────────────────────────────────────────────────────────────────────


def test_active_profile_unset(clean_env: None) -> None:
    from chayuan.server.profiles import active_profile, is_single_machine

    assert active_profile() is None
    assert is_single_machine() is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("single-machine", "single-machine"),
        ("single_machine", "single-machine"),
        ("singlemachine", "single-machine"),
        ("desktop", "single-machine"),
        ("SINGLE-MACHINE", "single-machine"),  # 大小写不敏感
        ("  single-machine  ", "single-machine"),  # trim
    ],
)
def test_active_profile_aliases(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    from chayuan.server.profiles import active_profile, is_single_machine

    monkeypatch.setenv("CHAYUAN_PROFILE", raw)
    assert active_profile() == expected
    assert is_single_machine() is True


def test_active_profile_unknown_returns_none(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chayuan.server.profiles import active_profile, is_single_machine

    monkeypatch.setenv("CHAYUAN_PROFILE", "kubernetes")
    assert active_profile() is None
    assert is_single_machine() is False


# ──────────────────────────────────────────────────────────────────────
# LOCAL_USER
# ──────────────────────────────────────────────────────────────────────


def test_local_user_fields() -> None:
    from chayuan.server.profiles import LOCAL_USER, local_user

    assert LOCAL_USER["id"] == "local"
    assert LOCAL_USER["role"] == "owner"
    assert LOCAL_USER["is_local"] is True

    # 返回 dict 副本,mutate 不影响常量
    u = local_user()
    u["mutated"] = True
    assert "mutated" not in LOCAL_USER


# ──────────────────────────────────────────────────────────────────────
# apply_single_machine 真实改 Settings
# ──────────────────────────────────────────────────────────────────────


def test_apply_single_machine_overrides_auth_required(
    clean_env: None, restore_settings: None
) -> None:
    from chayuan.server.profiles import apply_single_machine
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    if not hasattr(bs, "AUTH_REQUIRED"):
        pytest.skip("AUTH_REQUIRED 字段不存在")

    # 先把 AUTH_REQUIRED 设 True 验证会被覆写
    try:
        setattr(bs, "AUTH_REQUIRED", True)
    except Exception:
        pytest.skip("Settings.basic_settings.AUTH_REQUIRED 不可写")
    assert bs.AUTH_REQUIRED is True, f"setattr(True) 没生效: {bs.AUTH_REQUIRED}"

    apply_single_machine()

    assert getattr(bs, "AUTH_REQUIRED") is False


def test_apply_single_machine_overrides_default_vs_type(
    clean_env: None, restore_settings: None
) -> None:
    from chayuan.server.profiles import apply_single_machine
    from chayuan.settings import Settings

    if hasattr(Settings.kb_settings, "DEFAULT_VS_TYPE"):
        try:
            setattr(Settings.kb_settings, "DEFAULT_VS_TYPE", "faiss")
        except Exception:
            pytest.skip("kb_settings.DEFAULT_VS_TYPE 不可写")

    apply_single_machine()

    if hasattr(Settings.kb_settings, "DEFAULT_VS_TYPE"):
        assert getattr(Settings.kb_settings, "DEFAULT_VS_TYPE") == "sqlite-vec"


def test_apply_single_machine_sets_envs(
    clean_env: None, restore_settings: None
) -> None:
    from chayuan.server.profiles import apply_single_machine

    apply_single_machine()

    assert os.environ["CHAYUAN_PROFILE"] == "single-machine"
    assert os.environ["CHAYUAN_AUTH"] == "anonymous"
    assert os.environ["CHAYUAN_REDIS"] == "disabled"
    assert os.environ["CHAYUAN_QUEUE"] == "inproc"
    assert os.environ["CHAYUAN_VECTOR_STORE"] == "sqlite-vec"


def test_apply_single_machine_respects_existing_envs(
    clean_env: None, restore_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env 已显式设(用户 / Tauri sidecar)时尊重,不覆盖。"""
    from chayuan.server.profiles import apply_single_machine

    monkeypatch.setenv("CHAYUAN_VECTOR_STORE", "lancedb")  # 自定义
    apply_single_machine()
    assert os.environ["CHAYUAN_VECTOR_STORE"] == "lancedb"


def test_apply_single_machine_idempotent(
    clean_env: None, restore_settings: None
) -> None:
    from chayuan.server.profiles import apply_single_machine
    from chayuan.settings import Settings

    apply_single_machine()
    state1 = (
        getattr(Settings.basic_settings, "AUTH_REQUIRED", None),
        getattr(Settings.kb_settings, "DEFAULT_VS_TYPE", None),
    )
    apply_single_machine()
    state2 = (
        getattr(Settings.basic_settings, "AUTH_REQUIRED", None),
        getattr(Settings.kb_settings, "DEFAULT_VS_TYPE", None),
    )
    assert state1 == state2


# ──────────────────────────────────────────────────────────────────────
# apply_profile 派发
# ──────────────────────────────────────────────────────────────────────


def test_apply_profile_unset_is_noop(clean_env: None) -> None:
    from chayuan.server.profiles import apply_profile

    assert apply_profile() is None


def test_apply_profile_dispatches_single_machine(
    clean_env: None, restore_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chayuan.server.profiles import apply_profile

    monkeypatch.setenv("CHAYUAN_PROFILE", "single-machine")
    assert apply_profile() == "single-machine"


def test_apply_profile_unknown_returns_none(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chayuan.server.profiles import apply_profile

    monkeypatch.setenv("CHAYUAN_PROFILE", "kubernetes")
    assert apply_profile() is None


# ──────────────────────────────────────────────────────────────────────
# SupportedVSType + factory
# ──────────────────────────────────────────────────────────────────────


def test_supported_vs_type_has_sqlite_vec() -> None:
    from chayuan.server.knowledge_base.kb_service.base import SupportedVSType

    assert SupportedVSType.SQLITE_VEC == "sqlite-vec"


def test_factory_normalizes_dash_to_underscore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``"sqlite-vec".upper().replace("-", "_")`` 应命中 SQLITE_VEC 属性。

    Phase 5.x 后这里会真的实例化 ``SqliteVecKBService``;为了避免触发 KBService
    __init__ 里的 DB / 文件副作用,把若干 helper stub 掉。
    """
    from chayuan.server.knowledge_base.kb_service.base import (
        KBServiceFactory,
        SupportedVSType,
    )

    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.base.get_kb_vector_namespace",
        lambda *_a, **_kw: "ns_factory",
    )
    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.base.get_kb_path",
        lambda name: f"/tmp/{name}",
    )
    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.base.get_doc_path",
        lambda name: f"/tmp/{name}/docs",
    )
    monkeypatch.setattr(
        "chayuan.server.knowledge_base.kb_service.base.kb_exists",
        lambda *_a, **_kw: True,
    )

    svc1 = KBServiceFactory.get_service(
        kb_name="t",
        vector_store_type=SupportedVSType.SQLITE_VEC,
        embed_model="bge-m3",
    )
    svc2 = KBServiceFactory.get_service(
        kb_name="t",
        vector_store_type="sqlite-vec",
        embed_model="bge-m3",
    )
    assert svc1.vs_type() == "sqlite-vec"
    assert svc2.vs_type() == "sqlite-vec"
