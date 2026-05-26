"""Config Panel · provider_hero_strip · 排序 / 过滤纯函数单测。

NiceGUI 渲染部分不测，因为依赖 ui session；只测 ``select_top_providers``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

import pytest

from chayuan.server.config_panel.provider_hero_strip import (
    KIND_CLOUD, KIND_LOCAL,
    select_top_providers, _is_local_provider, _provider_passes_kind,
)


@dataclass
class _Fake:
    pid: str
    display_name: str
    color: str = "#000"
    tags: Tuple[str, ...] = ()
    apply_key_url: str = ""


def _make(pid: str, *tags: str) -> _Fake:
    return _Fake(pid=pid, display_name=pid.upper(), tags=tuple(tags))


def test_local_providers_filtered_out():
    """用户要求："不再展示 ollama 等本地模型服务，因为上面已经有了"。"""
    catalog = [
        _make("ollama", "本地", "推荐"),
        _make("openai", "国外"),
        _make("xinference", "本地"),
    ]
    assert _is_local_provider(catalog[0]) is True
    assert _is_local_provider(catalog[1]) is False
    rows = select_top_providers(catalog, lambda _p: (False, False, 0))
    pids = [r.pid for r in rows]
    assert "ollama" not in pids
    assert "xinference" not in pids
    assert "openai" in pids


def test_enabled_providers_always_in_top_n():
    """已启用的厂商**永远**优先展示，不会被裁掉，即使它在 catalog 末尾。"""
    catalog = [
        _make(f"x{i}", "国外") for i in range(20)
    ] + [
        _make("zenith", "国外"),  # 排最后但 enabled=True
    ]

    def _lookup(pid: str):
        return (pid == "zenith", False, 5 if pid == "zenith" else 0)

    rows = select_top_providers(catalog, _lookup, limit=8)
    pids = [r.pid for r in rows]
    assert "zenith" in pids
    # 而且应该排第一位（enabled bucket=0）
    assert pids[0] == "zenith"


def test_recommended_above_foreign_above_other():
    """同档情况下，"推荐" > "国外" > 其余。"""
    catalog = [
        _make("misc", "其它"),
        _make("foreign", "国外"),
        _make("rec", "推荐", "国内"),
    ]
    rows = select_top_providers(catalog, lambda _p: (False, False, 0), limit=8)
    pids = [r.pid for r in rows]
    assert pids.index("rec") < pids.index("foreign")
    assert pids.index("foreign") < pids.index("misc")


def test_limit_param_caps_count():
    catalog = [_make(f"p{i}", "国外") for i in range(20)]
    rows = select_top_providers(catalog, lambda _p: (False, False, 0), limit=5)
    assert len(rows) == 5


def test_default_limit_is_eight():
    """对应需求："横向展示 8 个模型供应商"。"""
    catalog = [_make(f"p{i}", "国外") for i in range(20)]
    rows = select_top_providers(catalog, lambda _p: (False, False, 0))
    assert len(rows) == 8


def test_state_lookup_propagated_into_row():
    """state_lookup 返的 enabled / configured / model_count 必须挂到 ProviderRowState 上。"""
    catalog = [_make("openai", "国外")]
    rows = select_top_providers(
        catalog, lambda _p: (True, True, 17), limit=8,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.enabled is True
    assert r.configured is True
    assert r.model_count == 17
    assert r.tags == ("国外",)


def test_real_provider_catalog_picks_eight_non_local(monkeypatch):
    """对接真实 ``PROVIDER_CATALOG``：8 个非本地厂商应该顺利返回。"""
    from chayuan.server.config_panel.model_config import PROVIDER_CATALOG
    rows = select_top_providers(
        PROVIDER_CATALOG, lambda _p: (False, False, 0), limit=8,
    )
    assert len(rows) == 8
    for r in rows:
        # 没有任何 row 应该带"本地" tag
        assert "本地" not in r.tags, f"{r.pid} 不应出现在云端 hero strip"


# ---------------------------------------------------------------------------
# kind 参数化(56-1 题:本地模型行)
# ---------------------------------------------------------------------------


def test_kind_local_only_keeps_local_providers():
    """``kind="local"`` 应该只保留 tags 含"本地"的厂商。"""
    catalog = [
        _make("ollama", "本地", "推荐"),
        _make("openai", "国外"),
        _make("vllm", "本地"),
        _make("misc", "其它"),
    ]
    rows = select_top_providers(
        catalog, lambda _p: (False, False, 0), kind=KIND_LOCAL,
    )
    pids = [r.pid for r in rows]
    assert set(pids) == {"ollama", "vllm"}
    # cloud 厂商绝不出现
    assert "openai" not in pids
    assert "misc" not in pids


def test_kind_cloud_explicit_matches_default():
    """显式 kind="cloud" 与缺省一致。"""
    catalog = [
        _make("ollama", "本地"),
        _make("openai", "国外"),
        _make("baidu", "国内"),
    ]
    default = select_top_providers(catalog, lambda _p: (False, False, 0))
    explicit = select_top_providers(
        catalog, lambda _p: (False, False, 0), kind=KIND_CLOUD,
    )
    assert [r.pid for r in default] == [r.pid for r in explicit]


def test_kind_local_recommended_above_others():
    """本地行排序也尊重"启用 → 推荐 → 其余",但不分国外/国内档。"""
    catalog = [
        _make("plain-local", "本地"),
        _make("rec-local", "本地", "推荐"),
        _make("vllm", "本地"),
    ]
    rows = select_top_providers(
        catalog, lambda _p: (False, False, 0), kind=KIND_LOCAL,
    )
    pids = [r.pid for r in rows]
    # 推荐排第一
    assert pids[0] == "rec-local"


def test_kind_local_enabled_first():
    """已启用的本地服务永远排首位,与 cloud 行同规则。"""
    catalog = [
        _make("rec-local", "本地", "推荐"),
        _make("active-local", "本地"),
    ]

    def _lookup(pid: str):
        return (pid == "active-local", True, 3)

    rows = select_top_providers(
        catalog, _lookup, kind=KIND_LOCAL, limit=8,
    )
    assert rows[0].pid == "active-local"


def test_provider_passes_kind_helper():
    """``_provider_passes_kind`` 行为对照表。"""
    p_local = _make("ollama", "本地")
    p_cloud = _make("openai", "国外")
    p_dual = _make("dual", "本地", "聚合")  # 双 tag,本地优先
    assert _provider_passes_kind(p_local, KIND_LOCAL) is True
    assert _provider_passes_kind(p_local, KIND_CLOUD) is False
    assert _provider_passes_kind(p_cloud, KIND_LOCAL) is False
    assert _provider_passes_kind(p_cloud, KIND_CLOUD) is True
    # 双 tag 算"本地"(任一含"本地"即视作本地)
    assert _provider_passes_kind(p_dual, KIND_LOCAL) is True
    assert _provider_passes_kind(p_dual, KIND_CLOUD) is False


def test_real_catalog_local_strip_includes_new_services():
    """真实 catalog: 本地 strip 应包含 56-1 题新增的 8 家本地服务。"""
    from chayuan.server.config_panel.model_config import PROVIDER_CATALOG
    # 把 limit 调大,跳过 8 限制,看真实集合
    rows = select_top_providers(
        PROVIDER_CATALOG, lambda _p: (False, False, 0),
        limit=100, kind=KIND_LOCAL,
    )
    pids = {r.pid for r in rows}
    # 历史已有
    assert {"ollama", "xinference", "vllm", "lm-studio", "gpustack"} <= pids
    # 56-1 新增
    assert {"llama-cpp", "localai", "tgi", "text-gen-webui",
            "koboldcpp", "jan", "gpt4all", "openllm"} <= pids
    # oneapi/new-api 已重分类到本地
    assert {"oneapi", "new-api"} <= pids
    # 任一 cloud 都不应出现
    for p in rows:
        assert "本地" in p.tags


def test_real_catalog_cloud_strip_excludes_new_local_services():
    """真实 catalog: cloud strip 不能漏出本地服务(双 tag 也要排除)。"""
    from chayuan.server.config_panel.model_config import PROVIDER_CATALOG
    rows = select_top_providers(
        PROVIDER_CATALOG, lambda _p: (False, False, 0),
        limit=100, kind=KIND_CLOUD,
    )
    pids = {r.pid for r in rows}
    forbidden = {
        "ollama", "xinference", "vllm", "lm-studio", "gpustack",
        "llama-cpp", "localai", "tgi", "text-gen-webui",
        "koboldcpp", "jan", "gpt4all", "openllm",
        "oneapi", "new-api",
    }
    assert pids.isdisjoint(forbidden), (
        f"cloud strip 漏出本地服务: {pids & forbidden}"
    )


def test_local_providers_have_logo_files():
    """每个本地条目的 logo 字段对应的物理文件应存在(避免 404 灰头像)。"""
    from pathlib import Path
    from chayuan.server.config_panel.model_config import PROVIDER_CATALOG
    logo_dir = (
        Path(__file__).resolve().parents[2]
        / "chayuan" / "img" / "model_logos"
    )
    missing = []
    for p in PROVIDER_CATALOG:
        if "本地" not in p.tags:
            continue
        if not p.logo:
            continue  # 允许首字母 fallback
        if not (logo_dir / p.logo).exists():
            missing.append(f"{p.pid} -> {p.logo}")
    assert not missing, f"以下本地厂商 logo 文件缺失:\n  " + "\n  ".join(missing)
