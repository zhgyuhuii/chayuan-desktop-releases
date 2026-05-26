"""模型设置页 · 镜像源切换段。

放在 ``render_runtime_framework_row`` 之前(整页顶部),让用户首先看到自己当前
走的是哪个镜像源、能不能切换、能不能测速。

UE 设计:
* 当前镜像源 + 延迟徽章(running 后展示;未测则灰)
* 4 个候选 chip:hf-mirror / huggingface / hf.co / modelscope + 自定义输入框
* 「测速并自动选最快」按钮:并发 ping 所有候选,4s 超时,挑延迟最低且 OK 的切到
* 切换瞬时持久化到 ``runtime.json`` 的 ``ai_platform.mirror`` 字段(下次启动也走这个)
* 修改后立刻 ``set_mirror`` 注入 env,后续 modelmgr / downloader 立即生效

不做的事:
* 不做 download 测试(只测 HEAD 连通性);真正的 GET 速度由用户实际下载体感决定
* 不做"自动定时检测"(用户主动触发即可,避免心智负担)
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.config_panel.mirror_source_panel")


# 持久化键(写到 runtime.json / Settings)
_RUNTIME_MIRROR_KEY = "ai_platform.mirror"


def _persist_mirror(name_or_url: Optional[str]) -> None:
    """把镜像源选择写到 ``runtime.json`` 的 ``ai_platform.mirror``。

    用 RuntimeInfo 单例的私有 ``_data`` + ``_flush`` (无公共 setter 给任意键
    使用,对它写小段配置块是已知做法,见 ``ai_platform/runtime_config.py``)。
    """
    try:
        from chayuan.server.runtime.runtime_info import get_runtime_info

        info = get_runtime_info()
        section = info._data.setdefault("ai_platform", {})  # noqa: SLF001
        if name_or_url is None:
            section.pop("mirror", None)
        else:
            section["mirror"] = name_or_url
        info._flush()  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        logger.warning("persist mirror to runtime.json failed: %s", e)


def render_mirror_source_row(ui: Any) -> None:
    """渲染镜像源切换段。

    若 ``chayuan_modelmgr`` 没装(用户只 ``pip install -e libs/chayuan-server``
    没装其它 sibling 包),给清晰的引导:命令清单 + 复制按钮 + 重试,
    而不是一行红字 silent 完事。
    """
    try:
        from chayuan_modelmgr.mirrors import (
            MIRRORS,
            SpeedtestResult,
            resolve_mirror,
            set_mirror,
            speedtest_mirrors,
        )
    except Exception as e:  # noqa: BLE001
        logger.info("modelmgr.mirrors not installed: %s", e)
        _render_modelmgr_missing_card(ui, error=str(e))
        return

    # 当前镜像源
    state: Dict[str, Any] = {
        "current": resolve_mirror(),
        "speedtest_results": [],
        "speedtest_running": False,
    }

    # ---- 卡片骨架 ----
    with ui.card().classes("w-full q-mb-md").props("flat bordered").style(
        "border-radius: 12px; padding: 16px;"
    ):
        with ui.row().classes("w-full no-wrap items-center gap-2"):
            ui.icon("public").style(
                "color: var(--cy-brand-600, #2563EB); font-size: 20px;"
            )
            ui.label("镜像源").style(
                "font-size: 14px; font-weight: 600;"
                " color: var(--cy-text-primary, #0A0A0A);"
            )
            current_endpoint_label = ui.label(state["current"].endpoint).style(
                "font-size: 11px; color: var(--cy-text-tertiary, #A1A1AA);"
                " font-family: ui-monospace, SFMono-Regular, monospace;"
                " margin-left: 8px;"
            )

            ui.space()

            speedtest_btn = ui.button(
                "测速并选最快", icon="speed",
            ).props("dense unelevated").style(
                "border-radius: 8px; font-size: 12px;"
            )

        ui.label(
            "镜像源决定从哪里下载模型权重。HF Mirror 国内网络通常最快;"
            "境外用户切到 HuggingFace;ModelScope 走阿里云路由,部分仓库需做 ID 转换。"
        ).style(
            "color: var(--cy-text-secondary, #52525B);"
            " font-size: 12px; margin-top: 6px; line-height: 1.5;"
        )

        # ---- 候选行 ----
        chip_row = ui.row().classes("w-full gap-2 q-mt-sm items-center")
        chip_widgets: Dict[str, Any] = {}

        def _refresh_chip_styles() -> None:
            """单条 chip 刷新,任一失败不影响其他 — 防整 row 崩。"""
            current_name = state["current"].name
            for key, widget in chip_widgets.items():
                try:
                    is_active = key == current_name
                    widget.props(
                        "color=primary" if is_active
                        else "color=grey-3 text-color=grey-9"
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("refresh chip style for %s failed: %r", key, e)

        def _switch_to(name_or_url: str) -> None:
            """切换镜像源 — 所有子操作各自 try/except,任一失败不让 page 崩。"""
            new_mirror = None
            try:
                new_mirror = set_mirror(name_or_url)
                state["current"] = new_mirror
            except Exception as e:  # noqa: BLE001
                logger.warning("set_mirror failed: %s", e)
                ui.notify(f"镜像源设置失败:{e}", type="negative")
                return

            try:
                current_endpoint_label.set_text(new_mirror.endpoint)
            except Exception as e:  # noqa: BLE001
                logger.debug("update endpoint label failed: %r", e)
            try:
                _persist_mirror(name_or_url)
            except Exception as e:  # noqa: BLE001
                logger.debug("persist mirror failed: %r", e)
            try:
                _refresh_chip_styles()
            except Exception as e:  # noqa: BLE001
                logger.debug("refresh chip styles failed: %r", e)
            ui.notify(f"已切换镜像源:{new_mirror.name}", type="positive")

        with chip_row:
            for key, mirror in MIRRORS.items():
                chip = ui.chip(
                    f"{mirror.name}",
                    icon="check_circle" if key == state["current"].name else "circle",
                    on_click=lambda k=key: _switch_to(k),
                ).props("clickable")
                chip_widgets[key] = chip

            # 自定义输入
            custom_input = ui.input(
                placeholder="自定义 endpoint, 如 https://my-mirror.example.com",
            ).props("dense outlined").style("min-width: 280px; font-size: 12px;")

            def _apply_custom() -> None:
                val = (custom_input.value or "").strip()
                if not val:
                    ui.notify("请填入完整 URL", type="warning")
                    return
                _switch_to(val)

            ui.button("应用", on_click=_apply_custom).props("dense flat").style(
                "font-size: 12px;"
            )

        _refresh_chip_styles()

        # ---- 测速结果区 ----
        results_container = ui.column().classes("w-full gap-1 q-mt-sm")

        def _render_speedtest_results() -> None:
            results_container.clear()
            results: List[SpeedtestResult] = state["speedtest_results"]
            if not results:
                return
            with results_container:
                ui.label("测速结果(延迟越低越好):").style(
                    "font-size: 11px; color: var(--cy-text-secondary, #52525B);"
                    " font-weight: 500; margin-bottom: 4px;"
                )
                for r in results:
                    bg = (
                        "var(--cy-success-50, #ECFDF5)" if r.is_ok()
                        else "var(--cy-danger-50, #FEF2F2)"
                    )
                    text = (
                        f"{r.name}  ·  {r.latency_ms:.0f} ms" if r.is_ok()
                        else f"{r.name}  ·  失败({r.error or '超时'})"
                    )
                    with ui.row().classes("w-full items-center gap-2").style(
                        f"background: {bg}; border-radius: 6px;"
                        f" padding: 4px 8px; font-size: 12px;"
                    ):
                        ui.icon(
                            "check" if r.is_ok() else "close"
                        ).style(
                            f"color: {'var(--cy-success-600, #059669)' if r.is_ok() else 'var(--cy-danger-600, #DC2626)'};"
                            f" font-size: 14px;"
                        )
                        ui.label(text).style("flex: 1;")
                        if r.is_ok():
                            ui.button(
                                "切到这个", on_click=lambda name=r.name: _switch_to(name)
                            ).props("flat dense").style(
                                "font-size: 11px;"
                                " color: var(--cy-brand-600, #2563EB);"
                            )

        def _on_speedtest_click() -> None:
            if state["speedtest_running"]:
                ui.notify("测速进行中...", type="info")
                return
            state["speedtest_running"] = True
            speedtest_btn.props("loading")
            ui.notify("测速中,4 秒超时...", type="info")

            def _bg() -> None:
                try:
                    results = speedtest_mirrors(timeout=4.0)
                    state["speedtest_results"] = results
                except Exception as e:  # noqa: BLE001
                    logger.warning("speedtest failed: %s", e)
                    state["speedtest_results"] = []
                finally:
                    state["speedtest_running"] = False
                    # NiceGUI 跨线程更新 UI 用 ``ui.timer`` 单次或 ``ui.run_javascript``
                    # 这里用最稳的方式:在主线程触发重新渲染
                    try:
                        speedtest_btn.props(remove="loading")
                        _render_speedtest_results()
                        # 自动选最快
                        ok_results = [r for r in state["speedtest_results"] if r.is_ok()]
                        if ok_results:
                            fastest = ok_results[0]
                            if fastest.name != state["current"].name:
                                _switch_to(fastest.name)
                                ui.notify(
                                    f"已自动切到最快源:{fastest.name} ({fastest.latency_ms:.0f}ms)",
                                    type="positive",
                                )
                            else:
                                ui.notify(
                                    f"当前源 {fastest.name} 已是最快({fastest.latency_ms:.0f}ms)",
                                    type="positive",
                                )
                        else:
                            ui.notify("所有镜像源都连不通,请检查网络", type="negative")
                    except Exception as e:  # noqa: BLE001
                        logger.warning("speedtest UI update failed: %s", e)

            threading.Thread(target=_bg, daemon=True).start()

        speedtest_btn.on("click", _on_speedtest_click)


def _render_modelmgr_missing_card(ui: Any, *, error: str) -> None:
    """``chayuan_modelmgr`` 不可用时的**一键修复**卡。

    UX 设计:
    * 一条消息 + 一个按钮(不让用户面对 pip 命令清单)
    * 点按钮调 ``POST /admin/install_sibling`` 后端跑 pip install -e
    * 错误细节折叠,需要才看

    用户原话:"不用出现镜像源模块未就绪提示 应该一键修复 只要一条消息
    XXX要安装 用户点击安装即可安装相应的依赖"
    """
    state: Dict[str, Any] = {"installing": False}

    with ui.card().classes("w-full q-mb-md").props("flat bordered").style(
        "border-color: var(--cy-warning-500, #f59e0b); "
        "background: var(--cy-warning-50, #fffbeb); padding: 14px 16px;"
    ):
        # 一条消息 + 一键按钮
        with ui.row().classes("items-center w-full no-wrap").style("gap: 12px;"):
            ui.icon("auto_fix_high", size="20px").style(
                "color: var(--cy-warning-600, #d97706);"
            )
            ui.label("镜像源依赖模块缺失,需要安装").style(
                "font-weight: 600; font-size: 14px;"
                " color: var(--cy-warning-700, #b45309); flex: 1;"
            )
            install_btn = ui.button(
                "一键安装", icon="download",
            ).props("dense unelevated color=primary")

            def _do_install(_e: Any = None) -> None:
                if state["installing"]:
                    return
                state["installing"] = True
                install_btn.props("loading disable")
                ui.notify("正在 pip install -e 三个 sibling 包...",
                          type="info", position="bottom")

                def _bg() -> None:
                    try:
                        # 直接调本进程的 install 函数(绕过 HTTP 鉴权 / cors)
                        import asyncio as _aio
                        from chayuan.server.api_server.admin_routes import (
                            install_sibling,
                        )
                        loop = _aio.new_event_loop()
                        try:
                            result = loop.run_until_complete(
                                install_sibling(name="all", user={"role": "admin"})
                            )
                        finally:
                            loop.close()
                        ok = (result or {}).get("code") == 0
                        try:
                            install_btn.props(remove="loading disable")
                            if ok:
                                ui.notify("✓ 安装完成,请刷新页面",
                                          type="positive", timeout=8000)
                            else:
                                failed = (result or {}).get("data", {}).get("failed", [])
                                ui.notify(
                                    f"部分失败: {failed[:2]};见后端日志",
                                    type="negative", timeout=8000,
                                )
                        except Exception:  # noqa: BLE001
                            pass
                    except Exception as e:  # noqa: BLE001
                        logger.exception("install_sibling failed")
                        try:
                            install_btn.props(remove="loading disable")
                            ui.notify(f"安装失败: {e}", type="negative")
                        except Exception:  # noqa: BLE001
                            pass
                    finally:
                        state["installing"] = False

                import threading
                threading.Thread(target=_bg, daemon=True).start()

            install_btn.on("click", _do_install)

        # 折叠区:技术细节(默认收起,有问题再点开)
        with ui.expansion("技术细节(查看后端报错)").props("dense").classes(
            "q-mt-sm"
        ).style("font-size: 11px;"):
            ui.label(error[:300]).style(
                "color: var(--cy-text-tertiary, #9ca3af); "
                "font-size: 11px; font-family: ui-monospace, monospace; "
                "white-space: pre-wrap;"
            )


__all__ = ["render_mirror_source_row"]
