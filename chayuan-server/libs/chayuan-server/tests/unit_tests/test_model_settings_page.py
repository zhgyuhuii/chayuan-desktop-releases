"""模型设置 v2 入口 — initial_tab 归一化与 fragment 解析(62 题)。

只测纯函数,不依赖 NiceGUI。
"""
from __future__ import annotations

import pytest

from chayuan.server.config_panel.model_settings.page import (
    TAB_DEFAULTS,
    TAB_MARKETPLACE,
    TAB_PROVIDERS,
    TAB_RUNTIME,
    normalize_initial_tab,
)


# ---------------------------------------------------------------------------
# normalize_initial_tab
# ---------------------------------------------------------------------------


# 106 题:① 运行时与服务 tab 已删除,3 tabs(providers / marketplace / defaults)
# fallback 默认从 RUNTIME 改为 PROVIDERS。"runtime" 字符串视为非法,归一化到 PROVIDERS。

@pytest.mark.parametrize("v", [
    TAB_PROVIDERS, TAB_MARKETPLACE, TAB_DEFAULTS,
])
def test_normalize_keeps_valid(v):
    assert normalize_initial_tab(v) == v


def test_normalize_old_runtime_falls_back_to_providers():
    """老 fragment 'runtime'(106 题前的合法值)→ 现在归一化到 PROVIDERS。"""
    assert normalize_initial_tab("runtime") == TAB_PROVIDERS


@pytest.mark.parametrize("v", [
    None, "", "unknown", "RUNTIME", "Defaults", "tab1",
    123, [], {},
])
def test_normalize_falls_back_to_providers(v):
    """非法/空/类型不对 → fallback 到 PROVIDERS,绝不抛。"""
    assert normalize_initial_tab(v) == TAB_PROVIDERS


def test_tab_constants_are_unique():
    tabs = {TAB_PROVIDERS, TAB_MARKETPLACE, TAB_DEFAULTS}
    assert len(tabs) == 3


# ---------------------------------------------------------------------------
# 入口 file:<name>#<tab> fragment 解析(由 dashboard 拆分,这里测 round-trip)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("input_key, expected_filename, expected_tab", [
    ("file:model_settings.yaml#defaults", "model_settings.yaml", "defaults"),
    ("file:model_settings.yaml#runtime", "model_settings.yaml", "runtime"),
    ("file:model_settings.yaml#providers", "model_settings.yaml", "providers"),
    ("file:model_settings.yaml#marketplace", "model_settings.yaml", "marketplace"),
    ("file:model_settings.yaml", "model_settings.yaml", None),
    ("file:kb_settings.yaml", "kb_settings.yaml", None),
])
def test_fragment_split_logic(input_key, expected_filename, expected_tab):
    """模拟 dashboard._render_page 解析逻辑(file:xxx#tab → filename + tab)。"""
    assert input_key.startswith("file:")
    body = input_key[len("file:"):]
    initial_tab = None
    if "#" in body:
        body, initial_tab = body.split("#", 1)
    assert body == expected_filename
    assert initial_tab == expected_tab


def test_fragment_split_then_normalize():
    """完整链路:file:xxx#defaults → 拆分 → normalize_initial_tab → defaults。"""
    key = "file:model_settings.yaml#defaults"
    body = key[len("file:"):]
    filename, raw_tab = body.split("#", 1)
    assert filename == "model_settings.yaml"
    assert normalize_initial_tab(raw_tab) == TAB_DEFAULTS


def test_invalid_tab_in_fragment_falls_back():
    """fragment 是非法 tab id → 落回 PROVIDERS(106 题:从 RUNTIME 改),不报错。"""
    key = "file:model_settings.yaml#bogus"
    body = key[len("file:"):]
    filename, raw_tab = body.split("#", 1)
    assert filename == "model_settings.yaml"
    assert normalize_initial_tab(raw_tab) == TAB_PROVIDERS


