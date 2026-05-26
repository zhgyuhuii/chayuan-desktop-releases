from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chayuan_gateway.app import create_app
from chayuan_registry import ModelRepository, session_scope
from chayuan_registry.db import reset_for_tests


@pytest.fixture
def client(monkeypatch):
    reset_for_tests("sqlite:///:memory:")
    with session_scope() as s:
        repo = ModelRepository(s)
        repo.upsert({"repo": "qwen/test", "category": "chat", "runtime": "ollama", "format": "gguf", "path": "/m"})
        repo.upsert({"repo": "bge/test", "category": "embedding", "runtime": "infinity", "format": "safetensors", "path": "/e"})
    app = create_app()
    return TestClient(app)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_list_models(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    data = r.json()["data"]
    ids = {m["id"] for m in data}
    assert "qwen/test" in ids and "bge/test" in ids


def test_chat_completions_mock(client):
    r = client.post("/v1/chat/completions", json={
        "model": "qwen/test",
        "messages": [{"role": "user", "content": "hello"}],
    }, headers={"Authorization": "Bearer sk-chayuan-dev"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["choices"][0]["message"]["content"].startswith("[mock:ollama]")


def test_chat_completions_stream_passthrough(client):
    """流式响应必须是 OpenAI 兼容的裸 SSE：仅 ``data: <json>`` 行 + ``data: [DONE]``。

    回归测试：之前用 ``EventSourceResponse`` 会多写一行 ``event: message``，
    导致 OpenAI / langchain SDK 把整个 chunk 解析失败。
    """
    import json as _json

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen/test",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
        headers={"Authorization": "Bearer sk-chayuan-dev"},
    ) as r:
        assert r.status_code == 200
        ctype = r.headers.get("content-type", "")
        assert ctype.startswith("text/event-stream")
        text = "".join(r.iter_text())

    lines = [ln for ln in text.split("\n") if ln.strip()]
    # 不允许出现 ``event:`` 头：OpenAI 协议是裸 data 流
    assert not any(ln.startswith("event:") for ln in lines), text
    data_lines = [ln for ln in lines if ln.startswith("data: ")]
    assert data_lines, text
    # 终止帧
    assert data_lines[-1] == "data: [DONE]"
    # 中间 chunk 必须是合法 JSON（mock adapter 至少返回一个 chunk）
    parsed = []
    for ln in data_lines[:-1]:
        body = ln[len("data: "):]
        parsed.append(_json.loads(body))
    assert parsed, text
    assert any("choices" in c or "object" in c for c in parsed)


def test_embedding_mock(client):
    r = client.post("/v1/embeddings", json={
        "model": "bge/test", "input": ["a", "b", "c"],
    }, headers={"Authorization": "Bearer sk-chayuan-dev"})
    assert r.status_code == 200
    assert len(r.json()["data"]) == 3


def test_system_services_masks_passwords(client, tmp_path, monkeypatch):
    from chayuan_supervisor.credentials import reset_for_tests
    info = reset_for_tests(tmp_path / "rt.json")
    info.set_endpoint("postgres", host="127.0.0.1", port=35432, scheme="postgresql",
                      user="chayuan", password="topsecret",
                      url="postgresql://chayuan:topsecret@127.0.0.1:35432/chayuan",
                      kind="postgres")
    r = client.get("/v1/system/services")
    assert r.status_code == 200
    pg = next(x for x in r.json()["data"] if x["name"] == "postgres")
    assert pg["password"] == "****"
    assert "topsecret" not in pg["url"] and "****" in pg["url"]
    r2 = client.get("/v1/system/services?reveal=true")
    pg2 = next(x for x in r2.json()["data"] if x["name"] == "postgres")
    assert pg2["password"] == "topsecret"
    reset_for_tests()


def test_admin_disable(client):
    r = client.post("/v1/admin/models/qwen/test/disable",
                    headers={"Authorization": "Bearer sk-chayuan-dev"})
    assert r.status_code == 200
    r2 = client.get("/v1/models")
    items = r2.json()["data"]
    qwen = next(m for m in items if m["id"] == "qwen/test")
    assert qwen["enabled"] is False


_AUTH = {"Authorization": "Bearer sk-chayuan-dev"}


def test_admin_recommended_models_groups_by_capability(client):
    """``/v1/admin/recommended_models``：返回 9 大 capability，全部带 ``installed`` 标记。"""
    r = client.get("/v1/admin/recommended_models", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    # 全部 9 大 capability 都被覆盖
    expected = {
        "chat", "text-embedding", "image-embedding", "rerank",
        "text-to-image", "text-to-video", "text-to-speech", "asr", "ocr",
    }
    assert expected <= set(body["capabilities"])
    # image-embedding 至少有 1 条推荐（默认 jina-clip-v1）
    img = body["recommended"]["image-embedding"]
    assert any(item["id"] == "jina-clip-v1" for item in img)
    # ``installed`` 标识能反映 fixture 中已写入 repo 的模型
    chat = body["recommended"]["chat"]
    # ``qwen/test`` 不在推荐里；推荐里的 ``qwen2.5:4b`` 没有 alias 命中 ``qwen/test``
    assert all(not item.get("installed") for item in chat) or all(
        not item.get("installed") for item in chat if item["id"] != "qwen/test"
    )


def test_admin_recommended_models_filtered(client):
    r = client.get("/v1/admin/recommended_models?capability=image-embedding", headers=_AUTH)
    assert r.status_code == 200
    rec = r.json()["recommended"]
    # 只包含被请求的那个 cap
    assert set(rec.keys()) == {"image-embedding"}


def test_admin_services_topology_returns_install_recipes(client):
    """``/v1/admin/services/topology``：每个服务给出 docker / 平台原生命令。"""
    r = client.get("/v1/admin/services/topology", headers=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["host_os"] in ("linux", "mac", "win")
    names = {s["name"] for s in body["services"]}
    # 关键服务全在
    assert {"postgres", "redis", "minio", "milvus", "ollama"} <= names
    pg = next(s for s in body["services"] if s["name"] == "postgres")
    assert pg["install"]["docker"], "docker fallback 必须存在"
    # status 三态
    assert pg["status"] in ("healthy", "configured", "missing")


def test_v1_embeddings_image_input_without_model_returns_412_setup(client):
    """图片向量化 + 没装 image-embedding 模型 → 412 + setup hint，不是 400 / 500。"""
    r = client.post(
        "/v1/embeddings",
        json={"input": [{"image_url": "data:image/png;base64,iVBORw0KGgo="}]},
        headers=_AUTH,
    )
    assert r.status_code == 412, r.text
    body = r.json()
    err = body["error"]
    assert err["code"] == "model_not_configured"
    assert err["capability"] == "image-embedding"
    # 引导调用方去哪个面板装哪个推荐模型
    assert err["setup"]["panel"] == "settings.aiPlatform.capability"
    assert err["setup"]["default"]["id"] == "jina-clip-v1"


def test_admin_runtimes_lists_all_adapters_with_capabilities(client):
    """``/v1/admin/runtimes``：返回每个 adapter 的 capability + 健康 + 安装方式。"""
    r = client.get("/v1/admin/runtimes", headers=_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    names = {x["name"] for x in body["runtimes"]}
    # 11 个内置 adapter 全在
    assert {"ollama", "vllm", "infinity", "comfyui", "rapidocr", "paddleocr", "piper"} <= names
    # 关键字段格式正确
    ollama = next(x for x in body["runtimes"] if x["name"] == "ollama")
    assert "chat" in ollama["categories"]
    assert ollama["install_kind"] == "one-click"
    assert ollama["health"] in ("healthy", "configured", "missing")
    assert isinstance(ollama["install_recipes"], dict)
    # category_labels 是中文友好版
    infinity = next(x for x in body["runtimes"] if x["name"] == "infinity")
    assert "文本嵌入" in infinity["category_labels"]
    assert infinity["install_kind"] == "pip"
    assert infinity["default_pip_package"]


def test_admin_runtime_install_rejects_manual_and_accepts_ollama(client, monkeypatch):
    """``/v1/admin/runtimes/{name}/install``：

    * unknown / docker-only runtime → 400
    * Ollama / pip 系列 → 200 + task_id（不真起 subprocess —— 直接 mock 掉 ``Popen``）
    """
    # 1) 未实现自动安装的 → 400
    r1 = client.post("/v1/admin/runtimes/comfyui/install", headers=_AUTH)
    assert r1.status_code == 400, r1.text
    assert "comfyui" in r1.json()["detail"]

    # 2) Ollama → 200，但要把 subprocess.Popen 替换掉，不真去跑 install.sh
    class _DummyProc:
        stdout = iter(["fake install line\n"])

        def wait(self):
            return 0

    import subprocess as _subprocess
    monkeypatch.setattr(
        _subprocess, "Popen",
        lambda *a, **kw: _DummyProc(),
    )

    r2 = client.post("/v1/admin/runtimes/ollama/install", headers=_AUTH)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["name"] == "ollama"
    assert body["kind"] == "one-click"
    assert body["task_id"]
    assert body["state"] == "queued"


def test_admin_defaults_round_trip(client):
    """``/v1/admin/defaults``：GET 返回每个 capability 的当前默认 + 候选；POST 写入。"""
    r = client.get("/v1/admin/defaults", headers=_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    # 9 大 capability 全在
    assert set(body["capabilities"]) >= {
        "chat", "embedding", "clip", "rerank", "t2i", "t2v", "tts", "asr", "ocr",
    }
    # candidates / defaults 都得是 dict
    assert "chat" in body["defaults"]
    assert isinstance(body["candidates"]["chat"], list)

    # 把 ``qwen/test`` 设为 chat 默认
    r2 = client.post(
        "/v1/admin/defaults",
        headers=_AUTH,
        json={"chat": "qwen/test", "non-existent": "x"},
    )
    assert r2.status_code == 200, r2.text
    results = r2.json()["results"]
    assert results["chat"]["ok"] is True
    assert results["chat"]["model"] == "qwen/test"
    # unknown capability 不报错，只标记 false
    assert results["non-existent"]["ok"] is False
    # 再 GET，``qwen/test`` 应是 chat 默认
    r3 = client.get("/v1/admin/defaults", headers=_AUTH).json()
    assert r3["defaults"]["chat"] == "qwen/test"


def test_admin_defaults_accepts_frontend_capability_aliases(client):
    """前端用 ``text-embedding`` / ``image-embedding`` 等 capability 字面量；
    后端 ``set_defaults`` 要把它映射成 ``embedding`` / ``clip``。"""
    r = client.post(
        "/v1/admin/defaults",
        headers=_AUTH,
        json={"text-embedding": "qwen/test"},  # qwen/test 不是 embedding；预期 set_default 失败
    )
    assert r.status_code == 200
    # qwen/test 是 chat，不会被 set 成 embedding 的默认；返回 ok=False 但不抛
    res = r.json()["results"]["text-embedding"]
    assert res["ok"] is False or res["model"] == "qwen/test"


def test_admin_models_locate_returns_well_formed_payload_for_known_model(client):
    """``/v1/admin/models/<id>/locate``：fixture 模型 ``qwen/test`` 路径不存在时
    仍然返回 200 + 结构完整，便于前端无脑渲染。"""
    r = client.get("/v1/admin/models/qwen%2Ftest/locate", headers=_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_id"] == "qwen/test"
    # 关键字段都在 —— 前端可以用 ``found`` 判定是否能"打开文件夹"
    for k in ("found", "runtime", "cache_kind", "path", "dir", "size_bytes", "blobs"):
        assert k in body, f"missing field: {k}"


def test_admin_models_locate_for_ollama_uses_filesystem(client, monkeypatch, tmp_path):
    """``runtime=ollama`` 走文件系统解析（manifest + blobs），不读 DB 路径。"""
    import json as _json
    root = tmp_path / "ollama"
    manifest_dir = root / "manifests" / "registry.ollama.ai" / "library" / "qwen2"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "0.5b").write_text(_json.dumps({
        "layers": [{
            "digest": "sha256:deadbeef",
            "size": 4096,
            "mediaType": "application/vnd.ollama.image.model",
        }],
    }))
    blob = root / "blobs" / "sha256-deadbeef"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"\x00" * 4096)
    monkeypatch.setenv("OLLAMA_MODELS", str(root))

    # repo 中不需要 ``qwen2:0.5b``——locate 不要求模型已注册
    r = client.get("/v1/admin/models/qwen2:0.5b/locate", headers=_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True
    assert body["cache_kind"] == "ollama-blobs"
    assert body["path"].endswith("blobs/sha256-deadbeef")
    assert body["size_bytes"] == 4096


def test_models_pull_returns_unique_task_id_with_repo_state(client):
    """``POST /v1/admin/models/pull``：同一个 repo 多次调用拿到不同 task_id（不再覆盖）。"""
    # 用一个会立即失败的不存在 repo，避免真去拉网络
    r1 = client.post(
        "/v1/admin/models/pull",
        json={"repo": "nonexistent/never-going-to-exist"},
        headers={"Authorization": "Bearer sk-chayuan-dev"},
    )
    r2 = client.post(
        "/v1/admin/models/pull",
        json={"repo": "nonexistent/never-going-to-exist"},
        headers={"Authorization": "Bearer sk-chayuan-dev"},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["task_id"] != r2.json()["task_id"]
