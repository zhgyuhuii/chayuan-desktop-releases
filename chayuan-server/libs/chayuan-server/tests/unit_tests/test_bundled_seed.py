"""``model_registry.bundled_seed.seed_bundled_models`` 测试。"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture
def chayuan_root_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    import chayuan.server.model_registry.local_index as li
    monkeypatch.setattr(li, "_SINGLETON", None)
    return tmp_path


@pytest.fixture
def bundled_source(tmp_path, monkeypatch):
    """在 tmp_path/bundled_source 下建一个最小 bundled_models 内容,并 monkey 到 env。"""
    src = tmp_path / "bundled_source"
    (src / "chat").mkdir(parents=True)
    (src / "embedding").mkdir(parents=True)
    (src / "rerank").mkdir(parents=True)
    (src / "asr").mkdir(parents=True)  # 故意空目录,验证空目录被跳过
    (src / "custom").mkdir(parents=True)

    (src / "chat" / "qwen3-4b.gguf").write_bytes(b"chat-model-binary")
    (src / "embedding" / "bge-m3.onnx").write_bytes(b"emb-onnx")
    # multi-file repo
    rerank_repo = src / "rerank" / "bge-reranker-base"
    rerank_repo.mkdir()
    (rerank_repo / "tokenizer.json").write_text("{}")
    (rerank_repo / "model.safetensors").write_bytes(b"rerank-weights")
    # custom + .gitkeep 占位
    (src / "custom" / ".gitkeep").write_text("")
    monkeypatch.setenv("CHAYUAN_BUNDLED_MODELS_DIR", str(src))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return src


def test_seed_copies_into_chayuan_root_models_bundled(chayuan_root_tmp, bundled_source):
    from chayuan.server.model_registry.bundled_seed import seed_bundled_models

    report = seed_bundled_models()

    target = chayuan_root_tmp / "models" / "bundled"
    assert report.source == str(bundled_source)
    assert report.target == str(target)
    assert (target / "chat" / "qwen3-4b.gguf").is_file()
    assert (target / "embedding" / "bge-m3.onnx").is_file()
    assert (target / "rerank" / "bge-reranker-base" / "tokenizer.json").is_file()
    assert (target / "rerank" / "bge-reranker-base" / "model.safetensors").is_file()

    # asr 是空目录(只有 .gitkeep + 普通空) -> 不在 target 出现
    assert not (target / "asr").exists()
    # custom 只有 .gitkeep -> 空目录 -> 同上
    assert not (target / "custom").exists()

    assert "bundled/chat/qwen3-4b.gguf" in report.copied
    assert "bundled/embedding/bge-m3.onnx" in report.copied
    assert report.errors == []


def test_seed_is_idempotent(chayuan_root_tmp, bundled_source):
    from chayuan.server.model_registry.bundled_seed import seed_bundled_models

    seed_bundled_models()
    second = seed_bundled_models()

    # 第二次:所有文件都已存在 size 匹配 -> 应该全部 skip,不 copy
    assert second.copied == [], f"second run should not copy, got {second.copied}"
    assert "bundled/chat/qwen3-4b.gguf" in second.skipped
    assert "bundled/embedding/bge-m3.onnx" in second.skipped


def test_seed_preserves_user_replacement(chayuan_root_tmp, bundled_source):
    """用户在 CHAYUAN_ROOT/models/bundled 下手动改过的文件,seed 不应覆盖。"""
    from chayuan.server.model_registry.bundled_seed import seed_bundled_models

    target = chayuan_root_tmp / "models" / "bundled"
    target.mkdir(parents=True, exist_ok=True)
    (target / "chat").mkdir(exist_ok=True)
    user_file = target / "chat" / "qwen3-4b.gguf"
    user_file.write_bytes(b"my-custom-replacement-bytes-XXX")
    # 让用户文件的 mtime 比 bundled 新
    later = time.time() + 100
    os.utime(user_file, (later, later))

    seed_bundled_models()

    assert user_file.read_bytes() == b"my-custom-replacement-bytes-XXX"


def test_seed_replaces_outdated_bundled_copy(chayuan_root_tmp, bundled_source):
    """目标存在但 size 不同 且 mtime 不比 bundled 新 -> 当作旧版 bundled 拷贝,允许替换。"""
    from chayuan.server.model_registry.bundled_seed import seed_bundled_models

    target = chayuan_root_tmp / "models" / "bundled" / "chat"
    target.mkdir(parents=True, exist_ok=True)
    old = target / "qwen3-4b.gguf"
    old.write_bytes(b"old")  # 比 bundled 短
    # 把 mtime 调到很久以前
    long_ago = time.time() - 86400
    os.utime(old, (long_ago, long_ago))

    report = seed_bundled_models()

    assert old.read_bytes() == b"chat-model-binary"
    assert "bundled/chat/qwen3-4b.gguf" in report.copied


def test_seed_returns_empty_report_when_source_missing(tmp_path, monkeypatch):
    """bundled_models_dir 找不到时,返回空 report,无异常。"""
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    monkeypatch.setenv("CHAYUAN_BUNDLED_MODELS_DIR", str(tmp_path / "nope"))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    # 把 __file__ 改到一个 parents[5] 也找不到的位置
    import chayuan.server.model_registry.local_index as li
    fake = tmp_path / "fake" / "a" / "b" / "c" / "d" / "e"
    fake.mkdir(parents=True)
    monkeypatch.setattr(li, "__file__", str(fake / "local_index.py"))
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "fake" / "no-exe")])

    from chayuan.server.model_registry.bundled_seed import seed_bundled_models

    report = seed_bundled_models()
    assert report.source is None
    assert report.copied == []
    assert report.skipped == []
    assert report.errors == []


def test_seeded_files_appear_in_scan(chayuan_root_tmp, bundled_source):
    """seed -> scan_once 后,bundled 文件出现在 local_index 里,relpath 带 'bundled/' 前缀。"""
    from chayuan.server.model_registry.bundled_seed import seed_bundled_models
    from chayuan.server.model_registry.local_index import (
        get_local_index,
        scan_once,
    )

    seed_bundled_models()
    scan_once()

    idx = get_local_index()
    entries = idx.list_entries()
    relpaths = {e.relpath for e in entries}
    assert any(r.startswith("bundled/chat/qwen3-4b.gguf") for r in relpaths), relpaths
    assert any(r.startswith("bundled/embedding/bge-m3.onnx") for r in relpaths), relpaths


def test_seed_to_dict_is_json_safe(chayuan_root_tmp, bundled_source):
    import json
    from chayuan.server.model_registry.bundled_seed import seed_bundled_models

    report = seed_bundled_models()
    blob = json.dumps(report.to_dict())
    parsed = json.loads(blob)
    assert "source" in parsed
    assert "target" in parsed
    assert "copied" in parsed
    assert isinstance(parsed["copied"], list)
    # 新增 external_pending / external_source 字段也得 JSON 安全
    assert "external_pending" in parsed
    assert "external_source" in parsed


# ──────────────────────────── External seed (ISO) ────────────────────────────


def test_external_seed_copies_from_mounted_media(chayuan_root_tmp, bundled_source, tmp_path, monkeypatch):
    """build.py 分流 ≥ 2 GB 大文件到外部介质,bundled_seed 应自动扫挂载点拷过去。

    模拟链路:
      bundled_source/  <-- 装机后的 <install>/bundled_models/(小文件 + manifest)
        .external_seed_manifest.json    -> 期望 embedding/big-model.bin (size=42)
        (没有 embedding/big-model.bin)  <-- 大文件被分流走了

      fake_media/models_seed/           <-- 模拟挂载的 ISO 光驱
        embedding/big-model.bin (42 bytes)

    bundled_seed 应该:
      1. 读 manifest 发现 big-model.bin 缺
      2. 扫候选挂载点,找到匹配介质
      3. 拷到 <CHAYUAN_ROOT>/models/bundled/embedding/big-model.bin
      4. report.external_source 指向找到的根, external_pending 为空
    """
    import json
    from chayuan.server.model_registry import bundled_seed as bs

    # 1) 在 bundled_source 里塞 manifest(模拟 build.py 写好的清单)
    big_content = b"X" * 42
    manifest = {
        "version": 1,
        "iso_volume_hint": "CHAYUAN_FULL",
        "files": [
            {"path": "embedding/big-model.bin", "size": len(big_content)},
        ],
    }
    (bundled_source / bs.EXTERNAL_SEED_MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )

    # 2) 模拟挂载的 ISO:tmp/fake_media/models_seed/embedding/big-model.bin
    fake_media = tmp_path / "fake_media"
    seed_root = fake_media / "models_seed"
    (seed_root / "embedding").mkdir(parents=True)
    (seed_root / "embedding" / "big-model.bin").write_bytes(big_content)

    # 3) monkey 候选介质枚举:只产出 fake_media 的 models_seed/
    def fake_candidates():
        yield seed_root

    monkeypatch.setattr(bs, "_candidate_external_roots", fake_candidates)

    # 4) 跑 seed
    report = bs.seed_bundled_models()

    # 5) 验:
    target_big = chayuan_root_tmp / "models" / "bundled" / "embedding" / "big-model.bin"
    assert target_big.is_file(), "外部介质里的大文件应该被拷到 target"
    assert target_big.read_bytes() == big_content
    assert report.external_source == str(seed_root)
    assert report.external_pending == []
    assert any("external/embedding/big-model.bin" in s for s in report.copied)


def test_external_seed_pending_when_media_absent(chayuan_root_tmp, bundled_source, monkeypatch):
    """没找到匹配介质时:external_pending 非空 + errors 写明显提示,不抛异常。"""
    import json
    from chayuan.server.model_registry import bundled_seed as bs

    (bundled_source / bs.EXTERNAL_SEED_MANIFEST_NAME).write_text(
        json.dumps({
            "version": 1,
            "iso_volume_hint": "CHAYUAN_FULL",
            "files": [{"path": "chat/huge.gguf", "size": 99999}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    # 候选枚举返回空 — 没插光驱的场景
    monkeypatch.setattr(bs, "_candidate_external_roots", lambda: iter([]))

    report = bs.seed_bundled_models()

    assert "chat/huge.gguf" in report.external_pending
    assert report.external_source is None
    assert any("未找到匹配的外部介质" in e for e in report.errors)


def test_external_seed_idempotent_when_target_already_present(chayuan_root_tmp, bundled_source, monkeypatch):
    """target 已经有大文件 + size 匹配时,不应再扫介质 — _candidate_external_roots 不该被调用。"""
    import json
    from chayuan.server.model_registry import bundled_seed as bs

    big = b"Y" * 17
    target = chayuan_root_tmp / "models" / "bundled" / "embedding" / "big.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(big)

    (bundled_source / bs.EXTERNAL_SEED_MANIFEST_NAME).write_text(
        json.dumps({
            "version": 1,
            "files": [{"path": "embedding/big.bin", "size": len(big)}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    called = {"n": 0}
    def spy():
        called["n"] += 1
        yield from ()

    monkeypatch.setattr(bs, "_candidate_external_roots", spy)
    report = bs.seed_bundled_models()

    assert called["n"] == 0, "target 已齐时不该扫介质"
    assert report.external_pending == []
    assert report.external_source is None
