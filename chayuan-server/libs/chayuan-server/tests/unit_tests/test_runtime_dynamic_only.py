"""83 题:运行时与服务卡片列表完全由 ``<CHAYUAN_ROOT>/compose/*.yaml`` 决定。

* 没 yaml = 没卡
* 静态 ``_FRAMEWORK_CATALOG`` 仅作元数据 lookup,不再决定卡片列表
* docker-compose.yaml 整合大文件被排除
* 每个 yaml 内的 service 各自一张卡;用 yaml 解析的 url/health_path
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def _fake_csf(file_path: Path, services: list):
    """构造 list_compose_service_files 返回的元素。"""
    class _CSF:
        def __init__(self, p, s):
            self.file_path = p
            self.services = s
    return _CSF(file_path, services)


def test_no_yaml_means_no_cards(monkeypatch):
    """compose/ 空 → 卡片列表为空。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    monkeypatch.setattr(
        mod, "_get_dynamic_compose_specs",
        lambda: [],
    )
    catalog = mod._get_effective_catalog()
    assert catalog == []


def test_dynamic_specs_excludes_aggregate_docker_compose_yaml(tmp_path, monkeypatch):
    """``docker-compose.yaml`` 整合大文件被排除,即使内有 service。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    aggregate = tmp_path / "docker-compose.yaml"
    aggregate.write_text("services:\n  vllm:\n    image: x\n", encoding="utf-8")
    single = tmp_path / "ollama.yaml"
    single.write_text(
        "services:\n  ollama:\n    image: ollama/ollama\n    ports:\n      - 11434:11434\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cm, "list_compose_service_files",
        lambda: [
            _fake_csf(aggregate, ["vllm"]),
            _fake_csf(single, ["ollama"]),
        ],
    )

    specs = mod._get_dynamic_compose_specs()
    names = [s.name for s in specs]
    assert "ollama" in names
    assert "vllm" not in names, (
        "docker-compose.yaml 整合大文件应被排除"
    )


def test_dynamic_spec_merges_static_metadata_for_known_service(tmp_path, monkeypatch):
    """已知 service(static catalog 命中)→ 用 static label / capabilities,
    但 url 走 yaml 解析(以 yaml 为准)。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    yaml_path = tmp_path / "vllm.yaml"
    yaml_path.write_text(
        """services:
  vllm:
    image: vllm/vllm-openai:latest
    ports:
      - "29999:8000"
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8000/health || exit 1"]
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cm, "list_compose_service_files",
        lambda: [_fake_csf(yaml_path, ["vllm"])],
    )

    specs = mod._get_dynamic_compose_specs()
    assert len(specs) == 1
    s = specs[0]
    assert s.name == "vllm"
    # static catalog 的 label 和 capabilities 被借用
    assert s.label == "vLLM"
    assert "chat" in s.capabilities
    assert s.needs_gpu is True
    # url 用 yaml 自定义端口 29999,不是 static 的 18000
    assert s.default_url == "http://127.0.0.1:29999"
    assert s.health_path == "/health"
    assert s.install_kind == "docker"


def test_dynamic_spec_unknown_service_falls_back_to_yaml(tmp_path, monkeypatch):
    """未知 service(用户自加 yaml)→ 全部从 yaml 推断。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    yaml_path = tmp_path / "qdrant.yaml"
    yaml_path.write_text(
        """services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - 6333:6333
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/healthz"]
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        cm, "list_compose_service_files",
        lambda: [_fake_csf(yaml_path, ["qdrant"])],
    )

    specs = mod._get_dynamic_compose_specs()
    assert len(specs) == 1
    s = specs[0]
    assert s.name == "qdrant"
    assert s.label == "Qdrant"  # title-cased fallback
    assert s.capabilities == ("custom",)
    assert s.default_url == "http://127.0.0.1:6333"
    assert s.health_path == "/healthz"
    assert s.install_kind == "docker"


def test_pip_only_static_frameworks_no_longer_appear(tmp_path, monkeypatch):
    """static catalog 里的 pip 类(funasr/cosyvoice/rapidocr 等)如果没有
    对应 yaml 文件,**不**出现在卡片列表(用户原话:不再有 pip)。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    # 只有一个 ollama yaml,没有 funasr / cosyvoice / paddleocr 等 pip 框架
    yaml_path = tmp_path / "ollama.yaml"
    yaml_path.write_text(
        "services:\n  ollama:\n    image: ollama/ollama\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cm, "list_compose_service_files",
        lambda: [_fake_csf(yaml_path, ["ollama"])],
    )

    catalog = mod._get_effective_catalog()
    names = {s.name for s in catalog}
    assert names == {"ollama"}
    # static catalog 里有 funasr / cosyvoice / paddleocr 等不会出现
    for name in ("funasr", "cosyvoice", "rapidocr", "paddleocr",
                 "voxcpm2", "piper", "whispercpp"):
        assert name not in names, (
            f"static {name} 没有 yaml 模板,不应出现在卡片列表"
        )


def test_get_framework_spec_by_name_finds_dynamic(tmp_path, monkeypatch):
    """``get_framework_spec_by_name`` 应能找到动态发现的 service。"""
    from chayuan.server.config_panel import runtime_framework_panel as mod
    from chayuan.server.config_panel import compose_manager as cm

    yaml_path = tmp_path / "infinity.yaml"
    yaml_path.write_text(
        "services:\n  infinity:\n    image: x\n    ports:\n      - 7997:7997\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cm, "list_compose_service_files",
        lambda: [_fake_csf(yaml_path, ["infinity"])],
    )

    spec = mod.get_framework_spec_by_name("infinity")
    assert spec is not None
    assert spec.name == "infinity"
    assert spec.default_url == "http://127.0.0.1:7997"
