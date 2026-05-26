"""``chayuan_supervisor.runtime_adapter`` 单元测试。

被测对象：让 supervisor 与 chayuan-server 共用同一份 ``runtime.json``。

两条独立路径要分别测：

* ``server`` 后端（chayuan-server 在场） → 写 ``<CHAYUAN_ROOT>/runtime.json``，
  ``services`` schema；
* ``supervisor`` 后端（standalone） → 写 ``<CHAYUAN_HOME>/data/runtime.json``，
  ``endpoints`` schema。

测试通过 monkeypatch 强制切换 _BACKEND，避开导入时探测的随机性。
"""
from __future__ import annotations

import threading

import pytest

# 整套 ``server-path`` 用例都依赖 ``chayuan.server.runtime``。在 Python>=3.13
# 等不在 chayuan-server poetry 约束内的解释器上，``chayuan`` 包装不上来，
# 这里统一 skip 而不是逐条 importorskip，避免测试列表噪声。
chayuan_server_runtime = pytest.importorskip(
    "chayuan.server.runtime",
    reason="chayuan-server not installed (e.g. Python>=3.13)；server 路径用例自动跳过",
)


@pytest.fixture(autouse=True)
def _reset_backend_singletons(monkeypatch, tmp_path):
    """每个用例前后重置：_BACKEND 探测缓存 / chayuan-server runtime_info / supervisor RuntimeInfo。"""
    import chayuan_supervisor.runtime_adapter as ra_mod

    ra_mod._BACKEND = None
    # 也清掉 server 端 singleton（用 tmp_path 当 CHAYUAN_ROOT）
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    try:
        import chayuan.server.runtime.runtime_info as ri_mod
        ri_mod._SINGLETON = None
        import chayuan.settings as st
        monkeypatch.setattr(st, "CHAYUAN_ROOT", str(tmp_path), raising=False)
    except Exception:
        pass
    # supervisor 自带 RuntimeInfo singleton
    try:
        import chayuan_supervisor.credentials as scr
        scr._INFO = None
    except Exception:
        pass
    yield
    ra_mod._BACKEND = None


# ---------------------------------------------------------------------------
# Backend 探测
# ---------------------------------------------------------------------------


def test_detect_backend_prefers_server_when_available():
    """默认环境里 chayuan-server 已装好，应该探到 server 路径。

    在某些 CI 环境（Python 3.13）上 chayuan-server 自身装不上（poetry 约束
    ``python >=3.10,<3.13``），这条测试自动 skip；不算回归。
    """
    pytest.importorskip(
        "chayuan.server.runtime",
        reason="chayuan-server 未安装到当前解释器（通常因 Python>=3.13 不在约束内）",
    )
    from chayuan_supervisor.runtime_adapter import _detect_backend
    import chayuan_supervisor.runtime_adapter as ra_mod
    ra_mod._BACKEND = None  # 强制重新探测
    assert _detect_backend() == "server"


def test_detect_backend_falls_back_to_supervisor(monkeypatch):
    """模拟 ``chayuan.server.runtime`` import 失败 → 回退 supervisor。"""
    import builtins
    real_import = builtins.__import__

    def _bad_import(name, *args, **kw):
        if name.startswith("chayuan.server.runtime"):
            raise ImportError(f"forced unavailable: {name}")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", _bad_import)
    import chayuan_supervisor.runtime_adapter as ra_mod
    ra_mod._BACKEND = None
    assert ra_mod._detect_backend() == "supervisor"


# ---------------------------------------------------------------------------
# get_runtime_info_unified —— server 路径
# ---------------------------------------------------------------------------


def test_get_runtime_info_unified_server_writes_chayuan_server_schema():
    """server 路径下 set_endpoint 应写到 ``services``（不是 ``endpoints``）。"""
    from chayuan_supervisor.runtime_adapter import get_runtime_info_unified

    ri = get_runtime_info_unified()
    ri.set_endpoint("postgres", host="127.0.0.1", port=35432, scheme="postgresql")

    # 直接读底层，确认走的是 server schema
    from chayuan.server.runtime.runtime_info import get_runtime_info
    raw = get_runtime_info()._data
    assert "services" in raw
    assert raw["services"]["postgres"]["port"] == 35432


