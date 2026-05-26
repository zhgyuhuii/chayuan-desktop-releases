"""vendor_loader.discover_vendor 测试 — 用 tmp_path 假目录模拟 vendor/。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from chayuan.server.runtime.vendor_loader import discover_vendor, known_runtimes, known_services


@pytest.fixture
def fake_vendor(tmp_path: Path) -> Path:
    root = tmp_path / "vendor"
    # services/redis: 仅有 docker-compose
    (root / "services" / "redis").mkdir(parents=True)
    (root / "services" / "redis" / "docker-compose.yml").write_text("name: redis", encoding="utf-8")
    (root / "services" / "redis" / "README.md").write_text("# redis", encoding="utf-8")

    # runtimes/ollama: 仅有可执行 binary
    (root / "runtimes" / "ollama" / "bin").mkdir(parents=True)
    bin_path = root / "runtimes" / "ollama" / "bin" / "ollama"
    bin_path.write_text("#!/bin/sh\necho mock", encoding="utf-8")
    os.chmod(bin_path, 0o755)

    # services/postgres: 既无 binary 也无 compose（应判 unavailable）
    (root / "services" / "postgres").mkdir(parents=True)

    # 顶层 unknown 目录
    (root / "weirdthing").mkdir()
    return root


def test_discover_marks_redis_available_via_compose(fake_vendor):
    layout = discover_vendor(fake_vendor)
    assert layout.root == fake_vendor
    redis = next(e for e in layout.services if e.name == "redis")
    assert redis.available is True
    assert redis.docker_compose == "docker-compose.yml"
    assert redis.label == "Redis"
    assert redis.default_port == "36379"  # 与 vendor/README 对齐


def test_discover_marks_ollama_available_via_binary(fake_vendor):
    layout = discover_vendor(fake_vendor)
    ollama = next(e for e in layout.runtimes if e.name == "ollama")
    assert ollama.available is True
    assert ollama.binary.endswith("ollama")
    assert ollama.kind == "llm"


def test_discover_marks_postgres_unavailable_with_issue(fake_vendor):
    layout = discover_vendor(fake_vendor)
    pg = next(e for e in layout.services if e.name == "postgres")
    assert pg.available is False
    assert pg.issues  # 应带提示


def test_unknown_subdir_collected(fake_vendor):
    layout = discover_vendor(fake_vendor)
    names = {e.name for e in layout.unknown}
    assert "weirdthing" in names


def test_known_tables_consistent():
    services = known_services()
    runtimes = known_runtimes()
    # services / runtimes 表至少要列出我们文档中支持的服务
    for n in ("postgres", "redis", "minio", "milvus"):
        assert n in services
    for n in ("ollama", "llama-cpp", "vllm", "whisper-cpp", "piper", "comfyui"):
        assert n in runtimes
    # 端口偏好都在大端口段（避免与上游默认冲突）
    for n, meta in services.items():
        assert int(meta["default_port"]) >= 30000, f"service {n} default_port too low"


def test_discover_returns_empty_layout_when_dir_missing(tmp_path):
    layout = discover_vendor(tmp_path / "nothing")
    assert not layout.services
    assert not layout.runtimes
    assert not layout.unknown
