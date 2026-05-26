"""Config Panel · API 可达性门闸（API Gate）。

**用途**：Config Panel 的某些页面（知识库 / 数据源 / 文件存储 / 数据治理）要
通过 62581 API Server 才能实际落地写。如果 API 没启动，这些页面应当**整体灰显**、
按钮禁用，而不是让用户点下去出现一堆连接错误。

设计：
- ``probe_api_status(base_url)``：发 ``GET /healthz``，返回 ``(ok, detail)``。
- ``api_base_url()``：读 ``Settings.basic_settings.API_SERVER`` 拼 URL。
- ``render_api_gate(ui, probe_fn=None)``：在当前 NiceGUI 容器内顶部渲染一条
  banner + 返回 ``(ok, user_token)``；若不 OK，调用方可直接 ``return`` 结束页面。
- ``with_api_gate(ui)``：装饰器版本，同等效果但更简洁。

HTTP 客户端用 ``httpx`` 同步版（NiceGUI handler 本身是同步的，aioresponse 也能用
``run.io_bound`` 但不必）；超时 2s，足够判断服务是否在听端口。

**缓存**：探活调用 1s 内频繁命中会消耗端口资源；我们在内存做 1s TTL 的最小缓存。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("chayuan.config_panel.api_gate")


@dataclass
class ApiStatus:
    ok: bool
    detail: str = ""
    url: str = ""
    latency_ms: float = 0.0


# 1 秒 TTL 足以覆盖一次页面刷新内的多处探测，同时失联后也能很快感知
_CACHE: dict = {"ts": 0.0, "status": None}
_CACHE_TTL_SEC = 1.0


def api_base_url() -> str:
    """拼 ``http://<API_SERVER.host>:<API_SERVER.port>``，默认 ``127.0.0.1:62581``。"""
    try:
        from chayuan.settings import Settings
        srv = getattr(Settings.basic_settings, "API_SERVER", {}) or {}
        host = str(srv.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        port = int(srv.get("port") or 62581)
        return f"http://{host}:{port}"
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:62581"


def probe_api_status(base_url: Optional[str] = None,
                       timeout_sec: float = 2.0,
                       use_cache: bool = True) -> ApiStatus:
    """``GET /healthz`` 探活；失败返回 ``ok=False`` 和错因。

    - ``base_url``：默认取自 ``Settings.basic_settings.API_SERVER``
    - ``timeout_sec``：很短；API 没启动时走 TCP ECONNREFUSED，~10ms 就返回
    - ``use_cache``：1 秒内重复调用走缓存，避免 UI 重绘时刷爆端口
    """
    url = (base_url or api_base_url()).rstrip("/")
    if use_cache:
        now = time.time()
        cached = _CACHE.get("status")
        if cached and (now - _CACHE["ts"] < _CACHE_TTL_SEC) and cached.url == url:
            return cached

    t0 = time.time()
    try:
        import httpx
        with httpx.Client(timeout=timeout_sec, trust_env=False) as c:
            r = c.get(f"{url}/healthz")
            latency = (time.time() - t0) * 1000
            if r.status_code == 200:
                status = ApiStatus(ok=True, detail="OK", url=url, latency_ms=latency)
            else:
                status = ApiStatus(
                    ok=False, detail=f"HTTP {r.status_code}",
                    url=url, latency_ms=latency,
                )
    except Exception as e:  # noqa: BLE001
        latency = (time.time() - t0) * 1000
        status = ApiStatus(
            ok=False,
            detail=f"{type(e).__name__}: {e}".split("\n", 1)[0][:180],
            url=url, latency_ms=latency,
        )

    _CACHE["ts"] = time.time()
    _CACHE["status"] = status
    return status


def invalidate_cache() -> None:
    """强制清缓存；用户点"重试"时调用。"""
    _CACHE["ts"] = 0.0
    _CACHE["status"] = None


def render_api_gate(ui, *, title: str = "API 服务", compact: bool = False) -> ApiStatus:
    """在当前 NiceGUI 容器内渲染"API 状态"banner 并返回状态。

    调用方约定：若返回 ``ok=False``，应当立刻 ``return`` 或跳过 IO 操作。

    :param ui: ``from nicegui import ui`` 的 ui
    :param title: banner 显示的页面名称（如「知识库管理」）
    :param compact: True 时只画一行细条；否则画一个高亮卡片
    """
    status = probe_api_status()

    def _retry():
        invalidate_cache()
        ui.navigate.reload()

    if status.ok:
        if not compact:
            with ui.row().classes("w-full items-center gap-2 q-pa-sm bg-green-1 rounded-borders"):
                ui.icon("check_circle").classes("text-green-700")
                ui.label(f"{title}：已连接 API ({status.url}, {status.latency_ms:.0f}ms)").classes("text-sm")
        return status

    # 不可达：显著的红色 banner + 引导
    with ui.column().classes(
        "w-full q-pa-md rounded-borders bg-red-1 border border-red-300 gap-2"
    ):
        with ui.row().classes("items-center gap-2"):
            ui.icon("error").classes("text-red-700 text-2xl")
            ui.label(f"{title} · API 服务未就绪").classes("text-lg font-bold text-red-800")
        ui.label(
            f"无法连接 {status.url}：{status.detail}"
        ).classes("text-sm text-red-800")
        ui.label(
            "以下功能在 API 服务启动前将被禁用。请在服务器上执行："
        ).classes("text-sm text-gray-700")
        ui.code("chayuan start -a", language="bash")
        ui.label(
            "或在 Windows PowerShell 下："
        ).classes("text-sm text-gray-700")
        ui.code(
            "python -m chayuan.startup --api",
            language="bash",
        )
        with ui.row().classes("items-center gap-2"):
            ui.button("🔄 重试", on_click=_retry).props("outline color=red")
            ui.button(
                "去「基础配置」查看", on_click=lambda: ui.navigate.to("/dashboard?nav=root"),
            ).props("flat color=red")
    return status
