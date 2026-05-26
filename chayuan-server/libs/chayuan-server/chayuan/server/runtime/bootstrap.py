"""启动期把 ``Settings.API_SERVER`` / ``CONFIG_SERVER`` / vendor 端口
统一过一遍 :class:`PortAllocator`，并把结果写进 ``runtime.json``。

调用时机：``chayuan.startup.main`` / ``chayuan service start`` 在 fork
worker 之前。返回的 :class:`BootstrapResult` 适合被 CLI 直接渲染成表格。

设计取舍
========

* **不**自动改 yaml。yaml 是用户意图，端口被占自动改写到 yaml 容易把
  next-restart 的"想用 5432"也改丢；改在 ``runtime.json`` 里更稳。
* 主进程的 API 端口是否要 auto-bump 由 ``Settings.PORT_PREFER_DEFAULT``
  决定；默认开。
* ``vendor`` 服务的端口仅在它"被声明要用"（出现在 ``Settings.VENDOR_AUTOSTART``
  或 ``service info`` 显式查询）时才进 allocator，避免无谓占用。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from chayuan.server.runtime.port_allocator import (
    DEFAULT_PORT_RANGE,
    PortAllocator,
    PortInUseError,
)
from chayuan.server.runtime.runtime_info import (
    RuntimeInfo,
    ServiceEndpoint,
    get_runtime_info,
)

logger = logging.getLogger("chayuan.runtime.bootstrap")


@dataclass
class BootstrapResult:
    """``allocate_core_ports`` 的返回。"""

    api: ServiceEndpoint
    config_panel: ServiceEndpoint
    vendor: List[ServiceEndpoint] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "api": dict(self.api.masked()),
            "config_panel": dict(self.config_panel.masked()),
            "vendor": [dict(e.masked()) for e in self.vendor],
            "warnings": list(self.warnings),
        }


def _make_allocator(*, settings=None) -> PortAllocator:
    """工厂：从 Settings 拿 PORT_RANGE，把已知 endpoint 先 reserve 进来。"""
    rng: Tuple[int, int] = DEFAULT_PORT_RANGE
    if settings is not None:
        try:
            rng = tuple(getattr(settings, "PORT_RANGE", DEFAULT_PORT_RANGE))  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            rng = DEFAULT_PORT_RANGE
    alloc = PortAllocator(port_range=rng)
    return alloc


def allocate_core_ports(*, runtime: Optional[RuntimeInfo] = None) -> BootstrapResult:
    """把 API + 配置面板端口过一遍 allocator，落到 ``runtime.json``。

    此函数**幂等**：重复调用不会改动 yaml，也不会重复 bump 已经稳定的端口；
    新发现的冲突才会触发 bump。
    """
    from chayuan.settings import Settings

    bs = Settings.basic_settings
    runtime = runtime or get_runtime_info()
    alloc = _make_allocator(settings=bs)

    warnings: List[str] = []

    # API 主服务 ----------------------------------------------------------
    api_pref = int((bs.API_SERVER or {}).get("port") or 62581)
    api_host = (bs.API_SERVER or {}).get("host") or bs.DEFAULT_BIND_HOST
    try:
        api_port = alloc.allocate(
            preferred=api_pref if bs.PORT_PREFER_DEFAULT else None,
            name="api",
        )
    except PortInUseError as e:
        warnings.append(f"API 端口分配失败：{e}")
        api_port = api_pref
    if api_port != api_pref:
        # 用 ``is_port_free`` 单独探一次确认是真"被占"还是"超出 range / 历史 named 命中其它端口"
        # 别再像旧版那样无脑写"被占"——曾导致 62581 / 8509 完全空闲时也提示用户。
        from chayuan.server.runtime.port_allocator import (
            DEFAULT_PORT_RANGE,
            is_port_free,
        )
        rng = tuple(getattr(bs, "PORT_RANGE", DEFAULT_PORT_RANGE))
        if is_port_free(api_pref, host=api_host or "127.0.0.1"):
            if not (rng[0] <= api_pref <= rng[1]):
                warnings.append(
                    f"API 偏好端口 {api_pref} 不在配置的端口范围 "
                    f"{rng[0]}-{rng[1]} 内，已改用 {api_port}（写入 runtime.json）；"
                    f"如需保留 {api_pref}，请把 PORT_RANGE 调成包含它的区间。"
                )
            else:
                # 范围内 + 空闲但仍被换掉 = 历史 named 命中其它端口，告诉用户"复用而非冲突"
                warnings.append(
                    f"API 端口复用历史值 {api_port}（{api_pref} 也空闲，未启用）"
                )
        else:
            warnings.append(
                f"API 偏好端口 {api_pref} 被占，已自动改用 {api_port}（写入 runtime.json）"
            )
    api_ep = runtime.set_endpoint(
        "api",
        host=api_host, port=api_port,
        scheme="http",
        url=f"http://{api_host or '127.0.0.1'}:{api_port}",
        kind="api",
    )

    # 配置面板 -----------------------------------------------------------
    cp_pref = int((bs.CONFIG_SERVER or {}).get("port") or 8502)
    cp_host = (bs.CONFIG_SERVER or {}).get("host") or bs.DEFAULT_BIND_HOST
    try:
        cp_port = alloc.allocate(
            preferred=cp_pref if bs.PORT_PREFER_DEFAULT else None,
            name="config_panel",
        )
    except PortInUseError as e:
        warnings.append(f"配置面板端口分配失败：{e}")
        cp_port = cp_pref
    if cp_port != cp_pref:
        from chayuan.server.runtime.port_allocator import (
            DEFAULT_PORT_RANGE,
            is_port_free,
        )
        rng = tuple(getattr(bs, "PORT_RANGE", DEFAULT_PORT_RANGE))
        if is_port_free(cp_pref, host=cp_host or "127.0.0.1"):
            if not (rng[0] <= cp_pref <= rng[1]):
                warnings.append(
                    f"配置面板偏好端口 {cp_pref} 不在配置的端口范围 "
                    f"{rng[0]}-{rng[1]} 内，已改用 {cp_port}；"
                    f"如需保留 {cp_pref}，请把 PORT_RANGE 调成包含它的区间。"
                )
            else:
                warnings.append(
                    f"配置面板端口复用历史值 {cp_port}（{cp_pref} 也空闲，未启用）"
                )
        else:
            warnings.append(
                f"配置面板偏好端口 {cp_pref} 被占，已自动改用 {cp_port}"
            )
    cp_ep = runtime.set_endpoint(
        "config_panel",
        host=cp_host, port=cp_port,
        scheme="http",
        url=f"http://{cp_host or '127.0.0.1'}:{cp_port}",
        kind="config_panel",
    )

    return BootstrapResult(api=api_ep, config_panel=cp_ep, warnings=warnings)


def render_endpoints_table(result: BootstrapResult) -> str:
    """把 :class:`BootstrapResult` 渲染成一个朴素 ASCII 表，CLI 直接打印。"""
    rows: List[Tuple[str, str, str, str]] = [
        ("服务", "地址", "用户", "密码"),
        ("─" * 14, "─" * 32, "─" * 18, "─" * 12),
    ]
    for ep in [result.api, result.config_panel, *result.vendor]:
        m = ep.masked()
        rows.append((
            str(m.get("name") or m.get("kind") or ""),
            str(m.get("url") or f"{m.get('host')}:{m.get('port')}"),
            str(m.get("user") or "-"),
            str(m.get("password") or "-"),
        ))
    cols = list(zip(*rows))
    widths = [max(len(str(c)) for c in col) for col in cols]
    out_lines: List[str] = []
    for r in rows:
        out_lines.append("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    if result.warnings:
        out_lines.append("")
        out_lines.append("⚠ 提示：")
        for w in result.warnings:
            out_lines.append(f"  - {w}")
    return "\n".join(out_lines)


__all__ = [
    "BootstrapResult",
    "allocate_core_ports",
    "render_endpoints_table",
]
