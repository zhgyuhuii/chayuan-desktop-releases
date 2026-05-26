"""``chayuan_gateway.services.model_locator`` 的单测。

覆盖：

* DB.path 存在 → 直接返回（不走 runtime 查询）
* Ollama：mock ``$OLLAMA_MODELS/manifests/...`` + ``blobs/sha256-...``
* HF cache：mock ``$HF_HOME/hub/models--<owner>--<name>/snapshots/<rev>/...``
* ComfyUI：mock ``$COMFYUI_MODELS/checkpoints/<file>``
* 兜底：runtime / path 都没有 → ``found=False``
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chayuan_gateway.services import model_locator as ml


# ---------------------------------------------------------------------------
# DB-path-first：自管理模型直接走 ``path``
# ---------------------------------------------------------------------------


def test_locate_uses_db_path_when_file_exists(tmp_path: Path):
    f = tmp_path / "chat" / "qwen.gguf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x00" * 1024)

    loc = ml.locate("qwen2.5:4b", runtime="ollama", db_path=str(f))
    # 注意：DB 里有现成路径就**优先**走 local，不用再去翻 ollama 缓存
    assert loc.found is True
    assert loc.path == str(f)
    assert loc.dir == str(f.parent)
    assert loc.size_bytes == 1024
    assert loc.cache_kind == "local"


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


def _mk_ollama_layout(root: Path, *, name: str, tag: str,
                      blob_digest: str, blob_size: int) -> Path:
    """生成 ollama models 目录骨架，返回 manifest 文件路径。"""
    manifest_dir = root / "manifests" / "registry.ollama.ai" / "library" / name
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / tag
    manifest.write_text(json.dumps({
        "schemaVersion": 2,
        "config": {"digest": "sha256:cccc", "size": 32, "mediaType": "application/vnd.docker.container.image.v1+json"},
        "layers": [
            {
                "digest": blob_digest,
                "size": blob_size,
                "mediaType": "application/vnd.ollama.image.model",
            },
            {
                "digest": "sha256:1111",
                "size": 100,
                "mediaType": "application/vnd.ollama.image.template",
            },
        ],
    }), encoding="utf-8")

    blob_dir = root / "blobs"
    blob_dir.mkdir(parents=True)
    blob_file = blob_dir / blob_digest.replace(":", "-", 1)
    blob_file.write_bytes(b"\x00" * blob_size)

    template_blob = blob_dir / "sha256-1111"
    template_blob.write_bytes(b"\x00" * 100)
    return manifest


def test_locate_ollama_resolves_manifest_and_primary_blob(monkeypatch, tmp_path: Path):
    root = tmp_path / "ollama"
    _mk_ollama_layout(root, name="qwen2", tag="0.5b",
                      blob_digest="sha256:abcd", blob_size=2048)
    monkeypatch.setenv("OLLAMA_MODELS", str(root))

    loc = ml.locate("qwen2:0.5b", runtime="ollama", db_path=None)

    assert loc.found is True
    assert loc.runtime == "ollama"
    assert loc.cache_kind == "ollama-blobs"
    # 主权重 = mediaType 含 "image.model" + 体积最大的 blob
    assert loc.path.endswith("blobs/sha256-abcd")
    assert loc.size_bytes == 2048
    # 也把所有 blobs 列出来，前端可以下钻
    digests = {b["digest"] for b in loc.blobs}
    assert digests == {"sha256:abcd", "sha256:1111"}
    assert all(b["exists"] is True for b in loc.blobs)
    assert loc.extra["manifest"].endswith("library/qwen2/0.5b")


def test_locate_ollama_returns_not_found_for_unknown_tag(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "ollama"))
    loc = ml.locate("nonexistent:7b", runtime="ollama", db_path=None)
    assert loc.found is False
    assert loc.cache_kind == "ollama-blobs"
    assert "未找到 manifest" in (loc.notes or "")


# ---------------------------------------------------------------------------
# HF cache（Infinity / vLLM）
# ---------------------------------------------------------------------------


def test_locate_hf_cache_picks_latest_snapshot(monkeypatch, tmp_path: Path):
    cache = tmp_path / "hf"
    monkeypatch.setenv("HF_HOME", str(cache))

    snap_root = cache / "hub" / "models--BAAI--bge-m3" / "snapshots"
    snap_root.mkdir(parents=True)
    old = snap_root / "old-rev"
    old.mkdir()
    (old / "config.json").write_text("{}")
    new = snap_root / "new-rev"
    new.mkdir()
    (new / "config.json").write_text("{}")
    (new / "model.safetensors").write_bytes(b"\x00" * 4096)
    # 给 mtime 拉开
    import os
    os.utime(old, (1, 1))
    os.utime(new, (1_000_000, 1_000_000))

    loc = ml.locate("BAAI/bge-m3", runtime="infinity", db_path=None)

    assert loc.found is True
    assert loc.cache_kind == "hf-cache"
    # 应该选了 new-rev
    assert "new-rev" in loc.path
    assert loc.path.endswith("model.safetensors")
    assert loc.dir.endswith("new-rev")
    assert loc.size_bytes >= 4096


def test_locate_hf_cache_missing_returns_found_false(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    loc = ml.locate("BAAI/never-cached", runtime="infinity", db_path=None)
    assert loc.found is False
    assert loc.cache_kind == "hf-cache"


# ---------------------------------------------------------------------------
# ComfyUI
# ---------------------------------------------------------------------------


def test_locate_comfyui_finds_in_checkpoints(monkeypatch, tmp_path: Path):
    root = tmp_path / "ComfyUI" / "models"
    (root / "checkpoints").mkdir(parents=True)
    (root / "checkpoints" / "sd_v15.safetensors").write_bytes(b"\x00" * 1024)
    monkeypatch.setenv("COMFYUI_MODELS", str(root))

    loc = ml.locate("sd_v15.safetensors", runtime="comfyui",
                    db_path=None, fmt="safetensors")
    assert loc.found is True
    assert loc.cache_kind == "comfyui"
    assert loc.path.endswith("checkpoints/sd_v15.safetensors")
    assert loc.size_bytes == 1024


def test_locate_comfyui_falls_back_to_rglob(monkeypatch, tmp_path: Path):
    """ComfyUI 子目录不在 ``_COMFY_SUBDIRS`` 映射中也能找到（rglob 兜底）。"""
    root = tmp_path / "ComfyUI" / "models"
    (root / "loras" / "subdir").mkdir(parents=True)
    fp = root / "loras" / "subdir" / "my_lora.safetensors"
    fp.write_bytes(b"\x00" * 100)
    monkeypatch.setenv("COMFYUI_MODELS", str(root))

    loc = ml.locate("my_lora.safetensors", runtime="comfyui", db_path=None, fmt="lora")
    assert loc.found is True
    assert loc.path == str(fp)


def test_locate_unknown_runtime_returns_found_false_with_db_path(tmp_path: Path):
    """unknown runtime + DB 路径不存在 → ``found=False`` 但保留路径展示。"""
    loc = ml.locate("some/repo", runtime="weird", db_path=str(tmp_path / "missing.bin"))
    assert loc.found is False
    assert loc.cache_kind == "local"
    assert loc.path == str(tmp_path / "missing.bin")
    assert loc.notes


def test_locate_unknown_runtime_no_path():
    loc = ml.locate("foo/bar", runtime=None, db_path=None)
    assert loc.found is False
    assert loc.path is None
    assert loc.notes