# ---------------------------------------------------------------------------
# service_config_page jump_targets 契约(62 题:llm/embed 跳 #defaults)
# ---------------------------------------------------------------------------


def test_service_config_jump_targets_for_llm_embed_have_defaults_tab():
    """``service_config_page._render_topology`` 中 llm/embed 应该跳 #defaults,
    确保 62 题的"默认 LLM"/"默认 Embedding"按钮跳到 ④ 默认模型 tab。"""
    import inspect
    from chayuan.server.config_panel import service_config_page
    src = inspect.getsource(service_config_page._render_topology)
    assert '"llm": "file:model_settings.yaml#defaults"' in src
    assert '"embed": "file:model_settings.yaml#defaults"' in src


# ---------------------------------------------------------------------------
# 66 题:mark_tab_dirty + invalidator 生命周期
# ---------------------------------------------------------------------------


def test_mark_tab_dirty_calls_registered_invalidators():
    """注册的 invalidator 收到正确的 tab_id。"""
    from chayuan.server.config_panel.model_settings import page as page_mod
    snapshot = list(page_mod._TAB_INVALIDATORS)
    page_mod._TAB_INVALIDATORS.clear()
    try:
        seen: list[str] = []

        def _inv(tid: str) -> None:
            seen.append(tid)

        page_mod._TAB_INVALIDATORS.append(_inv)
        page_mod.mark_tab_dirty(page_mod.TAB_DEFAULTS)
        page_mod.mark_tab_dirty(page_mod.TAB_MARKETPLACE)
        assert seen == [page_mod.TAB_DEFAULTS, page_mod.TAB_MARKETPLACE]
    finally:
        page_mod._TAB_INVALIDATORS.clear()
        page_mod._TAB_INVALIDATORS.extend(snapshot)


def test_mark_tab_dirty_unknown_tab_id_silently_ignored():
    """未知 tab_id → silent return,不抛,不调 invalidator。"""
    from chayuan.server.config_panel.model_settings import page as page_mod
    snapshot = list(page_mod._TAB_INVALIDATORS)
    page_mod._TAB_INVALIDATORS.clear()
    try:
        called = {"n": 0}

        def _inv(tid: str) -> None:
            called["n"] += 1

        page_mod._TAB_INVALIDATORS.append(_inv)
        page_mod.mark_tab_dirty("bogus_tab")
        assert called["n"] == 0
    finally:
        page_mod._TAB_INVALIDATORS.clear()
        page_mod._TAB_INVALIDATORS.extend(snapshot)


def test_mark_tab_dirty_failing_invalidator_auto_unregistered():
    """invalidator 抛异常(client 已死)→ 自动从 _TAB_INVALIDATORS 移除。"""
    from chayuan.server.config_panel.model_settings import page as page_mod
    snapshot = list(page_mod._TAB_INVALIDATORS)
    page_mod._TAB_INVALIDATORS.clear()
    try:
        def _boom(tid: str) -> None:
            raise RuntimeError("simulated dead client")

        page_mod._TAB_INVALIDATORS.append(_boom)
        page_mod.mark_tab_dirty(page_mod.TAB_DEFAULTS)
        # 失败的 fn 应已自动 unregister
        assert _boom not in page_mod._TAB_INVALIDATORS
    finally:
        page_mod._TAB_INVALIDATORS.clear()
        page_mod._TAB_INVALIDATORS.extend(snapshot)


