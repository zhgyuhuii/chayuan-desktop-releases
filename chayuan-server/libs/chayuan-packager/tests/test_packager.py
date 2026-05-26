from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from chayuan_packager import filter_manifest, scan, verify_manifest
from chayuan_packager.cli import cli


def _make_workspace(root: Path) -> None:
    (root / "vendor" / "runtimes" / "python" / "bin").mkdir(parents=True)
    (root / "vendor" / "runtimes" / "python" / "bin" / "python").write_text("#!/bin/sh\n")
    (root / "vendor" / "services" / "ollama").mkdir(parents=True)
    (root / "vendor" / "services" / "ollama" / "ollama").write_bytes(b"\x7fELF" + b"x" * 1024)
    (root / "models" / "chat" / "Qwen--Qwen2.5-3B").mkdir(parents=True)
    (root / "models" / "chat" / "Qwen--Qwen2.5-3B" / "x.gguf").write_bytes(b"x" * 4096)
    (root / "models" / "embedding" / "BAAI--bge-m3").mkdir(parents=True)
    (root / "models" / "embedding" / "BAAI--bge-m3" / "model.safetensors").write_bytes(b"y" * 2048)


def test_scan_finds_components(tmp_path: Path):
    _make_workspace(tmp_path)
    m = scan(tmp_path)
    kinds = {c.kind for c in m.components}
    assert kinds == {"runtime", "service", "model"}
    names = {c.name for c in m.components}
    assert "ollama" in names and "Qwen/Qwen2.5-3B" in names


def test_filter_lite_caps(tmp_path: Path):
    _make_workspace(tmp_path)
    raw = scan(tmp_path)
    filtered = filter_manifest(raw, "lite")
    cats = {c.category for c in filtered.components if c.kind == "model"}
    assert cats <= {"chat", "embedding", "rerank", "ocr"}
    assert any(c.name == "ollama" for c in filtered.components)


def test_verify_ok(tmp_path: Path):
    _make_workspace(tmp_path)
    m = scan(tmp_path)
    ok, problems = verify_manifest(m)
    assert ok and problems == []


def test_cli_dry_run(tmp_path: Path):
    _make_workspace(tmp_path)
    out = tmp_path / "dist"
    r = CliRunner().invoke(cli, [
        "build", "--workspace", str(tmp_path), "--target", "linux",
        "--release", "standard", "--version", "0.1.0",
        "--out", str(out), "--dry-run",
    ])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["dry_run"] is True
    assert (out / "chayuan-0.1.0-linux-standard.tar.zst.manifest.json").is_file()