def test_get_runtime_info_unified_set_endpoint_unknown_keys_to_extra():
    """supervisor 旧 API 的额外 key（如 ``console_port``）应进 extra。"""
    from chayuan_supervisor.runtime_adapter import get_runtime_info_unified

    ri = get_runtime_info_unified()
    ri.set_endpoint("minio", host="127.0.0.1", port=39000, console_port=39001,
                    metrics_port=39002, kind="minio")

    from chayuan.server.runtime.runtime_info import get_runtime_info
    raw = get_runtime_info()._data["services"]["minio"]
    assert raw["port"] == 39000
    assert raw["kind"] == "minio"
    extra = raw.get("extra") or {}
    assert extra.get("console_port") == 39001
    assert extra.get("metrics_port") == 39002


def test_runtime_info_adapter_endpoint_reads_back():
    from chayuan_supervisor.runtime_adapter import get_runtime_info_unified

    ri = get_runtime_info_unified()
    ri.set_endpoint("redis", host="127.0.0.1", port=36379, scheme="redis")

    ep = ri.endpoint("redis")
    assert ep["port"] == 36379
    assert ep["scheme"] == "redis"

    # 不存在的 service → {}
    assert ri.endpoint("ghost") == {}


def test_runtime_info_adapter_credentials_roundtrip():
    from chayuan_supervisor.runtime_adapter import get_runtime_info_unified

    ri = get_runtime_info_unified()
    ri.set_credentials("postgres", user="alice", password="s3cret")
    creds = ri.credentials("postgres")
    assert creds == {"user": "alice", "password": "s3cret"}
    assert ri.credentials("ghost") == {}


# ---------------------------------------------------------------------------
# ensure_credentials_unified
# ---------------------------------------------------------------------------


def test_ensure_credentials_unified_no_auth_short_circuits():
    from chayuan_supervisor.runtime_adapter import ensure_credentials_unified

    env, record = ensure_credentials_unified("redis", {"no_auth": True})
    assert env == {}
    assert record == {}


def test_ensure_credentials_unified_emits_env_vars():
    from chayuan_supervisor.runtime_adapter import ensure_credentials_unified

    env, record = ensure_credentials_unified("postgres", {
        "user_env": "PG_USER",
        "password_env": "PG_PASSWORD",
    })
    assert "PG_USER" in env and env["PG_USER"]
    assert "PG_PASSWORD" in env and env["PG_PASSWORD"]
    assert record["user"] == env["PG_USER"]
    assert record["password"] == env["PG_PASSWORD"]


def test_ensure_credentials_unified_stable_across_calls():
    """同名服务两次 ensure_credentials 应返回相同的密码。"""
    from chayuan_supervisor.runtime_adapter import ensure_credentials_unified

    env1, rec1 = ensure_credentials_unified("postgres", {
        "user_env": "PG_USER", "password_env": "PG_PASSWORD",
    })
    env2, rec2 = ensure_credentials_unified("postgres", {
        "user_env": "PG_USER", "password_env": "PG_PASSWORD",
    })
    assert rec1 == rec2
    assert env1 == env2


# ---------------------------------------------------------------------------
# make_unified_port_allocator
# ---------------------------------------------------------------------------


def test_make_unified_port_allocator_returns_shim_with_supervisor_api():
    """server 路径下应返回 _PortAllocatorShim，但兼容 supervisor 的 allocate(preferred=)。"""
    from chayuan_supervisor.runtime_adapter import (
        make_unified_port_allocator,
        _PortAllocatorShim,
    )
    pa = make_unified_port_allocator(40000, 40500)
    assert isinstance(pa, _PortAllocatorShim)

    # supervisor 老接口 allocate(preferred=N)
    p = pa.allocate(preferred=40123)
    assert p == 40123
    # 第二次相同 preferred → 自动 bump
    p2 = pa.allocate(preferred=40123)
    assert p2 != 40123 and 40000 <= p2 <= 40500
    pa.release(p)


def test_make_unified_port_allocator_reserves_runtime_json_endpoints():
    """启动时已经在 runtime.json 里的端口应被预先 reserve，不被新分配抢。"""
    from chayuan_supervisor.runtime_adapter import (
        get_runtime_info_unified,
        make_unified_port_allocator,
    )
    ri = get_runtime_info_unified()
    ri.set_endpoint("postgres", host="127.0.0.1", port=40123)

    pa = make_unified_port_allocator(40000, 40500)
    # preferred=40123 应该被立刻识别为占用，找到下一个空闲（40124+）
    p = pa.allocate(preferred=40123)
    assert p > 40123
