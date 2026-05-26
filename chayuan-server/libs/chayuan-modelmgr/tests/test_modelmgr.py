from __future__ import annotations

import json
from pathlib import Path

import pytest

from chayuan_core.events import EventBus, get_bus
from chayuan_modelmgr import (
    import_model,
    resolve_mirror,
    sha256_of_file,
    verify_directory,
    write_manifest,
)


def test_resolve_mirror_default():
    m = resolve_mirror()
    assert m.endpoint.startswith("http")


def test_resolve_mirror_env(monkeypatch):
    monkeypatch.setenv("CHAYUAN_MIRROR", "modelscope")
    m = resolve_mirror()
    assert m.name == "modelscope"
    assert m.kind == "modelscope"


def test_resolve_mirror_modelscope_known_name():
    """Named ``modelscope`` 应当解析成 modelscope kind，方便 downloader 走 modelscope 协议。"""
    m = resolve_mirror("modelscope")
    assert m.kind == "modelscope"
    assert "modelscope" in m.endpoint


def test_resolve_mirror_custom_endpoint_detects_modelscope():
    """传入自定义 modelscope.cn 镜像 URL 也应被识别为 modelscope kind。"""
    m = resolve_mirror("https://internal.modelscope.cn/proxy")
    assert m.kind == "modelscope"


def test_resolve_mirror_known_aliases():
    assert resolve_mirror("hf-mirror").kind == "hf"
    assert resolve_mirror("hf-co").kind == "hf"
    assert resolve_mirror("huggingface").kind == "hf"


def test_modelscope_downloader_lists_and_fetches(monkeypatch, tmp_path: Path):
    """ModelScope REST：先 list ``/repo/files``，再按 FilePath 拉每个 blob。"""
    import httpx

    from chayuan_modelmgr.downloader import DownloadOptions, ModelDownloader

    base = "https://www.modelscope.cn"
    repo = "owner/name"
    listed: dict[str, str] = {
        f"/api/v1/models/{repo}/repo/files": json.dumps({
            "Data": {
                "Files": [
                    {"Path": "config.json", "Type": "blob"},
                    {"Path": "model.safetensors", "Type": "blob"},
                    {"Path": "subdir", "Type": "tree"},  # 目录条目应被忽略
                ],
            },
        }),
    }
    files: dict[str, bytes] = {
        "config.json": b'{"hidden_size":768}',
        "model.safetensors": b"\x00" * 1024,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/repo/files" in url:
            assert "Recursive=True" in url
            return httpx.Response(200, content=listed[request.url.path])
        # 拉文件
        path = request.url.params.get("FilePath")
        assert path in files, f"unexpected: {url}"
        return httpx.Response(200, content=files[path])

    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    import chayuan_modelmgr.downloader as dl_mod
    # 强制走 REST fallback —— 模拟 huggingface_hub 不可用
    def _raise_hub(self, dest):
        raise dl_mod._HubMissingError("forced")
    monkeypatch.setattr(ModelDownloader, "_download_with_hub", _raise_hub)

    opt = DownloadOptions(repo=repo, mirror=base, category="weights")
    res = ModelDownloader(opt).run()
    assert (res.dest / "config.json").read_bytes().startswith(b"{")
    assert (res.dest / "model.safetensors").exists()
    assert "subdir" not in res.files


def test_sha256_and_manifest(tmp_path: Path):
    d = tmp_path / "m"
    d.mkdir()
    f = d / "weights.bin"
    f.write_bytes(b"hello")
    h = sha256_of_file(f)
    assert len(h) == 64
    write_manifest(d, source="test")
    ok, problems = verify_directory(d)
    assert ok and problems == []


def test_verify_detects_tamper(tmp_path: Path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "a.bin").write_bytes(b"hello")
    write_manifest(d)
    (d / "a.bin").write_bytes(b"tampered")
    ok, problems = verify_directory(d)
    assert not ok and any("sha256" in p or "size" in p for p in problems)


def test_import_model_uses_path_fallback(tmp_path: Path):
    src = tmp_path / "src" / "user--mymodel-test-import"
    src.mkdir(parents=True)
    (src / "weights.bin").write_bytes(b"x")
    dest, meta = import_model(src, category="rerank", repo="user/mymodel-test-import")
    try:
        assert dest.is_dir()
        assert (dest / "weights.bin").is_file()
        assert (dest / ".chayuan-manifest.json").is_file()
    finally:
        import shutil as _sh
        if dest.exists():
            _sh.rmtree(dest, ignore_errors=True)