def test_mark_tab_dirty_multiple_invalidators_isolated():
    """同时注册多个 invalidator(模拟多 client),失败的不影响其他。"""
    from chayuan.server.config_panel.model_settings import page as page_mod
    snapshot = list(page_mod._TAB_INVALIDATORS)
    page_mod._TAB_INVALIDATORS.clear()
    try:
        good_calls = []

        def _bad(tid: str) -> None:
            raise RuntimeError("dead")

        def _good(tid: str) -> None:
            good_calls.append(tid)

        page_mod._TAB_INVALIDATORS.append(_bad)
        page_mod._TAB_INVALIDATORS.append(_good)
        page_mod.mark_tab_dirty(page_mod.TAB_DEFAULTS)
        # _good 仍被调,_bad 被踢
        assert good_calls == [page_mod.TAB_DEFAULTS]
        assert _bad not in page_mod._TAB_INVALIDATORS
        assert _good in page_mod._TAB_INVALIDATORS
    finally:
        page_mod._TAB_INVALIDATORS.clear()
        page_mod._TAB_INVALIDATORS.extend(snapshot)


def test_providers_subpage_save_callback_invalidates_grouped_and_marks_defaults():
    """providers_subpage._wrap_invalidate_after_save 返回的 callback 应:
       1) state_cache.invalidate("states","grouped","defaults")
       2) mark_tab_dirty("defaults") + mark_tab_dirty("marketplace")
    """
    from chayuan.server.config_panel.model_settings import (
        page as page_mod,
        state_cache,
    )
    from chayuan.server.config_panel.model_settings.providers_subpage import (
        _wrap_invalidate_after_save,
    )

    # 准备 spy
    inv_calls: list[tuple] = []
    orig_invalidate = state_cache.invalidate
    state_cache.invalidate = lambda *keys: inv_calls.append(tuple(keys))

    seen_tabs: list[str] = []
    snapshot = list(page_mod._TAB_INVALIDATORS)
    page_mod._TAB_INVALIDATORS.clear()
    page_mod._TAB_INVALIDATORS.append(lambda tid: seen_tabs.append(tid))

    try:
        cb = _wrap_invalidate_after_save(None)
        assert cb is not None
        cb()
        # 验证 invalidate 调用
        assert any({"states", "grouped", "defaults"} <= set(args) for args in inv_calls), (
            f"应至少有一次 invalidate 包含 states/grouped/defaults,实际: {inv_calls}"
        )
        # 验证 mark_tab_dirty 触发了 ④ defaults 和 ③ marketplace
        assert page_mod.TAB_DEFAULTS in seen_tabs
        assert page_mod.TAB_MARKETPLACE in seen_tabs
    finally:
        state_cache.invalidate = orig_invalidate
        page_mod._TAB_INVALIDATORS.clear()
        page_mod._TAB_INVALIDATORS.extend(snapshot)


def test_providers_subpage_save_callback_chains_user_cb():
    """传入 user_cb 时,callback 应先调 user_cb 再 invalidate(finally 兜底)。"""
    from chayuan.server.config_panel.model_settings import (
        page as page_mod,
        state_cache,
    )
    from chayuan.server.config_panel.model_settings.providers_subpage import (
        _wrap_invalidate_after_save,
    )

    sequence: list[str] = []

    def _user_cb() -> None:
        sequence.append("user")

    orig_invalidate = state_cache.invalidate
    state_cache.invalidate = lambda *keys: sequence.append("invalidate")

    snapshot = list(page_mod._TAB_INVALIDATORS)
    page_mod._TAB_INVALIDATORS.clear()
    page_mod._TAB_INVALIDATORS.append(lambda tid: sequence.append(f"dirty:{tid}"))

    try:
        cb = _wrap_invalidate_after_save(_user_cb)
        cb()
        # user_cb 先,然后 invalidate,然后 mark_tab_dirty(2 次)
        assert sequence[0] == "user"
        assert sequence.count("invalidate") == 1
        assert sequence.count(f"dirty:{page_mod.TAB_DEFAULTS}") == 1
        assert sequence.count(f"dirty:{page_mod.TAB_MARKETPLACE}") == 1
    finally:
        state_cache.invalidate = orig_invalidate
        page_mod._TAB_INVALIDATORS.clear()
        page_mod._TAB_INVALIDATORS.extend(snapshot)
