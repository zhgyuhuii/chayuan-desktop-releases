"""模型配置页 · 图像向量化模型卡片（UI 组件，可复用）。

使命
----
把「图像向量化模型管理」从 REST 黑盒搬到前端可视化，覆盖完整生命周期：

    下载（在线 HF / HF 镜像） → 上传（离线 zip / tar.gz） → 测试（smoke）
    → 启用（只有测试通过的才会出现在知识库下拉）→ 删除（清缓存 + 清测试标记）

架构
----
- UI 侧**不走 HTTP**：Config Panel 与 API Server 同进程同 Python 运行时，直接调用
  :mod:`chayuan.server.image_source.model_registry`  与 :mod:`.model_manager`
  的原生函数——省 token / 省一次 round trip / 错误栈也更清晰。
- 状态读取通过 ``list_models_for_ui`` 一次拿齐三态（依赖 / 缓存 / 测试），UI 只做渲染。
- 耗时操作（download / smoke_test / upload_bundle）统一丢 ``run.io_bound``，
  避免卡死 NiceGUI 的 UI loop。
- 组件形态：``render_image_model_card(ui, mark_restart_needed=None)``，
  可被任意 NiceGUI 页面（目前首次消费者是 ``model_config``）放到任意位置。

镜像 / 代理提示
---------------
- HuggingFace 下载走 ``snapshot_download``，默认连 ``https://hf-mirror.com``；
  如需直连官方，可在启动前设置环境变量 ``HF_ENDPOINT=https://huggingface.co``。
- 需要代理时设置 ``HTTPS_PROXY`` / ``HF_HUB_DOWNLOAD_HTTP_REQUEST_PROXY``。
- UI 会把 ``HF_ENDPOINT``、``CHAYUAN_ROOT`` 当前值展示在卡片顶部，便于排障。
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.config_panel.image_model_panel")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _chinese_level_label(level: str) -> str:
    return {"strong": "中文 ⭐⭐⭐", "medium": "中文 ⭐⭐", "weak": "中文 ⭐"}.get(
        level, level or "-"
    )


def _size_mb_human(mb: float) -> str:
    try:
        v = float(mb)
    except (TypeError, ValueError):
        return "-"
    if v <= 0:
        return "-"
    if v < 1024:
        return f"{v:.0f} MB"
    return f"{v / 1024:.1f} GB"


def _fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "从未测试"
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(int(ts)))
    except Exception:  # noqa: BLE001
        return str(ts)


def _run_bg(
    fn: Callable[[], Any],
    on_done: Callable[[Any], None],
    done_queue: "Queue[tuple[Callable[[Any], None], Any]]",
) -> None:
    """把耗时任务放后台线程，完成后交给 UI timer 回调；避免卡 UI。

    NiceGUI 的 ``run.io_bound`` 也行，但这里我们不依赖 NiceGUI runner 模块以
    保持组件可重用（后续想套 Gradio / 纯 FastAPI 页面都能跑）。
    """
    def _worker() -> None:
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            logger.exception("image-model bg task failed")
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        done_queue.put((on_done, res))

    threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------------------
# 主渲染函数
# ---------------------------------------------------------------------------


def render_image_model_card(
    ui,
    *,
    mark_restart_needed: Optional[Callable[[], None]] = None,
) -> None:
    """在当前容器内渲染「图像向量化模型」卡片。

    ``mark_restart_needed`` 可选：当模型列表对主程序产生副作用（如默认模型切换）
    时用于通知父级展示重启横幅；本卡片下载 / 删除只影响本地缓存，默认不触发重启。
    """
    # 延迟 import，避免启动时把 torch 等重依赖拉进 UI 进程内存路径
    from chayuan.server.image_source.model_manager import (
        DEFAULT_HF_ENDPOINT,
        disk_usage_summary,
        model_cache_root,
        smoke_test_model,
        upload_model_bundle,
    )
    from chayuan.server.image_source.model_registry import list_models_for_ui
    from chayuan.server.shared.jobs import async_enabled, get_job_status, submit_job

    def _download(
        name: str, progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """下载：优先异步（大文件），回落同步；同步可能阻塞几分钟。"""
        if async_enabled():
            task_id, status = submit_job(
                "image_model_download", {"model_name": name},
            )
            return {"ok": True, "async": True, "task_id": task_id, "status": status}
        from chayuan.server.image_source.model_manager import download_model
        return download_model(name, progress_cb=progress_cb)

    # ----- 状态 -----
    state: Dict[str, Any] = {
        "busy": {},  # {model_name: "downloading" | "testing" | "uploading" | "deleting"}
        "progress": {},  # {model_name: "当前进度文本"}
        "download_jobs": {},  # {model_name: task_id}
    }
    done_queue: "Queue[tuple[Callable[[Any], None], Any]]" = Queue()
    progress_queue: "Queue[tuple[str, str]]" = Queue()

    def _drain_done_queue() -> None:
        progress_changed = False
        while True:
            try:
                name, msg = progress_queue.get_nowait()
            except Empty:
                break
            state["progress"][name] = msg
            progress_changed = True

        while True:
            try:
                on_done, res = done_queue.get_nowait()
            except Empty:
                break
            try:
                on_done(res)
            except Exception:  # noqa: BLE001
                logger.exception("image-model bg done callback failed")
        if progress_changed:
            _render_rows()

    def _poll_download_jobs() -> None:
        jobs = dict(state.get("download_jobs") or {})
        if not jobs:
            return

        changed = False
        for name, task_id in jobs.items():
            status = get_job_status(task_id) or {}
            task_state = str(status.get("state") or "queued")
            progress = str(status.get("progress") or "").strip()
            if progress:
                state["progress"][name] = progress
                changed = True
            elif task_state in ("queued", "running"):
                state["progress"][name] = "后台下载任务已提交，等待 worker 拉取模型..."
                changed = True

            if task_state in ("success", "failed", "cancelled"):
                state["download_jobs"].pop(name, None)
                state["busy"].pop(name, None)
                if task_state == "success":
                    size = _size_mb_human(status.get("size_mb") or 0)
                    state["progress"][name] = f"下载完成：{size}"
                    ui.notify(
                        f"已下载 {name}，{size}；请点「测试」验证端到端可用。",
                        type="positive", multi_line=True,
                    )
                else:
                    err = status.get("error") or status.get("reason") or task_state
                    state["progress"][name] = f"下载失败：{err}"
                    ui.notify(
                        f"下载失败：{err}", type="negative", multi_line=True,
                    )
                _render_disk()
                changed = True

        if changed:
            _render_rows()

    # ----- 容器 -----
    card = ui.card().classes("w-full q-mb-md").props("flat bordered").style(
        "padding: 12px 14px; background: #fafbfc;"
    )
    with card:
        # ---- 头部 ----
        with ui.row().classes("items-center w-full no-wrap").style("gap: 8px;"):
            ui.icon("image_search", size="20px").classes("text-indigo-7")
            ui.label("图像向量化模型").classes("text-subtitle1").style(
                "font-weight: 600;"
            )
            ui.label(
                "— 用于图像知识库的向量化存储与跨模态检索；"
                "只有「已下载 + 依赖就绪 + 测试通过」的模型才会出现在新建知识库的下拉中。"
            ).classes("text-caption text-grey-6").style("line-height: 1.4;")

        # ---- 环境变量 / 镜像源提示 ----
        hf_endpoint = os.environ.get("HF_ENDPOINT") or f"{DEFAULT_HF_ENDPOINT} (默认镜像)"
        hf_home = str(model_cache_root())
        with ui.row().classes("items-center w-full no-wrap q-mt-xs").style(
            "gap: 10px; padding: 6px 10px; background: #eef2ff; "
            "border-radius: 6px; font-size: 11px;"
        ):
            ui.icon("info", size="14px").classes("text-indigo-7")
            ui.label(
                f"HF 源：{hf_endpoint}  ·  缓存目录：{hf_home}  ·  "
                "缓存跟随 CHAYUAN_ROOT，迁移数据目录时可一并迁移"
            ).classes("text-caption").style("color: #4338ca; line-height: 1.4;")

        # ---- 磁盘占用 + 批量操作 ----
        disk_row = ui.row().classes("items-center w-full no-wrap q-mt-xs").style(
            "gap: 8px;"
        )
        rows_container = ui.column().classes("w-full q-mt-sm").style("gap: 6px;")

    # ----- 渲染：磁盘占用条 -----
    def _render_disk() -> None:
        disk_row.clear()
        with disk_row:
            try:
                summary = disk_usage_summary()
            except Exception as e:  # noqa: BLE001
                ui.label(f"磁盘统计失败：{e}").classes("text-caption text-negative")
                return
            total = summary.get("total_mb") or 0
            n = len(summary.get("items") or [])
            ui.icon("sd_storage", size="16px").classes("text-grey-7")
            ui.label(
                f"模型缓存：{n} 个  ·  占用 {_size_mb_human(total)}  ·  "
                f"目录：{summary.get('root') or '-'}"
            ).classes("text-caption text-grey-7")
            ui.space()
            ui.button(
                "上传离线包", icon="upload_file",
                on_click=lambda: _open_upload_dialog(),
            ).props("dense flat color=primary").tooltip(
                "内网 / 无外网场景：上传 HF snapshot 的 .zip 或 .tar.gz 离线安装"
            )
            ui.button(
                "刷新", icon="refresh",
                on_click=lambda: (_render_disk(), _render_rows()),
            ).props("dense flat color=grey-7")

    # ----- 渲染：模型行（按能力分组：跨模态 / 仅视觉）-----
    def _render_rows() -> None:
        rows_container.clear()
        try:
            models = list_models_for_ui()
        except Exception as e:  # noqa: BLE001
            with rows_container:
                ui.label(f"模型清单加载失败：{e}").classes("text-negative")
            return

        crossmodal = [m for m in models if (m.get("capabilities") or {}).get("crossmodal")]
        image_only = [m for m in models if not (m.get("capabilities") or {}).get("crossmodal")]

        def _render_header() -> None:
            with ui.row().classes("w-full items-center no-wrap").style(
                "gap: 8px; padding: 4px 8px; color: #6b7280; "
                "font-size: 11px; font-weight: 600; "
                "border-bottom: 1px solid #e5e7eb;"
            ):
                ui.label("模型").style("flex: 1 1 auto; min-width: 0;")
                ui.label("能力").style("flex: 0 0 110px;")
                ui.label("中文").style("flex: 0 0 72px;")
                ui.label("维度 / 大小").style("flex: 0 0 110px;")
                ui.label("状态").style("flex: 0 0 230px;")
                ui.label("操作").style("flex: 0 0 260px; text-align: right;")

        def _render_group(title: str, subtitle: str, items: List[Dict[str, Any]]) -> None:
            if not items:
                return
            with ui.row().classes("w-full items-center no-wrap").style(
                "gap: 6px; padding: 8px 6px 4px; "
                "border-bottom: 1px solid #f3f4f6;"
            ):
                ui.icon(
                    "swap_horiz" if "跨模态" in title else "image",
                    size="16px",
                ).classes("text-indigo-6" if "跨模态" in title else "text-grey-7")
                ui.label(title).classes("text-subtitle2").style(
                    "font-weight: 600; color: #374151;"
                )
                ui.label(subtitle).classes("text-caption text-grey-6").style(
                    "font-size: 11px;"
                )
                ui.label(f"· {len(items)} 个").classes(
                    "text-caption text-grey-5"
                )
            _render_header()
            for m in items:
                _render_one(m)

        with rows_container:
            _render_group(
                "跨模态（文本+图像）",
                "—— 支持文本查图与以图搜图；KB 默认推荐此类",
                crossmodal,
            )
            _render_group(
                "仅视觉（image-only）",
                "—— 只能以图搜图；DINOv2 / ResNet 无文本编码器",
                image_only,
            )

    def _render_one(m: Dict[str, Any]) -> None:
        name = m["name"]
        is_busy = bool(state["busy"].get(name))
        caps = m.get("capabilities") or {}
        extra_deps = m.get("extra_deps") or []

        with ui.row().classes("w-full items-center no-wrap").style(
            "gap: 8px; padding: 6px 8px; border-bottom: 1px solid #f3f4f6; "
            "background: #ffffff; border-radius: 4px;"
        ):
            # ---- 模型名 + 描述 ----
            col = ui.column().classes("col-grow").style(
                "gap: 0; min-width: 0; flex: 1 1 auto;"
            )
            with col:
                with ui.row().classes("items-center no-wrap").style("gap: 4px;"):
                    ui.label(name).classes("text-body2").style(
                        "font-weight: 500; font-family: ui-monospace, Menlo, monospace; "
                        "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                    )
                    _family_chip(m.get("family") or "")
                    if extra_deps:
                        _deps_chip(tuple(extra_deps))
                ui.label(m.get("description") or "").classes(
                    "text-caption text-grey-6"
                ).style("font-size: 11px; line-height: 1.2;")

            # ---- 能力 chip ----
            cap_box = ui.row().classes("items-center no-wrap").style(
                "gap: 3px; flex: 0 0 110px;"
            )
            with cap_box:
                if caps.get("crossmodal"):
                    _cap_chip("🔠+🖼️", "跨模态", "#6366f1")
                elif caps.get("image"):
                    _cap_chip("🖼️", "仅视觉", "#6b7280")

            # ---- 中文能力 ----
            ui.label(_chinese_level_label(m.get("chinese_level", ""))).classes(
                "text-caption text-grey-8"
            ).style("flex: 0 0 72px;")

            # ---- 维度 / 大小 ----
            size_text = _size_mb_human(
                m.get("cached_size_mb") if m.get("cached") else m.get("approx_size_mb")
            )
            ui.label(f"{m.get('dim') or '-'} 维 · {size_text}").classes(
                "text-caption text-grey-7"
            ).style("flex: 0 0 110px;")

            # ---- 状态徽标（三态） ----
            status_box = ui.row().classes("items-center no-wrap").style(
                "gap: 4px; flex: 0 0 230px;"
            )
            with status_box:
                _status_chip(
                    "依赖", m.get("deps_available"),
                    tooltip=m.get("deps_reason") or "torch / transformers / PIL",
                )
                _status_chip("已下载", m.get("cached"))
                _status_chip(
                    "测试", m.get("smoke_tested"),
                    tooltip=_fmt_ts(m.get("smoke_tested_at"))
                    + (("  错误: " + m["smoke_error"]) if m.get("smoke_error") else ""),
                )
                if m.get("ready"):
                    ui.icon("verified", size="16px").classes("text-positive").tooltip(
                        "严格就绪：KB 下拉会显示"
                    )

            # ---- 操作按钮 ----
            btn_row = ui.row().classes("items-center no-wrap").style(
                "gap: 4px; flex: 0 0 260px; justify-content: flex-end;"
            )
            with btn_row:
                if is_busy:
                    busy_kind = state["busy"][name]
                    busy_label = {
                        "downloading": "下载中…",
                        "testing": "测试中…",
                        "deleting": "删除中…",
                        "uploading": "安装中…",
                    }.get(busy_kind, busy_kind)
                    ui.spinner(size="16px")
                    progress_text = state["progress"].get(name) or busy_label
                    with ui.column().classes("q-gutter-none").style(
                        "min-width: 0; flex: 1 1 auto;"
                    ):
                        ui.label(busy_label).classes("text-caption text-grey-7").style(
                            "line-height: 1.1;"
                        )
                        if busy_kind == "downloading":
                            ui.linear_progress(value=None).props(
                                "instant-feedback rounded color=primary indeterminate",
                            ).style("height: 3px; width: 120px;")
                            ui.label(progress_text).classes(
                                "text-caption text-grey-6",
                            ).style(
                                "font-size: 10px; line-height: 1.2; "
                                "max-width: 180px; overflow: hidden; "
                                "text-overflow: ellipsis; white-space: nowrap;"
                            ).tooltip(progress_text)
                else:
                    # 下载（未缓存时）/ 重下（已缓存时作"更新"）
                    ui.button(
                        "重下" if m.get("cached") else "下载",
                        icon="cloud_download",
                        on_click=lambda _e, n=name: _handle_download(n),
                    ).props(
                        "dense unelevated size=sm "
                        + ("color=grey-6" if m.get("cached") else "color=primary")
                    ).tooltip(
                        f"从 HuggingFace ({hf_endpoint}) 下载 ~{_size_mb_human(m.get('approx_size_mb'))}"
                    )

                    # 测试（仅已缓存且依赖 OK）
                    can_test = bool(m.get("cached") and m.get("deps_available"))
                    btn_test = ui.button(
                        "测试", icon="science",
                        on_click=lambda _e, n=name: _handle_test(n),
                    ).props("dense size=sm " + (
                        "unelevated color=teal" if can_test else "flat color=grey-5"
                    ))
                    if not can_test:
                        btn_test.disable()
                        btn_test.tooltip(
                            "需先下载并安装依赖：pip install torch transformers pillow"
                        )
                    else:
                        btn_test.tooltip("加载模型并对一张 8x8 测试图做 embed，验证端到端可用")

                    # 删除（仅已缓存）
                    if m.get("cached"):
                        ui.button(
                            icon="delete_outline",
                            on_click=lambda _e, n=name: _confirm_delete(n),
                        ).props(
                            "dense flat size=sm color=grey-7"
                        ).tooltip("删除本地缓存（会清除测试通过标记）")

    # ----- 小部件：家族 chip、能力 chip、依赖 chip、状态 chip -----
    def _family_chip(family: str) -> None:
        color_map = {
            "siglip": "#0891b2", "clip": "#2563eb", "chinese_clip": "#dc2626",
            "jina_clip": "#059669", "eva_clip": "#7c3aed",
            "open_clip": "#0ea5e9", "dinov2": "#ea580c",
            "timm_vision": "#525252",
        }
        c = color_map.get(family, "#6b7280")
        ui.html(
            f'<span style="display:inline-block;padding:1px 6px;border-radius:8px;'
            f'background:{c}1a;color:{c};font-size:10px;line-height:1.6;'
            f'font-weight:600;">{family or "model"}</span>'
        )

    def _cap_chip(label: str, tooltip: str, color: str) -> None:
        chip = ui.html(
            f'<span style="display:inline-flex;align-items:center;padding:1px 8px;'
            f'border-radius:10px;background:{color}1a;color:{color};'
            f'font-size:11px;line-height:1.5;font-weight:600;">{label}</span>'
        )
        chip.tooltip(tooltip)

    def _deps_chip(extra_deps: tuple) -> None:
        """可选依赖标记：提示用户哪个模型需要额外 pip 包（timm / open_clip_torch）。"""
        pkgs = " / ".join(extra_deps)
        chip = ui.html(
            f'<span style="display:inline-block;padding:1px 6px;border-radius:8px;'
            f'background:#fef3c71a;color:#b45309;font-size:10px;line-height:1.6;'
            f'font-weight:600;border:1px dashed #fcd34d;">需 {pkgs}</span>'
        )
        chip.tooltip(f"该模型需要可选依赖：pip install {' '.join(extra_deps)}")

    def _status_chip(label: str, ok: Any, tooltip: str = "") -> None:
        if ok is True:
            bg, fg, icon = "#ecfdf5", "#065f46", "check_circle"
        elif ok is False:
            bg, fg, icon = "#fef2f2", "#991b1b", "cancel"
        else:
            bg, fg, icon = "#f3f4f6", "#6b7280", "help"
        chip = ui.html(
            f'<span style="display:inline-flex;align-items:center;gap:2px;'
            f'padding:1px 6px;border-radius:10px;background:{bg};color:{fg};'
            f'font-size:11px;line-height:1.5;font-weight:600;">'
            f'<i class="material-icons" style="font-size:12px;">{icon}</i>'
            f'{label}</span>'
        )
        if tooltip:
            chip.tooltip(tooltip)

    # ----- 动作：下载 / 测试 / 删除 / 上传 -----
    def _handle_download(name: str) -> None:
        state["busy"][name] = "downloading"
        state["progress"][name] = "准备下载..."
        _render_rows()

        def _on_done(res: Any) -> None:
            if isinstance(res, dict) and res.get("async"):
                task_id = str(res.get("task_id") or "")
                if not task_id:
                    state["busy"].pop(name, None)
                    state["progress"][name] = "提交后台下载失败：未返回任务 ID"
                    ui.notify("提交后台下载失败：未返回任务 ID", type="negative")
                    _render_rows()
                    return
                state["download_jobs"][name] = task_id
                state["progress"][name] = (
                    f"后台任务 {task_id} 已提交，正在等待下载进度..."
                )
                ui.notify(
                    f"已提交后台下载任务 {task_id}；"
                    "大模型通常需数分钟，可点刷新查看状态。",
                    type="info", multi_line=True,
                )
                _render_rows()
                return
            state["busy"].pop(name, None)
            if isinstance(res, dict) and res.get("ok"):
                state["progress"][name] = "下载完成"
                ui.notify(
                    f"已下载 {name}，{_size_mb_human(res.get('size_mb') or 0)}；"
                    "请点「测试」验证端到端可用。",
                    type="positive", multi_line=True,
                )
            else:
                err = res.get("error") if isinstance(res, dict) else str(res)
                state["progress"][name] = f"下载失败：{err}"
                ui.notify(f"下载失败：{err}", type="negative", multi_line=True)
            _render_disk()
            _render_rows()

        _run_bg(
            lambda: _download(
                name,
                progress_cb=lambda msg, n=name: progress_queue.put((n, msg)),
            ),
            _on_done,
            done_queue,
        )

    def _handle_test(name: str) -> None:
        state["busy"][name] = "testing"
        _render_rows()

        def _on_done(res: Any) -> None:
            state["busy"].pop(name, None)
            if isinstance(res, dict) and res.get("ok"):
                ui.notify(
                    f"✔ 测试通过 {name}（{res.get('dim')} 维）；"
                    "它现在会出现在新建知识库的下拉中。",
                    type="positive", multi_line=True,
                )
            else:
                err = res.get("error") if isinstance(res, dict) else str(res)
                ui.notify(
                    f"测试失败：{err}", type="negative", multi_line=True, timeout=6000,
                )
            _render_rows()

        _run_bg(lambda: smoke_test_model(name), _on_done, done_queue)

    def _confirm_delete(name: str) -> None:
        with ui.dialog() as d, ui.card().style("min-width: 360px;"):
            ui.label(f"删除模型缓存「{name}」？").classes("text-subtitle1").style(
                "font-weight: 600;"
            )
            ui.label(
                "将移除该模型在本地的全部权重文件以及测试通过标记；"
                "任何仍在使用该模型的图像知识库检索会失败，需重新下载或切换模型。"
            ).classes("text-caption text-grey-7").style("line-height: 1.5;")
            with ui.row().classes("w-full justify-end q-mt-sm").style("gap: 6px;"):
                ui.button("取消", on_click=d.close).props("flat dense")

                def _do() -> None:
                    d.close()
                    state["busy"][name] = "deleting"
                    _render_rows()
                    from chayuan.server.image_source.model_manager import delete_model

                    def _on_done(res: Any) -> None:
                        state["busy"].pop(name, None)
                        if isinstance(res, dict) and res.get("ok"):
                            ui.notify(res.get("msg") or "已删除", type="positive")
                        else:
                            ui.notify(
                                f"删除失败：{res.get('error') if isinstance(res, dict) else res}",
                                type="negative",
                            )
                        _render_disk()
                        _render_rows()

                    _run_bg(lambda: delete_model(name), _on_done, done_queue)

                ui.button(
                    "确认删除", icon="delete_outline", on_click=_do,
                ).props("unelevated dense color=negative")
        d.open()

    # ----- 上传离线包 -----
    def _open_upload_dialog() -> None:
        with ui.dialog() as dialog, ui.card().style("min-width: 520px;"):
            ui.label("上传模型离线包").classes("text-h6 q-mb-xs")
            ui.label(
                "适合无公网 / 企业内网场景。"
                "将 HuggingFace snapshot 打包成 .zip / .tar.gz 上传，后端会解压到缓存目录。"
                "建议导出仅权重 + 配置文件（忽略 .msgpack / .h5 等冗余格式）以缩减体积。"
            ).classes("text-caption text-grey-6 q-mb-sm").style(
                "line-height: 1.5;"
            )

            name_input = ui.input(
                label="模型名称（与 HF repo_id 一致，如 google/siglip2-base-patch16-224）",
            ).props("dense outlined autofocus").classes("w-full")

            upload_state: Dict[str, Any] = {"path": None, "filename": ""}

            def _on_upload(e) -> None:
                data = e.content.read() if hasattr(e, "content") else None
                if not data:
                    return
                suffix = os.path.splitext(e.name or "")[1] or ".zip"
                if suffix.lower() not in (".zip", ".gz", ".tar", ".tgz"):
                    ui.notify(
                        f"不支持的文件类型 {suffix}，仅接受 .zip / .tar.gz / .tgz",
                        type="warning",
                    )
                    return
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=suffix,
                ) as tf:
                    tf.write(data)
                    upload_state["path"] = tf.name
                    upload_state["filename"] = e.name or "bundle"
                ui.notify(
                    f"已接收 {e.name}（{_size_mb_human(len(data) / (1024 * 1024))}），"
                    "请填写模型名后点「开始安装」",
                    type="info", multi_line=True,
                )

            ui.upload(
                label="拖拽或选择 .zip / .tar.gz 文件",
                auto_upload=True,
                on_upload=_on_upload,
                multiple=False,
            ).props("accept=.zip,.tar,.gz,.tgz max-file-size=5368709120").classes(
                "w-full"
            )

            btn_row = ui.row().classes("w-full justify-end q-mt-sm").style("gap: 6px;")
            with btn_row:
                ui.button("取消", on_click=dialog.close).props("flat dense")

                def _do_install() -> None:
                    name = (name_input.value or "").strip()
                    if not name:
                        ui.notify("请填写模型名称", type="warning")
                        return
                    path = upload_state.get("path")
                    if not path:
                        ui.notify("请先选择离线包", type="warning")
                        return
                    dialog.close()
                    state["busy"][name] = "uploading"
                    _render_rows()

                    def _on_done(res: Any) -> None:
                        state["busy"].pop(name, None)
                        try:
                            os.unlink(path)
                        except Exception:  # noqa: BLE001
                            pass
                        if isinstance(res, dict) and res.get("ok"):
                            ui.notify(
                                f"已安装 {name}；请点「测试」验证可用性。",
                                type="positive", multi_line=True,
                            )
                        else:
                            ui.notify(
                                f"安装失败：{res.get('error') if isinstance(res, dict) else res}",
                                type="negative", multi_line=True,
                            )
                        _render_disk()
                        _render_rows()

                    _run_bg(
                        lambda: upload_model_bundle(model_name=name, bundle_path=path),
                        _on_done,
                        done_queue,
                    )

                ui.button(
                    "开始安装", icon="save_alt", on_click=_do_install,
                ).props("unelevated dense color=primary")

        dialog.open()

    # ----- 首次渲染 -----
    _render_disk()
    _render_rows()
    ui.timer(0.1, _drain_done_queue)
    ui.timer(1.0, _poll_download_jobs)


__all__ = ["render_image_model_card"]
