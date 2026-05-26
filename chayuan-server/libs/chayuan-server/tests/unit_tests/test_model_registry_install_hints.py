"""``install_hints.build_install_hints`` 行为测试 + 与 layout.yaml 的对账。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from chayuan.server.model_registry.install_hints import (
    DEFAULT_MIRRORS,
    InstallHint,
    build_install_hints,
)


# ───────────────────────────── 主流程 ──────────────────────────────


def test_empty_when_nothing_missing():
    assert build_install_hints([]) == []


def test_chat_recommends_lite():
    hints = build_install_hints(["chat"])
    assert len(hints) == 1
    h = hints[0]
    assert h.release == "lite"
    assert "chat" in h.covered_capabilities
    assert h.approx_size_mb > 0
    assert h.mirrors  # 默认镜像必须带


def test_three_basic_capabilities_merge_into_lite():
    hints = build_install_hints(["chat", "text-embedding", "rerank"])
    assert len(hints) == 1
    assert hints[0].release == "lite"
    assert sorted(hints[0].covered_capabilities) == [
        "chat", "rerank", "text-embedding",
    ]


def test_order_lite_before_standard():
    """lite / standard 都需要时，lite 排前。"""
    hints = build_install_hints(["chat", "image-to-text"])
    assert [h.release for h in hints] == ["lite", "standard"]


def test_unknown_capability_falls_back_to_lite():
    hints = build_install_hints(["unknown-xyz"])
    assert hints
    assert hints[0].release == "lite"


def test_dedup_capability():
    """同一 capability 重复传入不应在结果里重复出现。"""
    hints = build_install_hints(["chat", "chat", "chat"])
    assert len(hints) == 1
    assert hints[0].covered_capabilities == ["chat"]


# ───────────────────────────── 镜像 ──────────────────────────────


def test_default_mirrors_have_hf_mirror_first():
    """中国大陆推荐排前。"""
    assert DEFAULT_MIRRORS
    assert DEFAULT_MIRRORS[0]["name"] == "hf-mirror"
    # 应该至少含 huggingface 官方
    names = {m["name"] for m in DEFAULT_MIRRORS}
    assert "huggingface" in names


def test_mirrors_isolated_per_hint():
    """每个 hint 拿到的 mirrors 是独立拷贝；改一个不应该影响其它。"""
    hints = build_install_hints(["chat", "image-to-text"])
    h1, h2 = hints
    h1.mirrors[0]["name"] = "MUTATED"
    assert h2.mirrors[0]["name"] != "MUTATED"


# ───────────────────────────── 序列化 ──────────────────────────────


def test_install_hint_serializable():
    h = build_install_hints(["chat"])[0]
    s = json.dumps(h.to_dict())
    d = json.loads(s)
    assert d["release"] == "lite"
    assert d["mirrors"]
    assert isinstance(d["approx_size_mb"], int)


# ─────────────────────── 与 layout.yaml 对账 ───────────────────────


def _layout_yaml_path() -> Path:
    """packaging/python312/layout.yaml 相对当前测试文件的位置。"""
    here = Path(__file__).resolve()
    # here = .../chayuan-server/libs/chayuan-server/tests/unit_tests/<this>
    # parents[0] = unit_tests
    # parents[1] = tests
    # parents[2] = libs/chayuan-server
    # parents[3] = libs
    # parents[4] = chayuan-server (repo root) ← packaging/ 就在这里
    return here.parents[4] / "packaging" / "python312" / "layout.yaml"


def _capabilities_in_release(layout: dict, release: str) -> set[str]:
    """根据 layout.yaml 的 release.<name>.models 推断该 release 覆盖的 capability 集合。

    用模型条目的 ``dest`` 路径（``models/<capability>/<vendor>--<name>``）
    第二段作为 capability 字段——这是 layout.yaml 本身的约定。
    """
    release_spec = (layout.get("releases") or {}).get(release, {})
    model_names: list[str] = list(release_spec.get("models") or [])
    by_name = {m["name"]: m for m in (layout.get("models") or [])}
    caps: set[str] = set()
    for name in model_names:
        m = by_name.get(name)
        if not m:
            continue
        dest = str(m.get("dest") or "")
        # dest 形如 "models/chat/Qwen--..."
        parts = dest.split("/")
        if len(parts) >= 2 and parts[0] == "models":
            kind = parts[1]
            # 把 dest 用的简写映射回 identifier 用的 capability 全称
            caps.add(_kind_to_capability(kind))
    return caps


def _kind_to_capability(kind: str) -> str:
    return {
        "chat": "chat",
        "embedding": "text-embedding",
        "rerank": "rerank",
        "asr": "speech-to-text",
        "ocr": "image-to-text",
    }.get(kind, kind)


def test_install_hints_align_with_layout_yaml():
    """安装提示推荐的 release 必须真能覆盖到对应的 capability。

    这是个保护性测试——如果 layout.yaml 升级模型时改了 release 段，但
    ``_CAP_TO_RELEASE`` 没跟上，这里立刻挂掉。
    """
    layout_path = _layout_yaml_path()
    if not layout_path.is_file():
        pytest.skip(f"layout.yaml not found at {layout_path}")

    layout = yaml.safe_load(layout_path.read_text(encoding="utf-8"))

    # 把所有 capability 在 install_hints 里推荐到的 release 取出来，
    # 然后核对该 release 的 layout.yaml 模型清单确实涵盖这个 capability。
    for cap in ("chat", "text-embedding", "rerank", "speech-to-text", "image-to-text"):
        hints = build_install_hints([cap])
        assert hints, f"capability {cap} 应该有至少一个推荐 release"
        recommended = hints[0].release
        covered = _capabilities_in_release(layout, recommended)
        assert cap in covered, (
            f"install_hints 推荐 {cap} → {recommended}，但 layout.yaml "
            f"中 {recommended} 不含 {cap}（覆盖集 = {covered}）"
        )
