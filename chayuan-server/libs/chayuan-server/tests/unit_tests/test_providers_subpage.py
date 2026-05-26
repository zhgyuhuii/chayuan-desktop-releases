"""模型设置 v2 ② 厂商页 — 云分组纯函数单测(57 题 P2)。

NiceGUI 渲染部分不测,只测 ``group_cloud_providers`` /
``_classify_cloud_provider`` 这种无 UI 依赖的纯函数。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from chayuan.server.config_panel.model_config import (
    PROVIDER_CATALOG,
    _CLOUD_GROUP_ORDER,
    _classify_cloud_provider,
    group_cloud_providers,
)


@dataclass
class _Fake:
    """轻量 ProviderMeta 替身,够 _classify_cloud_provider / group_cloud_providers 用。"""
    pid: str
    display_name: str = ""
    tags: Tuple[str, ...] = ()


def _make(pid: str, *tags: str, name: str = "") -> _Fake:
    return _Fake(pid=pid, display_name=name or pid.upper(), tags=tuple(tags))


# ---------------------------------------------------------------------------
# _classify_cloud_provider
# ---------------------------------------------------------------------------


def test_classify_local_returns_empty():
    """tags 含'本地' → 不属于云分组(返空字符串)。"""
    assert _classify_cloud_provider(_make("ollama", "本地", "推荐")) == ""
    assert _classify_cloud_provider(_make("vllm", "本地")) == ""


def test_classify_recommended_takes_priority_over_country():
    """同时含'推荐'和'国内' → 归到'推荐'(推荐优先)。"""
    assert _classify_cloud_provider(_make("deepseek", "国内", "推荐")) == "推荐"


def test_classify_country_groups():
    assert _classify_cloud_provider(_make("openai", "国外")) == "国外"
    assert _classify_cloud_provider(_make("moonshot", "国内")) == "国内"


def test_classify_aggregator():
    assert _classify_cloud_provider(_make("openrouter", "聚合")) == "聚合"


def test_classify_other_when_no_known_tag():
    """没有任何已知 tag → 其它。"""
    assert _classify_cloud_provider(_make("mystery")) == "其它"
    assert _classify_cloud_provider(_make("other", "未知 tag")) == "其它"


# ---------------------------------------------------------------------------
# group_cloud_providers
# ---------------------------------------------------------------------------


def test_group_basic_segregation():
    catalog = [
        _make("ollama", "本地"),       # 本地不出现
        _make("openai", "国外"),
        _make("deepseek", "国内", "推荐"),
        _make("moonshot", "国内"),
        _make("openrouter", "聚合"),
    ]
    g = group_cloud_providers(catalog)
    assert "推荐" in g and len(g["推荐"]) == 1
    assert "国内" in g and {p.pid for p in g["国内"]} == {"moonshot"}
    assert "国外" in g and {p.pid for p in g["国外"]} == {"openai"}
    assert "聚合" in g and {p.pid for p in g["聚合"]} == {"openrouter"}
    # 本地排除
    all_pids = {p.pid for v in g.values() for p in v}
    assert "ollama" not in all_pids


def test_group_empty_groups_omitted():
    """没有任何'国外'厂商 → '国外' key 不出现在结果里。"""
    catalog = [_make("deepseek", "国内", "推荐"), _make("moonshot", "国内")]
    g = group_cloud_providers(catalog)
    assert "国外" not in g
    assert "聚合" not in g


def test_group_search_filter():
    """search 在 pid / display_name 中模糊匹配(忽略大小写)。"""
    catalog = [
        _make("openai", "国外", name="OpenAI"),
        _make("deepseek", "国内", "推荐", name="深度求索"),
        _make("moonshot", "国内", name="月之暗面"),
    ]
    g = group_cloud_providers(catalog, search="DEEP")
    assert sum(len(v) for v in g.values()) == 1
    assert g["推荐"][0].pid == "deepseek"
    # 中文也能匹配
    g = group_cloud_providers(catalog, search="月之")
    assert sum(len(v) for v in g.values()) == 1
    assert g["国内"][0].pid == "moonshot"


def test_group_filter_tag():
    """filter_tag 仅保留含此 tag 的厂商。"""
    catalog = [
        _make("openai", "国外"),
        _make("deepseek", "国内", "推荐"),
        _make("moonshot", "国内"),
    ]
    g = group_cloud_providers(catalog, filter_tag="国内")
    assert "国外" not in g
    pids = {p.pid for v in g.values() for p in v}
    assert pids == {"deepseek", "moonshot"}


def test_group_order_follows_constant():
    """返回的 dict 按 _CLOUD_GROUP_ORDER 的顺序保留 key。"""
    catalog = [
        _make("openrouter", "聚合"),
        _make("openai", "国外"),
        _make("moonshot", "国内"),
        _make("deepseek", "国内", "推荐"),
        _make("mystery"),
    ]
    g = group_cloud_providers(catalog)
    keys = list(g.keys())
    expected_order = [k for k, _l, _i in _CLOUD_GROUP_ORDER if k in g]
    assert keys == expected_order


# ---------------------------------------------------------------------------
# 真实 catalog
# ---------------------------------------------------------------------------


def test_real_catalog_no_local_in_cloud_groups():
    """真实 PROVIDER_CATALOG: 任何'本地' tag 厂商都不应出现在云分组里。"""
    g = group_cloud_providers(PROVIDER_CATALOG)
    all_pids = {p.pid for v in g.values() for p in v}
    local_pids = {
        "ollama", "xinference", "vllm", "lm-studio", "gpustack",
        "llama-cpp", "localai", "tgi", "text-gen-webui",
        "koboldcpp", "jan", "gpt4all", "openllm",
        "oneapi", "new-api",
    }
    assert all_pids.isdisjoint(local_pids), (
        f"云分组漏出本地: {all_pids & local_pids}"
    )


def test_real_catalog_has_all_4_main_groups():
    """真实 catalog 应至少有 推荐/国内/国外/聚合 4 个分组(各非空)。"""
    g = group_cloud_providers(PROVIDER_CATALOG)
    for required in ("推荐", "国内", "国外", "聚合"):
        assert required in g and len(g[required]) > 0, (
            f"分组 {required!r} 在真实 catalog 中缺失或为空"
        )


def test_real_catalog_search_chinese_works():
    """真实 catalog 中文搜索 'DeepSeek' 命中。"""
    g = group_cloud_providers(PROVIDER_CATALOG, search="deepseek")
    pids = {p.pid for v in g.values() for p in v}
    assert "deepseek" in pids


# ---------------------------------------------------------------------------
# 64.2 题:云 grid 刷新 trigger
# ---------------------------------------------------------------------------


def test_trigger_cloud_grid_refresh_calls_registered():
    """trigger_cloud_grid_refresh 调用所有已注册的 fn。"""
    from chayuan.server.config_panel.model_config import (
        _CLOUD_GRID_REFRESHERS, trigger_cloud_grid_refresh,
    )
    snapshot = list(_CLOUD_GRID_REFRESHERS)
    _CLOUD_GRID_REFRESHERS.clear()
    try:
        called = {"n": 0}

        def _fake() -> None:
            called["n"] += 1

        _CLOUD_GRID_REFRESHERS.append(_fake)
        trigger_cloud_grid_refresh()
        assert called["n"] == 1
    finally:
        _CLOUD_GRID_REFRESHERS.clear()
        _CLOUD_GRID_REFRESHERS.extend(snapshot)


def test_trigger_cloud_grid_refresh_removes_failing_fn():
    """fn 抛异常 → 自动从 _CLOUD_GRID_REFRESHERS 移除。"""
    from chayuan.server.config_panel.model_config import (
        _CLOUD_GRID_REFRESHERS, trigger_cloud_grid_refresh,
    )
    snapshot = list(_CLOUD_GRID_REFRESHERS)
    _CLOUD_GRID_REFRESHERS.clear()
    try:
        def _boom() -> None:
            raise RuntimeError("simulated dead client")

        _CLOUD_GRID_REFRESHERS.append(_boom)
        trigger_cloud_grid_refresh()
        # fn 抛错后应被自动移除
        assert _boom not in _CLOUD_GRID_REFRESHERS
    finally:
        _CLOUD_GRID_REFRESHERS.clear()
        _CLOUD_GRID_REFRESHERS.extend(snapshot)
