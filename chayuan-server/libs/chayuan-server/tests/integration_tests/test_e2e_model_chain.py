"""端到端模型链路集成测试。

模拟用户装机后的实际状态:把假 GGUF / HF 目录布到 ``<CHAYUAN_ROOT>/models/``
之下,然后逐层验证 B1 → B2 → B2.5 → B2.6 → B3 五段拼起来真能跑通:

1. :mod:`local_index.scan_once` 扫到本地模型
2. :mod:`bootstrap.check_bootstrap` 报告 ready
3. :mod:`candidates_bridge.merge_local_into_candidates` 把本地条目合并进
   capability candidates,且 ``source == "local_index"``
4. :mod:`capability_router.resolve_model` 能拿到默认 model_id
5. :mod:`process_args.resolve_all` 给出 llamacpp / infinity 的 args 含 ``--model <path>``
6. :mod:`path_resolver.resolve_model_id_to_path` 把 local_index 风格 id 翻译回磁盘路径

这一组测试故意走真实 IO(临时目录) + 真实单例(经 monkeypatch 切根),不靠 mock。
任一环回归会立即报错。
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest


# ───────────────────────── 假模型布置 ─────────────────────────


def _write_min_gguf(p: Path, architecture: str = "qwen3") -> None:
    """生成最小可被 identifier 识别的 GGUF 文件。

    GGUF 头格式::

        b'GGUF' + version(u32 LE) + tensor_count(u64 LE) + kv_count(u64 LE)
        + (key_len_u64 + key_bytes + value_type_u32 + ...) * kv_count

    这里写一对 KV:``general.architecture = <arch>``,让 identifier
    识别 capability = chat。
    """
    with open(p, "wb") as f:
        f.write(b"GGUF")
        f.write(struct.pack("<I", 3))           # version
        f.write(struct.pack("<Q", 0))           # tensor_count
        f.write(struct.pack("<Q", 1))           # kv_count
        # KV: general.architecture (string)
        key = b"general.architecture"
        f.write(struct.pack("<Q", len(key)))
        f.write(key)
        f.write(struct.pack("<I", 8))           # value_type = string
        arch_bytes = architecture.encode("utf-8")
        f.write(struct.pack("<Q", len(arch_bytes)))
        f.write(arch_bytes)


def _plant_models(root: Path) -> dict:
    """在临时 root 下布置三类模型。返回 model_id → 实际磁盘路径映射。"""
    models = root / "models"

    # chat: 单文件 GGUF(layout.yaml 风格的 dest 路径)
    chat_dir = models / "chat" / "Qwen--Qwen3-4B-Instruct-GGUF"
    chat_dir.mkdir(parents=True, exist_ok=True)
    chat_file = chat_dir / "qwen3-4b-instruct-q4_k_m.gguf"
    _write_min_gguf(chat_file, architecture="qwen3")

    # embedding: HF transformers 目录(config.json + model_type=bge)
    embed_dir = models / "embedding" / "BAAI--bge-m3"
    embed_dir.mkdir(parents=True, exist_ok=True)
    (embed_dir / "config.json").write_text(json.dumps({
        "model_type": "bge", "hidden_size": 1024, "vocab_size": 250002,
    }))

    # rerank: 故意**不**写 config.json —— 真实 bge-reranker-v2-m3 的底座是
    # xlm-roberta,identifier Level 1 会把它归到 ``text-embedding``(0.95 置信)
    # 压过 Level 5 路径关键字(0.3 置信)。这是 identifier 当前一个已知局限。
    # 这里用 tokenizer.json 触发 ``_is_dir_repo`` 但跳过 Level 1,让路径关键字
    # ``rerank`` 真正生效(模拟实际部署中不带 config.json 的 rerank 模型目录,
    # 或先经过 path_hints 兜底场景)。
    rerank_dir = models / "rerank" / "BAAI--bge-reranker-v2-m3"
    rerank_dir.mkdir(parents=True, exist_ok=True)
    (rerank_dir / "tokenizer.json").write_text("{}")

    return {
        "chat":      str(chat_file),
        "embedding": str(embed_dir),
        "rerank":    str(rerank_dir),
    }


# ───────────────────────── fixture ─────────────────────────


@pytest.fixture
def model_root(tmp_path, monkeypatch):
    """构造临时 CHAYUAN_ROOT + 三类模型,重置 local_index 单例。

    监替换 ``chayuan.settings.CHAYUAN_ROOT`` 为临时路径;同步重置
    ``local_index._SINGLETON`` 让单例重新按新 root 加载。
    """
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    paths = _plant_models(tmp_path)

    # 重置 local_index 单例,让 get_local_index() 用新 root 重建
    import chayuan.server.model_registry.local_index as li
    monkeypatch.setattr(li, "_SINGLETON", None)

    yield tmp_path, paths


# ───────────────────────── Layer 1: scan ─────────────────────────


def test_scan_finds_planted_models(model_root):
    from chayuan.server.model_registry.local_index import scan_once

    delta = scan_once()
    # 至少三个新条目落地
    assert len(delta.added) >= 3
    caps = {e.capability for e in delta.added}
    assert "chat" in caps
    assert "text-embedding" in caps
    assert "rerank" in caps


# ───────────────────────── Layer 2: bootstrap ─────────────────────────


def test_bootstrap_ready_after_scan(model_root):
    """三类必需 capability 都布到位 → bootstrap.ready=True。"""
    from chayuan.server.model_registry.bootstrap import check_bootstrap
    from chayuan.server.model_registry.local_index import scan_once

    scan_once()
    report = check_bootstrap(do_scan=False)
    assert report.ready is True
    assert report.missing == []
    sat = {s.capability: s.satisfied for s in report.statuses}
    assert sat == {"chat": True, "text-embedding": True, "rerank": True}


# ───────────────────────── Layer 3: candidates_bridge ─────────────────────────


def test_local_candidates_appear_in_capability_dict(model_root):
    """合并后的 candidates 应该至少含 3 条 source=local_index 的本地记录。"""
    from chayuan.server.model_registry.candidates_bridge import (
        merge_local_into_candidates,
    )
    from chayuan.server.model_registry.local_index import scan_once

    scan_once()
    candidates: dict[str, list[dict]] = {
        "chat": [], "embedding": [], "rerank": [],
    }
    merge_local_into_candidates(candidates, do_scan=False)

    for cap in ("chat", "embedding", "rerank"):
        assert candidates[cap], f"{cap} 没拿到本地候选"
        ids = {c["id"] for c in candidates[cap]}
        sources = {c["source"] for c in candidates[cap]}
        assert sources == {"local_index"}
        # 路径都填上了
        for c in candidates[cap]:
            assert c["path"]


# ───────────────────────── Layer 4: path_resolver ─────────────────────────


def test_path_resolver_translates_model_id(model_root):
    """local_index 风格 model_id 应该翻译回 entry.path。"""
    from chayuan.server.model_registry.local_index import (
        get_local_index,
        scan_once,
    )
    from chayuan.server.model_registry.path_resolver import (
        resolve_model_id_to_path,
    )

    scan_once()
    entries = get_local_index().by_capability("rerank")
    assert entries
    mid = entries[0].model_id
    expected_path = entries[0].path
    assert resolve_model_id_to_path(mid) == expected_path


def test_path_resolver_passes_unknown_through(model_root):
    """非 local_index 标识(HF repo / 绝对路径)应原样返回。"""
    from chayuan.server.model_registry.path_resolver import (
        resolve_model_id_to_path,
    )
    assert resolve_model_id_to_path("BAAI/bge-m3") == "BAAI/bge-m3"
    assert resolve_model_id_to_path("/opt/manual/x") == "/opt/manual/x"


# ─────────────────────── Layer 5: process_args ───────────────────────


def test_process_args_resolves_llamacpp_from_local(model_root):
    """没有任何 capability default 时,process_args 应该兜底取 local_index 第一个候选。"""
    from chayuan.server.model_registry.local_index import scan_once
    from chayuan.server.model_registry.process_args import (
        resolve_llamacpp_args,
    )

    scan_once()
    r = resolve_llamacpp_args()
    assert r.ok, f"llamacpp 应能从本地兜底解析,但 missing={r.missing} reason={r.reason}"
    assert "--model" in r.args
    # 解析出的 model 路径应在临时 root 之下
    root, paths = model_root
    model_idx = r.args.index("--model")
    model_path = r.args[model_idx + 1]
    assert model_path.startswith(str(root))
    assert model_path == paths["chat"]


def test_process_args_resolves_infinity_embed_and_rerank(model_root):
    """infinity 同时挂 embed + rerank,应该两类都解出来。"""
    from chayuan.server.model_registry.local_index import scan_once
    from chayuan.server.model_registry.process_args import (
        resolve_infinity_args,
    )

    scan_once()
    r = resolve_infinity_args()
    # 两类都解出来 → 没有 missing
    assert r.missing == [], f"reason={r.reason}"
    # --model-id 应该出现两次(embed + rerank)
    assert r.args.count("--model-id") == 2
    # 两条路径都落在 root 下
    root, paths = model_root
    paths_in_args = [a for a in r.args if str(root) in a]
    assert len(paths_in_args) == 2


def test_process_args_resolve_all_full_snapshot(model_root):
    """resolve_all() 一次性把三个进程都解掉。"""
    from chayuan.server.model_registry.local_index import scan_once
    from chayuan.server.model_registry.process_args import resolve_all

    scan_once()
    snap = resolve_all()
    assert set(snap.keys()) == {"llamacpp", "infinity", "ollama"}
    assert snap["llamacpp"].ok
    # infinity 至少有一类解上
    assert snap["infinity"].resolved_models
    # ollama env 至少有 OLLAMA_MODELS
    assert "OLLAMA_MODELS" in snap["ollama"].env
