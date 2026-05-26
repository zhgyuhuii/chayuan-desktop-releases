"""PortAllocator 单元测试。

不开真端口；通过注入 ``bind_check`` 模拟"哪些端口被占"。
"""
from __future__ import annotations

import pytest

from chayuan.server.runtime.port_allocator import (
    DEFAULT_PORT_RANGE,
    PortAllocator,
    PortInUseError,
)


def make_allocator(taken: set, range_: tuple = (40000, 40010)) -> PortAllocator:
    """每个端口除 taken 内的视为空闲。"""
    return PortAllocator(
        port_range=range_,
        bind_check=lambda p: p not in taken,
    )


def test_allocate_returns_preferred_when_free():
    alloc = make_allocator(taken=set())
    assert alloc.allocate(preferred=40005, name="api") == 40005


def test_allocate_bumps_when_preferred_occupied():
    alloc = make_allocator(taken={40005, 40006})
    port = alloc.allocate(preferred=40005, name="x")
    assert port == 40007  # 跳过被占的两个


def test_allocate_wraps_around_low_when_high_full():
    alloc = make_allocator(taken={40005, 40006, 40007, 40008, 40009, 40010}, range_=(40000, 40010))
    port = alloc.allocate(preferred=40005, name="x")
    # preferred=40005 后面全占；从 low 端再扫一遍，第一个空闲是 40000
    assert port == 40000


def test_allocate_raises_when_full():
    rng = (40000, 40003)
    alloc = make_allocator(taken={40000, 40001, 40002, 40003}, range_=rng)
    with pytest.raises(PortInUseError):
        alloc.allocate(preferred=40000, name="x")


def test_named_reuses_previous_allocation():
    alloc = make_allocator(taken=set())
    p1 = alloc.allocate(preferred=40001, name="postgres")
    p2 = alloc.allocate(preferred=40009, name="postgres")  # 不同 preferred 也要复用
    assert p1 == p2 == 40001


def test_named_reallocates_if_old_now_taken():
    """如果 name 历史端口现在被外部抢占，allocator 应改派一个新端口。"""
    alloc = make_allocator(taken=set())
    p1 = alloc.allocate(preferred=40001, name="redis")
    assert p1 == 40001
    # 模拟外部进程抢占了 40001
    alloc._bind_check = lambda p: p not in {40001}
    p2 = alloc.allocate(preferred=40001, name="redis")
    assert p2 != 40001
    assert p2 in range(40000, 40011)


def test_skip_set_excluded():
    alloc = make_allocator(taken=set())
    port = alloc.allocate(preferred=40000, name="api", skip={40000, 40001})
    assert port == 40002


def test_reserve_blocks_subsequent_allocations():
    alloc = make_allocator(taken=set())
    alloc.reserve(40005, name="manual")
    p = alloc.allocate(preferred=40005, name="other")
    assert p != 40005


def test_default_range_in_high_band():
    """安全检查：默认范围是高位，避免 < 32768 的常见服务端口段。"""
    lo, hi = DEFAULT_PORT_RANGE
    assert lo >= 32768
    assert hi <= 65535


# --- 回归测试：preferred 在 range 之外不应误报"被占" -----------------------
#
# 用户 2026-05-02 报告：
#
#   服务刚启动报：
#     ⚠ API 偏好端口 62581 被占，已自动改用 40000（写入 runtime.json）
#     ⚠ 配置面板偏好端口 8509 被占，已自动改用 40001
#
#   但 ``ss -lntp`` 看上去 62581 / 8509 完全空闲。
#
# 根因：旧版 ``PortAllocator.allocate`` 第 165 行把 ``preferred`` 加入候选时
# 检查了 ``self._lo <= p <= self._hi``。默认 range=(40000, 60999)；当
# ``preferred=62581`` 时这个判断为 False，preferred 被**直接跳过**——
# 然后 fallback 走 40000 + 顺序探，得 40000 / 40001。日志却写"已被占用"，
# 完全不准确。
#
# 修复后：preferred 即使在 range 外也单独 bind 探一次。空闲就直接返回它。
def test_allocate_preferred_outside_range_is_still_returned_when_free():
    """preferred=62581 + range=(40000, 60999)：62581 空闲就该用它，不能 bump 到 40000。"""
    alloc = PortAllocator(
        port_range=(40000, 60999),
        bind_check=lambda _p: True,  # 全部空闲
    )
    port = alloc.allocate(preferred=62581, name="api")
    assert port == 62581, (
        "preferred=62581 在默认 range (40000-60999) 之外，但完全空闲；"
        "应该返回 62581 本身，而不是 fallback 到 40000。"
    )


def test_allocate_preferred_outside_range_falls_back_when_busy():
    """preferred 在 range 外 + 真被占：仍然应当 fallback 到 range 内。"""
    busy = {62581}
    alloc = PortAllocator(
        port_range=(40000, 40010),
        bind_check=lambda p: p not in busy,
    )
    port = alloc.allocate(preferred=62581, name="api")
    assert 40000 <= port <= 40010, (
        "preferred=62581 真被占时，应该回到配置的 [40000, 40010] 范围里找。"
    )


def test_allocate_preferred_outside_range_logs_helpful_warning(caplog):
    """preferred 在 range 外 + 实际可用，但 fallback（例如 named 历史命中）时，
    日志应明示是"超出 range"而不是误报"被占用"。"""
    import logging

    alloc = PortAllocator(
        port_range=(40000, 40010),
        bind_check=lambda _p: True,
    )
    # 先把 ``api`` 命名到 40005（来自 runtime.json 的历史值），让下一次 allocate
    # 走"name 复用"分支——绕过 preferred 直接返回 40005。
    alloc.reserve(40005, name="api")

    with caplog.at_level(logging.INFO, logger="chayuan.runtime.port_allocator"):
        port = alloc.allocate(preferred=62581, name="api")
    assert port == 40005
    # 这条 case 不该出现"已被占用"字样（preferred 根本没探过）
    msgs = [rec.getMessage() for rec in caplog.records]
    assert not any("已被占用" in m for m in msgs), msgs


def test_allocate_preferred_in_range_busy_logs_busy_warning(caplog):
    """preferred 在 range 内但真被占：日志应明示"被占用"。"""
    import logging

    busy = {40005}
    alloc = PortAllocator(
        port_range=(40000, 40010),
        bind_check=lambda p: p not in busy,
    )
    with caplog.at_level(logging.INFO, logger="chayuan.runtime.port_allocator"):
        port = alloc.allocate(preferred=40005, name="api")
    assert port != 40005
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("已被占用" in m for m in msgs), msgs
