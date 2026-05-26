"""通用 lazy + async 渲染装饰器 — 切 tab 时避免同步阻塞 NiceGUI WS。

设计动机
========

4 个 tab(① 运行时 / ② 模型厂商 / ③ 模型广场 / ④ 默认模型)的 click handler
在切换时**同步**调 ``render_xxx_subpage`` → 大量 quasar 组件一次塞进 WS 单帧
→ client ack 超时 → NiceGUI 判定连接断 → 前端整页 reload。

② 模型厂商最严重:30+ 云厂商卡片 + 4 个本地框架 hero strip + 搜索栏 +
"添加自定义" dialog + "全部保存"按钮,组件总数过千。

机制
====

1. **click handler 同步部分**:清空容器 → mount 单 spinner 占位(<10ms,3 个组件)
2. ``ui.timer(delay, _real_mount, once=True)`` 把真渲染推到下一 event loop tick
3. ``_real_mount`` 内部:清 spinner → 在原 container 内调 ``render_fn``
4. ``render_fn`` 异常时显示 fallback label,不让整个 panel 崩

性能
====

* click 响应:<10ms(spinner 行 mount)
* 真渲染:50ms 后开始,与 click handler 完全解耦
* WS 心跳:整个加载期间不间断 → 不会触发 connection lost

模块化与复用
============

4 个 ``_render_xxx`` 函数共用本工具,任何子页面想要"立刻反馈 + 异步实渲染"
直接 ``lazy_async_render(ui, container, label, render_fn)`` 即可,无需再写
重复的 skeleton/timer/error-fallback 模板代码。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Tuple

logger = logging.getLogger("chayuan.config_panel.model_settings._async_mount")


def lazy_async_render(
    ui: Any,
    container: Any,
    label: str,
    render_fn: Callable[[], None],
    *,
    delay: float = 0.05,
    error_prefix: str = "渲染失败:",
) -> None:
    """在 ``container`` 内先 mount spinner,延迟 ``delay`` 秒异步执行 ``render_fn``。

    Args:
        ui: NiceGUI ui 模块。
        container: 已存在的 ``ui.column`` / ``ui.element``;先清空 → mount
            spinner → 异步清 spinner → mount 真内容。**调用前 container 必须已
            进入 client scope**(已 ``with parent:``)。
        label: spinner 旁边提示文字,如 ``"② 模型厂商"``;也用于 logger / 错误
            消息前缀。
        render_fn: **同步**渲染函数,无参无返回。在原 container scope 内被调用,
            函数体里可以直接 ``ui.row()/ui.column()/...`` 无需再开 scope。
            渲染函数若抛异常,错误信息会显示在 container 内,**不会向外传播**。
        delay: 异步 mount 延迟秒数,默认 ``0.05``;给 NiceGUI 一帧时间把
            spinner 同步到客户端 + 处理 WS 心跳。低于 0.03 可能不够。
        error_prefix: ``render_fn`` 抛异常时显示的错误前缀。

    Note:
        * 若 ``container.clear()`` 抛(client 已死),静默吞掉 — 用户已离开页面
        * ``ui.timer`` 由 NiceGUI 在 client scope 内管理,client 死亡时自动取消
    """
    try:
        container.clear()
    except Exception:  # noqa: BLE001
        # client 已死(用户离开页) — 不再继续
        return

    with container:
        spinner_row = ui.row().classes("items-center w-full no-wrap").style(
            "padding: 24px; gap: 10px;"
        )
        with spinner_row:
            ui.spinner(size="md").props("color=primary")
            ui.label(f"{label} 加载中…").classes("text-caption text-grey-7")

    def _real_mount() -> None:
        # 异步 tick:清 spinner → mount 真内容
        try:
            container.clear()
        except Exception:  # noqa: BLE001
            return  # client 已死,放弃
        try:
            with container:
                render_fn()
        except Exception as e:  # noqa: BLE001
            logger.exception("[%s] async render failed", label)
            # 不再 raise,改在 container 内显示错误 — 整页不崩
            try:
                container.clear()
                with container:
                    ui.label(
                        f"{error_prefix}{label}({type(e).__name__}: {e})"
                    ).classes("text-negative text-sm font-mono")
                    ui.label("(可刷新页面重试,或查看服务日志)").classes(
                        "text-xs text-grey-6"
                    )
            except Exception:  # noqa: BLE001
                pass

    try:
        ui.timer(delay, _real_mount, once=True)
    except Exception:  # noqa: BLE001
        # ui.timer 在某些 client scope 外的环境失败 — 退化为同步执行
        logger.debug(
            "[%s] ui.timer unavailable, falling back to sync render", label,
        )
        _real_mount()


def chunked_async_render(
    ui: Any,
    container: Any,
    label: str,
    chunks: List[Tuple[str, Callable[[], None]]],
    *,
    initial_delay: float = 0.05,
    batch_delay: float = 0.03,
    show_progress: bool = True,
    error_prefix: str = "渲染失败:",
) -> None:
    """**流式分块**渲染 — 把 N 个组件子树分多个 ui.timer 步骤 mount,每步之间
    让出 event loop 给 NiceGUI WS 心跳处理 ack。

    设计动机
    --------

    ``lazy_async_render`` 把"整团组件"整体推迟 50ms 才 mount,**真渲染开始后**
    仍然是一次同步 mount,大量组件序列化超时 → connection lost。

    本函数把渲染拆成 N 个独立 chunk(``[(name, fn), ...]``),链式 ``ui.timer``
    串起来:每个 chunk mount 后,用一个新 timer 排下一个 chunk,**两个 chunk
    之间让 event loop 跑一帧**,WS 心跳和 ack 能正常处理。

    时间线
    ------
    ::

        t=0       click handler 同步:container.clear() + mount progress label
        t=50ms    ui.timer fire → 调 chunks[0].fn()(mount 第 1 块,如顶栏)
        t=80ms    ui.timer fire → 调 chunks[1].fn()(mount 第 2 块,如 推荐 5 张)
        t=110ms   ...                     chunks[2].fn()(国内 15 张)
        t=140ms   ...                     chunks[3].fn()(国外 20 张)
        t=170ms   ...                     chunks[4].fn()(聚合 15 张)
        t=200ms   全部完成,进度 label 删除

    每个 chunk 之间 30ms 间隔 ≈ 2 帧,足够 WS 处理上一帧的 quasar JSON。

    Args:
        chunks: ``[(name, render_fn), ...]``,每个 ``render_fn`` 在原 container
            scope 内 mount 一个组件子树(如 ``ui.row()/ui.column()/ui.card()...``)。
            **顺序执行**,不并发(NiceGUI ui 命令必须在 client scope 同步序列)。
        initial_delay: 第 1 个 chunk 的延迟,默认 ``0.05``(同 ``lazy_async_render``)。
        batch_delay: 后续 chunk 之间的间隔,默认 ``0.03``(2 帧)。
        show_progress: ``True`` 时在 spinner 旁显示 ``"已加载 i/N (chunk_name)"``。

    Note:
        * 任意 chunk 异常 → 显示错误 label,**继续**执行后续 chunk(局部失败不阻塞整体)
        * client 死亡 → 静默退出,不再排后续 timer
        * 空 ``chunks`` 列表 → 直接清 container 退出
    """
    if not chunks:
        try:
            container.clear()
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        container.clear()
    except Exception:  # noqa: BLE001
        return

    # 进度区(spinner + 文字),mount 完所有 chunk 后整体删除
    # show_progress=False:完全不 mount(给已有顶栏加载提示的场景留出位置)
    progress_holder: Dict[str, Any] = {}
    if show_progress:
        with container:
            progress_row = ui.row().classes("items-center w-full no-wrap").style(
                "padding: 6px 12px; gap: 8px;"
            )
            with progress_row:
                progress_holder["spinner"] = ui.spinner(size="sm").props("color=primary")
                progress_holder["label"] = ui.label(
                    f"{label} 加载中… (0/{len(chunks)})"
                ).classes("text-caption text-grey-7")
            progress_holder["row"] = progress_row

    state = {"i": 0}
    total = len(chunks)

    def _update_progress(name: str) -> None:
        if not show_progress:
            return
        lbl = progress_holder.get("label")
        if lbl is None:
            return
        try:
            lbl.set_text(f"{label} 加载中… ({state['i']}/{total} {name})")
        except Exception:  # noqa: BLE001
            pass

    def _remove_progress() -> None:
        row = progress_holder.get("row")
        if row is None:
            return
        try:
            row.delete()
        except Exception:  # noqa: BLE001
            pass

    def _run_chunk(idx: int) -> None:
        # client 死 → 提前退出
        try:
            if hasattr(container, "client") and not getattr(
                container.client, "has_socket_connection", True,
            ):
                _remove_progress()
                return
        except Exception:  # noqa: BLE001
            pass

        if idx >= total:
            _remove_progress()
            return

        name, fn = chunks[idx]
        try:
            with container:
                fn()
        except Exception as e:  # noqa: BLE001
            logger.exception("[%s] chunk %s render failed", label, name)
            try:
                with container:
                    ui.label(
                        f"{error_prefix}{label}/{name}({type(e).__name__}: {e})"
                    ).classes("text-negative text-xs font-mono")
            except Exception:  # noqa: BLE001
                pass

        state["i"] = idx + 1
        _update_progress(name)

        if idx + 1 < total:
            try:
                ui.timer(batch_delay, lambda: _run_chunk(idx + 1), once=True)
            except Exception:  # noqa: BLE001
                # timer 不可用 — 退化同步 finish remaining
                _run_chunk(idx + 1)
        else:
            _remove_progress()

    try:
        ui.timer(initial_delay, lambda: _run_chunk(0), once=True)
    except Exception:  # noqa: BLE001
        # timer 不可用 — 退化同步执行所有 chunks
        logger.debug("[%s] ui.timer unavailable, sync fallback for chunked", label)
        _run_chunk(0)


__all__ = ["lazy_async_render", "chunked_async_render"]
