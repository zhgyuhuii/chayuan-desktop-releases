"""集成 smoke：把 ai-platform 路由挂到一个新建的 FastAPI app，命中关键端点。

避开 chayuan-server 主 app（启动开销大），只验证 ``register_ai_platform_routes``
本身的契约：

* 9 类路由全部 mount 成功 → ``/v1/models`` 200；
* ``/v1/admin/doctor`` 返回 ``preflight / runtime / adapters`` 三段；
* ``ModelRepository`` 已被 LocalIndexRepository 覆盖（不是 SQLAlchemy 真实库）。
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """构造一个最小 FastAPI app，挂上 ai-platform 路由 + 一份种子 local_index。"""
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    import chayuan.settings as st
    monkeypatch.setattr(st, "CHAYUAN_ROOT", str(tmp_path), raising=False)

    # 重置：runtime / runtime_info / runtime adapter registry
    import chayuan.server.runtime.runtime_info as ri_mod
    ri_mod._SINGLETON = None
    import chayuan_runtime.registry as reg_mod
    reg_mod._REGISTRY = None
    import chayuan_supervisor.runtime_adapter as ra_mod
    ra_mod._BACKEND = None

    # 给 local_index 喂一个 chat 模型
    from chayuan.server.model_registry.local_index import (
        LocalModelEntry,
        LocalModelIndex,
    )
    import chayuan.server.model_registry.local_index as li_mod

    idx_path = tmp_path / "model_registry" / "local_models.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx = LocalModelIndex(path=idx_path)
    idx.replace_all([LocalModelEntry(
        model_id="hf/qwen3-4b",
        path="/tmp/hf/qwen3-4b",
        relpath="hf/qwen3-4b",
        capability="chat",
        family="ollama",
        format="gguf",
        size_bytes=4096,
        mtime=1.0,
        confidence=0.95,
        evidence=[],
        meta={},
        source_tag="huggingface",
    )])
    li_mod._SINGLETON = idx

    from fastapi import FastAPI
    app = FastAPI()

    from chayuan.server.ai_platform import register_ai_platform_routes
    summary = register_ai_platform_routes(app)
    assert "models" in summary["mounted"], summary
    assert "admin" in summary["mounted"], summary
    return app


@pytest.mark.requires("fastapi", "starlette")
def test_v1_models_returns_local_index(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        r = c.get("/v1/models")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert "hf/qwen3-4b" in ids
    qwen = next(m for m in body["data"] if m["id"] == "hf/qwen3-4b")
    assert qwen["category"] == "chat"
    assert qwen["runtime"] == "ollama"


@pytest.mark.requires("fastapi", "starlette")
def test_v1_models_filter_by_category(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        r = c.get("/v1/models", params={"category": "chat"})
        assert r.status_code == 200
        chats = [m["id"] for m in r.json()["data"]]
        assert "hf/qwen3-4b" in chats

        r2 = c.get("/v1/models", params={"category": "embedding"})
        assert r2.status_code == 200
        assert r2.json()["data"] == []


@pytest.mark.requires("fastapi", "starlette")
def test_v1_admin_doctor_returns_three_sections(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        r = c.get("/v1/admin/doctor")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "host" in body and body["host"]["python"]
    # preflight 失败时是 dict + error，但 200 不变
    assert "preflight" in body
    assert "runtime" in body  # 可能为 {} —— 仍是 dict
    assert isinstance(body["runtime"], dict)
    assert "adapters" in body
    assert isinstance(body["adapters"], list)
    # 11 个 adapter 至少都被探到名字
    names = [a["name"] for a in body["adapters"]]
    for must in ("ollama", "vllm", "comfyui", "rapidocr"):
        assert must in names, f"adapter {must} missing from doctor response"

    # v5.1 新增字段：每条 adapter 报告都得有 kind + probe_url
    for ad in body["adapters"]:
        assert ad["kind"] in ("http", "subprocess"), ad
        assert "probe_url" in ad

    # piper / whispercpp 应该被识别成 subprocess 类
    by_name = {a["name"]: a for a in body["adapters"]}
    assert by_name["piper"]["kind"] == "subprocess"
    assert by_name["whispercpp"]["kind"] == "subprocess"
    assert by_name["piper"]["probe_url"] == ""

    # ollama 的 probe_url 应该是 base_url + /api/tags（v5.1 health_url 接口）
    ollama = by_name["ollama"]
    assert ollama["probe_url"].endswith("/api/tags"), ollama["probe_url"]


@pytest.mark.requires("fastapi", "starlette")
def test_v1_admin_doctor_query_flags_disable_sections(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        r = c.get("/v1/admin/doctor",
                  params={"with_adapters": "false", "with_runtime": "false"})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime"] is None
    assert body["adapters"] is None


@pytest.mark.requires("fastapi", "starlette")
def test_v1_admin_doctor_fix_unknown_returns_hint(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        r = c.post("/v1/admin/doctor/fix/totally-unknown")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "hint" in body and isinstance(body["hint"], str)
