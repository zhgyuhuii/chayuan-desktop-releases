from __future__ import annotations

from chayuan_preflight import run_all


def test_run_all_returns_report():
    rep = run_all()
    d = rep.to_dict()
    assert "summary" in d and "checks" in d
    assert all(c["severity"] in ("ok", "warn", "fatal") for c in d["checks"])
    # at minimum we expect os.platform + os.python + port + gpu
    names = {c["name"] for c in d["checks"]}
    assert "os.platform" in names and "os.python" in names
