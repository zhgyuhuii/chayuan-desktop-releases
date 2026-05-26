from __future__ import annotations

import sys
import time
from pathlib import Path

import yaml

from chayuan_supervisor import (
    PortAllocator,
    RestartPolicy,
    SupervisorManager,
    get_runtime_info,
    load_spec,
)
from chayuan_supervisor.credentials import ensure_credentials, reset_for_tests
from chayuan_supervisor.manager import topo_sort


def test_port_allocator_alloc_release():
    a = PortAllocator(16500, 16550)
    p = a.allocate()
    assert 16500 <= p <= 16550
    a.release(p)


def test_port_allocator_prefers_preferred():
    a = PortAllocator(40000, 40500)
    p = a.allocate(preferred=40123)
    assert p == 40123
    p2 = a.allocate(preferred=40123)
    assert p2 != 40123  # already used → bumps forward
    assert p2 > 40123


def test_port_allocator_wraps_when_high_exhausted():
    import socket as _s
    a = PortAllocator(50000, 50010)
    # Reserve 50005..50010 manually
    for k in range(50005, 50011):
        a.reserve(k)
    p = a.allocate(preferred=50007)
    # All [50007..50010] are used → should wrap and find one in [50000..50004]
    assert 50000 <= p <= 50004


def test_port_allocator_skips_busy_socket():
    import socket as _s
    s = _s.socket(_s.AF_INET, _s.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    busy = s.getsockname()[1]
    s.listen(1)
    try:
        a = PortAllocator(busy, busy + 50)
        p = a.allocate(preferred=busy)
        assert p != busy and p > busy
    finally:
        s.close()


def test_credentials_persist_across_calls(tmp_path, monkeypatch):
    target = tmp_path / "rt.json"
    reset_for_tests(target)
    env, rec1 = ensure_credentials("postgres", {
        "user_env": "PG_USER", "password_env": "PG_PASSWORD"
    })
    assert env["PG_USER"] == rec1["user"] and env["PG_PASSWORD"] == rec1["password"]
    # Second call must return the SAME password — proves persistence.
    env2, rec2 = ensure_credentials("postgres", {
        "user_env": "PG_USER", "password_env": "PG_PASSWORD"
    })
    assert rec1 == rec2
    assert env == env2
    reset_for_tests()  # restore singleton


def test_credentials_no_auth_returns_empty(tmp_path):
    reset_for_tests(tmp_path / "rt.json")
    env, rec = ensure_credentials("ollama", {"no_auth": True})
    assert env == {} and rec == {}
    reset_for_tests()


def test_topo_sort_orders_deps():
    from chayuan_supervisor.manager import ProcessSpec

    a = ProcessSpec(name="a", binary="x")
    b = ProcessSpec(name="b", binary="x", depends_on=["a"])
    c = ProcessSpec(name="c", binary="x", depends_on=["b"])
    out = topo_sort([c, b, a])
    names = [s.name for s in out]
    assert names.index("a") < names.index("b") < names.index("c")


def test_restart_policy_backoff():
    p = RestartPolicy(max_restarts=3, base_sec=1.0, cap_sec=8.0)
    assert p.delay_for(1) == 1.0
    assert p.delay_for(2) == 2.0
    assert p.delay_for(3) == 4.0
    assert p.delay_for(99) == 8.0
    assert p.should_restart(0) and not p.should_restart(3)


def test_load_spec_and_dry_run(tmp_path: Path):
    spec = {
        "processes": [
            {"name": "echo1", "binary": sys.executable, "args": ["-c", "print('hi')"], "port": "P1"},
            {"name": "echo2", "binary": sys.executable, "args": ["-c", "print('hi')"], "depends_on": ["echo1"]},
        ]
    }
    f = tmp_path / "supervisor.yaml"
    f.write_text(yaml.safe_dump(spec), encoding="utf-8")
    specs = load_spec(f)
    assert {s.name for s in specs} == {"echo1", "echo2"}
    mgr = SupervisorManager(specs=specs)
    procs = mgr.plan()
    mgr.up(dry_run=True)
    assert all(p.state.value in ("running", "stopped") for p in procs)


def test_manager_plan_uses_preferred_port_and_credentials(tmp_path: Path):
    reset_for_tests(tmp_path / "rt.json")
    spec = {
        "processes": [
            {
                "name": "fakedb",
                "binary": "/bin/true",
                "args": ["--port", "${DB_PORT}", "--user", "${DB_USER}", "--pwd", "${DB_PWD}"],
                "port": "DB_PORT",
                "preferred_port": 41099,
                "credentials": {"user_env": "DB_USER", "password_env": "DB_PWD"},
                "expose": {"kind": "postgres", "scheme": "postgresql", "database": "test"},
            }
        ]
    }
    f = tmp_path / "supervisor.yaml"
    f.write_text(yaml.safe_dump(spec), encoding="utf-8")
    specs = load_spec(f)
    mgr = SupervisorManager(specs=specs)
    procs = mgr.plan()
    p = procs[0]
    assert "41099" in p.args  # preferred port honoured
    assert p.env.get("DB_USER")
    assert p.env.get("DB_PWD")
    eps = mgr.endpoints()
    ep = eps["fakedb"]
    assert ep["port"] == 41099 and ep["host"] == "127.0.0.1"
    assert ep["url"].startswith("postgresql://")
    assert ep["password"] == p.env["DB_PWD"]

    # Plan again to prove credentials & port are persisted in runtime.json
    info = get_runtime_info()
    assert info.endpoint("fakedb")["port"] == 41099
    assert info.credentials("fakedb")["password"] == p.env["DB_PWD"]
    reset_for_tests()


def test_real_subprocess_starts(tmp_path: Path):
    """Spawn a real short-lived python subprocess and confirm state transitions."""
    from chayuan_supervisor.manager import ProcessSpec

    spec = ProcessSpec(
        name="sleep1",
        binary=sys.executable,
        args=["-c", "import time; time.sleep(2)"],
    )
    mgr = SupervisorManager(specs=[spec])
    mgr.plan()
    mgr.up()
    try:
        for _ in range(20):
            st = mgr.status()[0]
            if st["state"] == "running" and st.get("pid"):
                break
            time.sleep(0.2)
        assert st["state"] == "running"
    finally:
        mgr.down()
