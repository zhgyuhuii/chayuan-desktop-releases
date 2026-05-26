"""模型框架"安装"弹窗 —— 固定大小 / 关闭不杀任务 / 多镜像源 / 内置配置编辑器。

UE 设计:
    ┌─────────────────────────────── 720×740 固定 ───────────────────────────┐
    │ Ollama · 安装与配置                                          [✕]       │  ← 标题 + 右上关闭
    ├────────────────────────────────────────────────────────────────────────┤
    │ 状态:已安装·未启动 · binary: /usr/local/bin/ollama                    │  ← 健康 + URL
    ├────────────────────────────────────────────────────────────────────────┤
    │ 镜像源: [官方 curl] [国内 ghproxy] [阿里云 OSS] [Docker 官方] [自定义▾]│  ← chip 选择
    │  下方说明: "上游官方;海外网络流畅时首选"                                │
    ├────────────────────────────────────────────────────────────────────────┤
    │            [▶ 一键安装]   [■ 终止任务]   [▶ 启动]   [⏹ 停止]            │  ← 操作按钮
    │  当前任务: install-ab12cd · 运行中 · 12s                               │
    ├────────────────────────────────────────────────────────────────────────┤
    │ ▾ 配置文件  <CHAYUAN_ROOT>/runtime/funasr.yaml [💾 保存并重启]    │  ← 可折叠
    │  ┌──────────────────────────────────────────────────────────────────┐  │
    │  │ model: paraformer-zh                                              │  │
    │  │ vad_model: fsmn-vad                                               │  │
    │  │ ...                                                               │  │
    │  └──────────────────────────────────────────────────────────────────┘  │
    ├────────────────────────────────────────────────────────────────────────┤
    │  日志(滚动)                                                           │
    └────────────────────────────────────────────────────────────────────────┘

关闭右上 ✕:
* 弹窗关 → 但 install_task_manager 任务继续
* 再开 → ``manager.attach(framework)`` 拿到当前 task,日志 / 状态恢复

"终止任务"按钮:
* 显式终止 → manager.cancel(task_id) → SIGTERM 然后 3s 后 SIGKILL

"配置文件"折叠区(NEW):
* 仅当 framework 有管理的 yaml 文件时显示(funasr / cosyvoice / rapidocr /
  paddleocr 4 个 modality wrapper);其他 framework 显示"无独立配置(由命令行
  / 自身配置文件管理)"
* "保存并重启":写入 yaml → manager.stop_service → manager.start_service → 探活
"""
from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from chayuan.server.config_panel.install_recipes import (
    IMAGE_REWRITES,
    MIRROR_SOURCES,
    custom_recipe,
    get_recipes,
    get_recipes_simplified,
)
from chayuan.server.config_panel.install_task_manager import (
    InstallRecipe,
    get_install_manager,
)

# ============================================================================
# 配置编辑器(NEW):每一个有"管理 yaml"的 framework 在弹窗内可见 + 可改 + 重启
# ----------------------------------------------------------------------------
# 设计要点:
# * 4 个 modality wrapper(funasr/cosyvoice/rapidocr/paddleocr)由我们维护
#   <CHAYUAN_ROOT>/runtime/<framework>.yaml — 列在 _MANAGED_CONFIG_FRAMEWORKS
# * 其它 framework(ollama/vllm/comfyui 等)有自己的配置体系,不在此管;
#   弹窗里显示 "无独立配置"。
# * 保存触发停-启 daemon,刷新探针,这样 yaml 改动立即生效不用重启 chayuan。
# ============================================================================

_MANAGED_CONFIG_FRAMEWORKS: Dict[str, str] = {
    # framework -> 默认端口(只用于"重启后探活"提示)
    "funasr": "18180",
    "cosyvoice": "18280",
    "rapidocr": "18380",
    "paddleocr": "18480",
    "voxcpm2": "18580",
}


def _bootstrap_default_yaml(framework: str) -> str:
    """从对应 wrapper 模块抓默认配置,转 yaml。用于首次打开 / 重置默认。"""
    try:
        if framework == "funasr":
            from chayuan.server.modality.funasr_server import _DEFAULT_CONFIG
        elif framework == "cosyvoice":
            from chayuan.server.modality.cosyvoice_server import _DEFAULT_CONFIG
        elif framework == "rapidocr":
            from chayuan.server.modality.rapidocr_server import _DEFAULT_CONFIG
        elif framework == "paddleocr":
            from chayuan.server.modality.paddleocr_server import _DEFAULT_CONFIG
        elif framework == "voxcpm2":
            from chayuan.server.modality.voxcpm2_server import _DEFAULT_CONFIG
        else:
            return ""
        import yaml
        return (
            f"# {framework} 默认配置 — 由 chayuan UI 写入\n"
            f"# 修改后点 '保存并重启' 立即生效\n"
            + yaml.safe_dump(_DEFAULT_CONFIG, allow_unicode=True, sort_keys=False)
        )
    except Exception as e:  # noqa: BLE001
        return f"# 读取默认配置失败: {e}\n"


def _runtime_config_path(framework: str) -> Optional[Any]:
    """返回 ``Path`` 对象;不在白名单则返回 None。"""
    if framework not in _MANAGED_CONFIG_FRAMEWORKS:
        return None
    try:
        from chayuan.server.modality._runtime_server_base import runtime_config_path

        return runtime_config_path(framework)
    except ImportError:
        return None


# 43 题 P0.C / 83 题:docker 类 framework(由 compose 管理)的 yaml 真源是
# ``<CHAYUAN_ROOT>/compose/<service>.yaml``(每服务一文件,83 题后约定),
# 历史聚合文件 ``docker-compose.yaml`` 仅作 fallback。
# UI 在配置 tab 显示**该服务自己的 yaml**让用户编辑;保存后点 ▶ 启动 即可重起。
def _is_docker_framework(spec: Any) -> bool:
    """判断 framework 是否由 docker-compose 管理。

    优先看 spec.install_kind;再 fallback 到 compose_manager.is_managed。
    """
    try:
        if str(getattr(spec, "install_kind", "") or "") == "docker":
            return True
        from chayuan.server.config_panel.compose_manager import is_managed
        return is_managed(getattr(spec, "name", ""))
    except Exception:  # noqa: BLE001
        return False


def _compose_config_path(service: Optional[str] = None) -> Optional[Any]:
    """返回该 service 对应的 compose yaml 路径。

    优先级:
      1. ``<CHAYUAN_ROOT>/compose/<service>.yaml`` —— 83 题后每个服务独立 yaml
         (``compose_manager.get_compose_file_for_service``)
      2. ``<CHAYUAN_ROOT>/compose/docker-compose.yaml`` —— 历史聚合 fallback
      3. None —— compose_manager 都失败时
    """
    try:
        from chayuan.server.config_panel.compose_manager import (
            ensure_compose_file,
            get_compose_file_for_service,
        )
        if service:
            per_svc = get_compose_file_for_service(service)
            if per_svc is not None:
                return per_svc
        return ensure_compose_file()
    except Exception:  # noqa: BLE001
        return None


# Phase 4 — CPU 性能提示表
# GPU 框架在 CPU 上跑性能差,UI 显式警告
_CPU_PERFORMANCE_HINTS: Dict[str, str] = {
    "vllm": "⚠ vLLM 在 CPU 模式性能差(大模型加载几分钟,推理 1-5 token/s);"
            "建议有 NVIDIA GPU 时启用 --profile gpu",
    "vllm-cpu": "⚠ vLLM CPU 镜像 — 启动慢(2-3 分钟首次)、推理慢;"
                "仅适合临时测试,生产请用 GPU",
    "comfyui": "⚠ ComfyUI 在 CPU 模式几乎不能用 — "
               "单张 SD 图片 CPU 推理需 5-10 分钟。建议必须有 GPU。",
}


def _render_docker_deploy_inline(
    ui: Any,
    *,
    framework: str,
    label: str,
    h: Any,
    on_after_install: Optional[Any],
    log_ref: Dict[str, Any],
) -> None:
    """54-B — 简化版 docker 启动 inline 按钮(替代独立 deploy tab + 部署日志区)。

    设计变化:
      * 配置 tab yaml 编辑器**顶部**两个按钮:▶ 启动 / ⏹ 停止
      * 启动按钮直接 ``docker compose up -d --wait`` + auto-register
      * 部署日志写到 install_dialog 现有 ``log_html``(日志 tab),不再独立日志区
      * 启动时自动切到日志 tab 让用户看流式输出

    Args:
        log_ref: ``{"log_html": ui.html, "log_tab_select": Callable}`` 共享引用 dict。
            因为 cfg_panel 内 mount 按钮时 log_html 还没创建,通过 dict ref
            延迟填充 — 用户点击时(click handler 时刻)ref 已就绪。
    """
    # CPU 提示(仅 vllm/vllm-cpu/comfyui 等有此项时)
    cpu_hint = _CPU_PERFORMANCE_HINTS.get(framework, "")
    if cpu_hint:
        with ui.row().classes("w-full items-start").style(
            "padding: 6px 16px; gap: 6px;"
            "background: #fef3c7; border-left: 3px solid #f59e0b;"
            "margin: 6px 16px 0 16px; border-radius: 4px;"
        ):
            ui.icon("warning", size="14px").classes("text-amber-8")
            ui.label(cpu_hint).style(
                "color: #78350f; font-size: 11px; line-height: 1.4;"
            )

    # 启动 / 停止 按钮 + 状态(配置 tab yaml 编辑器顶部 inline)
    with ui.row().classes("items-center w-full no-wrap").style(
        "padding: 6px 16px; gap: 8px;",
    ):
        start_btn = ui.button(
            f"▶ 启动 {label}", icon="play_arrow",
        ).props("unelevated color=primary dense")
        stop_btn = ui.button(
            "⏹ 停止", icon="stop",
        ).props("flat dense")
        ui.label(
            "调 docker compose up -d --wait,日志见 [📋 日志] tab",
        ).style("color: #6b7280; font-size: 11px; margin-left: 8px;")
        deploy_status = ui.label("").style(
            "color: #6b7280; font-size: 11px; margin-left: auto;",
        )

    # 共享状态:日志 buffer
    log_buf: List[str] = []

    def _append(line: str) -> None:
        """追加一行到 install_dialog 现有 log_html(日志 tab,延迟通过 ref 拿)。"""
        log_buf.append(line)
        if len(log_buf) > 500:
            del log_buf[: len(log_buf) - 500]
        log_html = log_ref.get("log_html")
        if log_html is None:
            return
        try:
            log_html.set_content(
                "\n".join(_html_escape(s) for s in log_buf),
            )
        except Exception:  # noqa: BLE001
            pass

    def _switch_to_log_tab() -> None:
        sel = log_ref.get("log_tab_select")
        if sel is None:
            return
        try:
            sel("log")
        except Exception:  # noqa: BLE001
            pass

    async def _do_start() -> None:
        """▶ 启动:docker compose up -d --wait + auto-register。"""
        from chayuan.server.config_panel.container_lifecycle import (
            get_container_lifecycle, LifecycleError,
        )
        from chayuan.server.config_panel.auto_register import (
            register_after_healthy,
        )
        lc = get_container_lifecycle()

        start_btn.props("loading")
        deploy_status.set_text("准备中...")
        # 自动切到日志 tab,让用户看流式输出
        _switch_to_log_tab()

        try:
            # 拉镜像 — 用 ContainerLifecycle 流式
            deploy_status.set_text("拉镜像...")
            _append(f"\n$ docker compose pull {framework}")
            try:
                async for ll in lc.pull(framework, timeout=900.0):
                    _append(ll.text)
            except LifecycleError as e:
                _append(f"[ERROR] pull 失败: {e.code.value} — {e.hint}")
                deploy_status.set_text("✗ pull 失败")
                ui.notify(f"{label} 拉镜像失败: {e.hint}", type="negative")
                return

            # 启动 + 等 healthy(含 45 题端口冲突自动重分配)
            deploy_status.set_text("启动并等 healthy...")
            _append(f"\n$ docker compose up -d --wait {framework}")
            try:
                health = await lc.up(framework, wait_healthy=True, timeout=180.0)
                if health.port_reallocated_to is not None:
                    msg = (
                        f"⚠ host 端口 {health.port_reallocated_from} 被占用,"
                        f"已自动改用 {health.port_reallocated_to} 并写回 yaml"
                    )
                    _append(msg)
                    ui.notify(msg, type="warning", timeout=8000)
                _append(
                    f"[OK] 容器: {health.state.value} ({health.container_name})",
                )
            except LifecycleError as e:
                _append(f"[ERROR] up 失败: {e.code.value} — {e.hint}")
                if e.stderr:
                    _append("─ stderr ─")
                    _append(e.stderr[:500])
                deploy_status.set_text("✗ 启动失败")
                ui.notify(f"{label} 启动失败: {e.hint}", type="negative")
                return

            # 自动注册模型
            deploy_status.set_text("自动注册模型...")
            _append("\n$ auto-register")
            report = await register_after_healthy(framework)
            _append(report.summary())

            if report.ok:
                deploy_status.set_text("✓ 已就绪")
                ui.notify(report.summary(), type="positive", timeout=8000)
                if on_after_install:
                    try:
                        on_after_install()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                deploy_status.set_text("⚠ 已启动,注册失败")
                ui.notify(
                    f"{label} 已启动但自动注册失败 — 见日志",
                    type="warning", timeout=6000,
                )
        except Exception as e:  # noqa: BLE001
            logger.exception("[deploy] %s 失败", framework)
            _append(f"[FATAL] {type(e).__name__}: {e}")
            deploy_status.set_text("✗ 内部错误")
            ui.notify(f"{label} 内部错误: {e}", type="negative", timeout=8000)
        finally:
            start_btn.props(remove="loading")

    async def _do_stop() -> None:
        """⏹ 停止:docker compose stop <service>。"""
        from chayuan.server.config_panel.container_lifecycle import (
            get_container_lifecycle, LifecycleError,
        )
        lc = get_container_lifecycle()
        stop_btn.props("loading")
        deploy_status.set_text("停止中...")
        _switch_to_log_tab()
        try:
            _append(f"\n$ docker compose stop {framework}")
            ok = await lc.stop(framework)
            _append(f"[{'OK' if ok else 'ERROR'}] stop {framework}")
            if ok:
                deploy_status.set_text("✓ 已停止")
                ui.notify(f"{label} 已停止", type="info", timeout=4000)
                if on_after_install:
                    try:
                        on_after_install()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                deploy_status.set_text("✗ 停止失败")
                ui.notify(f"{label} 停止失败 — 见日志", type="warning")
        except Exception as e:  # noqa: BLE001
            logger.exception("[stop] %s 失败", framework)
            _append(f"[FATAL] {type(e).__name__}: {e}")
            deploy_status.set_text("✗ 错误")
        finally:
            stop_btn.props(remove="loading")

    start_btn.on("click", _do_start)
    stop_btn.on("click", _do_stop)


# 别名保持向后兼容(其他位置可能还在 import 旧名)
_render_docker_deploy_section = _render_docker_deploy_inline


def _render_compose_source_switcher(ui: Any, *, framework: str) -> None:
    """56-B 题:在配置 tab 顶部加一行 chip,让用户一键切换镜像源。

    用户场景:海外网慢拉不到 ``vllm/vllm-openai:latest``,改用国内镜像源:
      * 官方源 (Docker Hub) — 海外
      * DaoCloud — 国内主流加速
      * 1ms.run — 国内备用
      * 阿里云 — 企业用户

    切换逻辑:复制 ``compose/sources/<name>.yaml`` 到 ``docker-compose.yaml``,
    保留用户已编辑的端口 / volumes / environment(只换 image 字段)。
    """
    try:
        from chayuan.server.config_panel.compose_sources import (
            list_compose_sources, get_active_source_name,
            activate_source, ensure_source_files,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("[compose-source-switch] import failed: %r", e)
        return

    # 首次启动时把内置源 yaml 写到 sources/(若不存在)
    try:
        ensure_source_files()
    except Exception as e:  # noqa: BLE001
        logger.debug("[compose-source-switch] ensure files failed: %r", e)

    sources = list_compose_sources()
    active = get_active_source_name()

    # chip 行 — 容器顶部小一行
    with ui.row().classes("items-center w-full no-wrap").style(
        "padding: 4px 16px 0 16px; gap: 6px; flex-wrap: wrap;",
    ):
        ui.icon("dns", size="14px").classes("text-grey-7")
        ui.label("镜像源:").style(
            "color: #6b7280; font-size: 11px; font-weight: 500;",
        )
        for s in sources:
            is_active = (s.name == active)
            chip = ui.chip(
                s.label,
                color="primary" if is_active else None,
                text_color="white" if is_active else None,
            ).props("clickable" + (" outline" if not is_active else ""))
            chip.tooltip(s.description)

            def _on_click(_e: Any = None, name: str = s.name, _label: str = s.label) -> None:
                # 切换源 → 复制 yaml + 重启对应 service(用户在 [▶ 启动] 那里手动)
                ok = activate_source(name)
                if ok:
                    ui.notify(
                        f"✓ 已切换到 [{_label}] — 点 ▶ 启动 重起容器拉新镜像",
                        type="positive", timeout=6000,
                    )
                else:
                    ui.notify(
                        f"切换 [{_label}] 失败 — 见日志",
                        type="negative", timeout=4000,
                    )

            chip.on("click", _on_click)


def _find_diagnose_script() -> Optional[Path]:
    """在 chayuan-server 仓的 ``scripts/`` 找 install_diagnose.py。

    搜索路径(按顺序):
      1. ``CHAYUAN_REPO_ROOT/scripts/install_diagnose.py``(环境变量)
      2. 从 ``__file__`` 上溯 4-5 级找 ``scripts/install_diagnose.py``
         (libs/chayuan-server/chayuan/server/config_panel/install_dialog.py
          → libs/chayuan-server/scripts/  否
          → 仓根/scripts/                 是)
      3. cwd 下找 ``scripts/install_diagnose.py``(用户在仓根跑 chayuan)
    """
    import os

    env = os.environ.get("CHAYUAN_REPO_ROOT", "").strip()
    if env:
        p = Path(env) / "scripts" / "install_diagnose.py"
        if p.exists():
            return p
    here = Path(__file__).resolve()
    for parent in [here.parent.parent.parent.parent.parent,  # 仓根
                   here.parent.parent.parent.parent,         # libs/chayuan-server
                   here.parent.parent.parent]:               # chayuan/
        cand = parent / "scripts" / "install_diagnose.py"
        if cand.exists():
            return cand
    cwd_cand = Path.cwd() / "scripts" / "install_diagnose.py"
    if cwd_cand.exists():
        return cwd_cand
    return None

logger = logging.getLogger("chayuan.config_panel.install_dialog")


_HEALTH_TONE: Dict[str, Dict[str, str]] = {
    "running":    {"bg": "#ecfdf5", "border": "#a7f3d0", "text": "运行中",       "color": "#10b981"},
    "installed":  {"bg": "#eff6ff", "border": "#bfdbfe", "text": "已安装·未启动", "color": "#3b82f6"},
    # configured 已合并到 missing — 留映射避免历史代码 KeyError
    "configured": {"bg": "#f9fafb", "border": "#e5e7eb", "text": "未安装", "color": "#9ca3af"},
    "missing":    {"bg": "#f9fafb", "border": "#e5e7eb", "text": "未安装",       "color": "#9ca3af"},
}


def open_install_dialog(ui: Any, h: Any, *, on_after_install: Optional[Any] = None) -> None:
    """打开框架安装弹窗(2 阶段加载 — 55 题)。

    阶段 1(同步,毫秒级):
      * ui.notify spinner toast 立即反馈
      * 立即 mount **placeholder dialog**(spinner + "加载中"),立即 open 给用户看
      * ui.timer(0.05) 调度真实 mount

    阶段 2(异步,timer 触发):
      * 关闭 placeholder
      * 构造真实 dialog 主体(yaml 编辑器 / 日志 / 部署按钮 / ...)— 850 行 mount
        在主线程同步执行,但已脱离 click handler,asyncio loop 在调度间隙能 flush WS

    解决:刚启动 + 首次 click 模型配置 / docker 卡片仍 connection lost 问题 —
    主因是子进程 spawn 后首次 mount 大块 DOM + 同步 IO 累计 5-15 秒,卡死 WS。
    """
    label = h.spec.label
    framework = h.spec.name

    # 立即 toast 让用户看到点击被接收
    try:
        ui.notify(
            f"正在打开 {label} 配置弹窗...",
            type="ongoing", timeout=1500, spinner=True,
        )
    except Exception:  # noqa: BLE001
        pass

    # 55 题阶段 1:立即 mount **placeholder dialog**(spinner)— 用户瞬间
    # 看到对话框,后续真实内容由 ui.timer 异步填(避免 WS 心跳超时)。
    placeholder_dialog = ui.dialog().props("persistent=false")
    with placeholder_dialog:
        with ui.card().style(
            "min-width: 480px; padding: 36px; "
            "display: flex; flex-direction: column; align-items: center;",
        ):
            ui.spinner("dots", size="lg", color="primary")
            ui.label(f"正在加载 {label} 安装弹窗...").classes(
                "q-mt-md text-grey-7",
            ).style("font-size: 14px;")
            ui.label(
                "正在准备 yaml / 探活 / 构造 UI 元素...",
            ).classes("q-mt-xs text-caption text-grey-6")
    placeholder_dialog.open()

    def _real_open() -> None:
        """阶段 2:0.05s 后真实构造 dialog 主体(已脱离 click handler)。"""
        try:
            placeholder_dialog.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            placeholder_dialog.delete()
        except Exception:  # noqa: BLE001
            pass
        try:
            _build_install_dialog_body(ui, h, on_after_install)
        except Exception as e:  # noqa: BLE001
            logger.exception("[install_dialog] build body failed: %s", e)
            try:
                ui.notify(
                    f"打开 {label} 失败: {type(e).__name__}: {e}",
                    type="negative", timeout=8000,
                )
            except Exception:  # noqa: BLE001
                pass

    ui.timer(0.05, _real_open, once=True)


def _build_install_dialog_body(ui: Any, h: Any, on_after_install: Optional[Any] = None) -> None:
    """55 题 — install_dialog 真实主体构造(原 open_install_dialog 内容)。

    脱离 click handler 后由 ui.timer 调度,主线程仍同步 mount 但 WS 心跳
    在调度间隙有机会 flush。
    """
    framework = h.spec.name
    label = h.spec.label
    manager = get_install_manager()

    # 当前选中的 recipe / 自定义命令
    state: Dict[str, Any] = {
        "selected_idx": 0,                         # 当前选中 recipe 在 recipes 中的下标
        "custom_cmd": "",
        "task_id": (manager.attach(framework).task_id
                    if manager.attach(framework) else ""),
    }

    # 52 题:每个 framework 只显示 1 种安装方式(docker 优先, pip fallback)。
    # 用户已表态:有 docker 就只 docker,没有就 pip,不再让用户在多个之间纠结。
    # 仍保留 ``get_recipes`` 老接口给"显示更多镜像源"等高级场景。
    recipes: List[InstallRecipe] = get_recipes_simplified(framework)
    if not recipes:
        # framework 缺自动安装 recipe → fallback 单个手工 recipe
        recipes = [InstallRecipe(
            label="(暂无自动安装)",
            cmd=["echo", f"{framework} 暂不支持自动安装,请手动操作"],
            note="占位 recipe;请填自定义命令或参考文档",
        )]

    # **按可用性过滤 chip** — 用户没装 docker 就不显示 docker recipe;
    # 没装 git 就不显示 git clone — 让选项清爽,不让用户瞎选导致失败
    # 但保留至少 1 个 — 都不可用时让用户看到"全 unavailable"信息引导装基础工具
    def _recipe_available(rec: InstallRecipe) -> bool:
        # 自定义命令永远显示
        if rec.label == "自定义命令":
            return True
        # 占位 recipe 也显示(让用户知道暂无自动)
        if rec.label.startswith("(暂无"):
            return True
        # 显式 requires 字段(常规情况)
        if rec.requires:
            return shutil.which(rec.requires) is not None
        # cmd 第一个 token 是已知工具时(docker/git/pip)— 走 PATH 检查
        if rec.cmd:
            first = rec.cmd[0]
            if first in ("docker", "git", "pip", "pip3", "poetry"):
                return shutil.which(first) is not None
        return True  # 无 requires 又无显式工具名 → 假定可用

    available_recipes = [r for r in recipes if _recipe_available(r)]
    # 全部不可用 — 退化:保留全部 + 在 banner 提示
    no_recipe_runnable = (not available_recipes)
    if no_recipe_runnable:
        available_recipes = list(recipes)

    # 加 "自定义" 一项作为最后
    recipes_with_custom: List[InstallRecipe] = list(available_recipes) + [
        InstallRecipe(label="自定义命令", cmd=[], note="在下方输入框填写"),
    ]

    # 弹窗本体
    with ui.dialog().props("persistent=false") as dialog:
        dialog.props("backdrop-filter")
        with ui.card().style(
            "width: 720px; height: 740px; "  # +100px 给配置编辑器折叠区
            "padding: 0; gap: 0; "
            "display: flex; flex-direction: column;"
        ):
            # ============== 顶栏 (标题 + 右上关闭) ==============
            with ui.row().classes("w-full items-center no-wrap").style(
                "padding: 12px 16px; border-bottom: 1px solid #e5e7eb; flex: 0 0 auto;"
            ):
                ui.label(f"{label} · 安装与配置").style(
                    "font-size: 16px; font-weight: 600; flex: 1;"
                )
                ui.button(icon="close", on_click=dialog.close).props(
                    "dense flat round"
                ).style("color: #6b7280;")

            # ============== 状态行 ==============
            tone = _HEALTH_TONE.get(h.state, _HEALTH_TONE["missing"])
            with ui.row().classes("items-center w-full no-wrap").style(
                f"padding: 8px 16px; gap: 8px; flex: 0 0 auto;"
                f" background: {tone['bg']}; border-bottom: 1px solid {tone['border']};"
            ):
                ui.html(
                    f'<span style="display:inline-block;width:10px;height:10px;'
                    f'border-radius:50%;background:{tone["color"]};"></span>'
                )
                ui.label(tone["text"]).style(
                    f"color: {tone['color']}; font-weight: 600; font-size: 12px;"
                )
                if h.url:
                    ui.label(f"URL: {h.url}").style(
                        "color: #6b7280; font-size: 11px; "
                        "font-family: ui-monospace, monospace;"
                    )
                if h.bin_path:
                    ui.label(f"binary: {h.bin_path}").style(
                        "color: #6b7280; font-size: 11px; "
                        "font-family: ui-monospace, monospace;"
                    )

            # ============== 镜像/官方下架引导 (若有) ==============
            # 检查所有 recipes 中是否有 image 命中 IMAGE_REWRITES,如果有
            # 在镜像源 chip 之前显示一个引导 banner,告诉用户为什么要走替代镜像
            _rewrites_seen = []
            for rec in recipes:
                for token in rec.cmd:
                    base = token.split(":", 1)[0]
                    if base in IMAGE_REWRITES:
                        _rewrites_seen.append((base, IMAGE_REWRITES[base]))
            if _rewrites_seen:
                base, rw = _rewrites_seen[0]
                with ui.row().classes("w-full no-wrap items-start").style(
                    "padding: 8px 16px 0 16px; gap: 8px; flex: 0 0 auto;"
                ):
                    ui.icon("info", size="16px").style(
                        "color: var(--cy-warning-600, #d97706); margin-top: 1px;"
                    )
                    with ui.column().classes("min-w-0").style("gap: 2px; flex: 1;"):
                        ui.label(f"原镜像 {base} 不可用").style(
                            "font-size: 12px; font-weight: 600; "
                            "color: var(--cy-warning-700, #b45309);"
                        )
                        ui.label(rw.get("reason", "")).style(
                            "font-size: 11px; color: var(--cy-text-secondary, #52525b);"
                        )
                        if rw.get("search_hint"):
                            ui.html(
                                f'<a href="{rw["search_hint"]}" target="_blank" '
                                f'rel="noopener noreferrer" '
                                f'style="font-size: 11px; '
                                f'color: var(--cy-brand-600, #2563eb);">'
                                f'如何搜替代镜像 →</a>'
                            )

            # ============== 镜像源 chip 选择 ==============
            note_label: Any = None  # 引用,在切换时更新
            chip_row = ui.row().classes("items-center q-mt-sm").style(
                "padding: 0 16px; gap: 6px; flex-wrap: wrap; flex: 0 0 auto;"
            )
            chip_widgets: List[Any] = []

            # 注入 chip 高亮 CSS — 切换样式用 .classes() 比 .props() 安全得多,
            # 不会触发 Quasar 内部状态重算 → 不会触发 WS 抖动 → 不会页面重载
            ui.add_head_html(
                """
                <style>
                .recipe-chip {
                    font-size: 11px; min-height: 24px;
                    padding: 2px 10px; border-radius: 12px;
                    border: 1px solid #d6dbe6;
                    background: #ffffff; color: #1f2a44;
                    transition: background 0.12s, border-color 0.12s;
                }
                .recipe-chip:hover { background: #f1f5f9; }
                .recipe-chip.recipe-chip-active {
                    background: #2563eb !important;
                    color: #ffffff !important;
                    border-color: #2563eb !important;
                }
                </style>
                """
            )

            def _set_recipe(idx: int) -> None:
                """切 recipe — 整段 try/except 终极兜底,任何异常都不让 page 崩。

                关键设计:用 ``classes()`` 切换 CSS class(纯 DOM 属性),不用
                ``props()``(会触发 Quasar 内部 reactive 重算)。前者只 patch
                一个 attr,后者会引发整个 component re-render — 后者在 10+ chip
                同时切换时偶发让 NiceGUI WS 雪崩,客户端重连,主页面 reload。
                """
                try:
                    state["selected_idx"] = idx
                    rec = recipes_with_custom[idx]
                    # 高亮切换 — 用 classes(),单条失败不影响其它
                    for i, w in enumerate(chip_widgets):
                        try:
                            if i == idx:
                                w.classes(add="recipe-chip-active")
                            else:
                                w.classes(remove="recipe-chip-active")
                        except Exception as e:  # noqa: BLE001
                            logger.debug("chip[%s] class update failed: %r", i, e)
                    try:
                        if note_label is not None:
                            note_label.set_text(rec.note or " ")
                    except Exception as e:  # noqa: BLE001
                        logger.debug("note_label update failed: %r", e)
                    try:
                        custom_input.visible = (idx == len(recipes_with_custom) - 1)
                    except Exception:  # noqa: BLE001
                        pass
                except Exception as e:  # noqa: BLE001
                    logger.exception("_set_recipe(%s) failed: %r", idx, e)

            with chip_row:
                for i, rec in enumerate(recipes_with_custom):
                    # ui.button + recipe-chip CSS class — 切换样式只改一个 class,
                    # 不再触发 Quasar 内部 props 链重算
                    btn = ui.button(rec.label).props(
                        "flat dense size=sm no-caps"
                    ).classes("recipe-chip")
                    btn.on("click", lambda i_=i: _set_recipe(i_))
                    chip_widgets.append(btn)

            note_label = ui.label(" ").style(
                "padding: 4px 16px; color: #6b7280; font-size: 11px; flex: 0 0 auto;"
            )

            # 自定义命令输入框
            custom_input = ui.input(
                placeholder="自定义安装命令, 如 brew install ollama"
            ).props("dense outlined").style(
                "margin: 4px 16px; flex: 0 0 auto;"
            )
            custom_input.visible = False

            # ============== 一键安装 / 终止 按钮 ==============
            ctrl_row = ui.row().classes("items-center w-full no-wrap").style(
                "padding: 8px 16px; gap: 10px; "
                "border-top: 1px solid #e5e7eb; "
                "border-bottom: 1px solid #e5e7eb; flex: 0 0 auto;"
            )
            install_btn = None
            cancel_btn = None
            task_label: Any = None

            def _check_dependency(rec: InstallRecipe) -> Optional[str]:
                """返回缺失依赖名;None 表示 OK。"""
                if rec.requires and not shutil.which(rec.requires):
                    return rec.requires
                return None

            def _start_install() -> None:
                idx = state["selected_idx"]
                rec = recipes_with_custom[idx]
                if idx == len(recipes_with_custom) - 1:
                    cmd = (state["custom_cmd"] or custom_input.value or "").strip()
                    if not cmd:
                        ui.notify("请先填写自定义命令", type="warning")
                        return
                    rec = custom_recipe(framework, cmd)
                missing = _check_dependency(rec)
                if missing:
                    ui.notify(f"缺少依赖: {missing} 不在 PATH 中", type="negative")
                    return
                task = manager.start(framework=framework, recipe=rec)
                state["task_id"] = task.task_id
                ui.notify(f"{label} 安装已开始(后台进行)", type="info")
                _refresh_view()

            def _cancel_install() -> None:
                tid = state["task_id"]
                if not tid:
                    return
                ok = manager.cancel(tid)
                if ok:
                    ui.notify("已发送终止信号", type="warning")
                else:
                    ui.notify("任务不可终止(可能已结束)", type="info")

            def _start_repair() -> None:
                """🔧 一键修复 — 把当前任务的日志喂给 install_diagnose.py,
                以非交互模式运行修复命令。所有输出走当前弹窗的日志区。"""
                tid = state["task_id"]
                if not tid:
                    ui.notify("当前没有任务,无法诊断", type="warning")
                    return
                cur = manager.get(tid)
                if not cur or not cur.log:
                    ui.notify("当前任务无日志可分析", type="warning")
                    return
                if not cur.is_terminal():
                    ui.notify("等当前任务结束后再点修复", type="warning")
                    return

                # 先用 diagnose --json 探一下看有没有匹配规则,没匹配就别白跑
                diag_script = _find_diagnose_script()
                if not diag_script:
                    ui.notify("找不到 install_diagnose.py", type="negative")
                    return
                log_text = "\n".join(cur.log)
                try:
                    probe = subprocess.run(
                        [sys.executable, str(diag_script), "--json", "--stdin"],
                        input=log_text, capture_output=True, text=True, timeout=15,
                    )
                    info = (
                        json.loads(probe.stdout)
                        if probe.stdout and probe.returncode in (0, 1)
                        else {}
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("diagnose --json 失败: %r", e)
                    info = {}

                kind = info.get("kind", "unknown")
                if kind == "unknown" or not info.get("fixes"):
                    ui.notify(
                        "未识别到已知失败模式 — 请把日志贴给开发组",
                        type="info",
                    )
                    return

                # 把日志写到临时文件,作为 diagnose 子进程的输入
                # (用 stdin 也行,但 InstallRecipe 不支持注入 stdin,改用临时文件)
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".log", delete=False, encoding="utf-8",
                )
                tmp.write(log_text)
                tmp.close()

                fix_count = sum(1 for f in info["fixes"] if not f.get("optional"))
                summary = info.get("summary", kind)

                # 构造一个新的 install task 跑 diagnose --auto-fix-yes
                # 这样自动拿到日志流 / 终止 / 状态轮转 等已有能力
                repair_recipe = InstallRecipe(
                    label=f"🔧 一键修复 · {kind}",
                    cmd=[
                        sys.executable, str(diag_script),
                        tmp.name, "--auto-fix-yes",
                    ],
                    note=f"识别为「{summary}」,执行 {fix_count} 个非可选修复命令",
                )
                new_task = manager.start(
                    framework=f"{framework}::repair", recipe=repair_recipe,
                )
                state["task_id"] = new_task.task_id
                ui.notify(
                    f"识别为「{summary}」,开始执行 {fix_count} 个修复命令...",
                    type="info",
                )
                _refresh_view()

            # ============== 服务启停按钮(独立于安装) ==============
            # 设计:把"安装"和"运行服务"的概念分开,避免用户被"终止"吓退
            #   * 安装区: ▶ 一键安装(下载/构建)+ ■ 终止安装(撤销当前安装任务)
            #   * 服务区: ▶ 启动服务(已安装,起 daemon)+ ⏹ 停止服务(关 daemon)
            # 服务区独立动作,不会影响安装任务

            def _recipe_runnable(rec: InstallRecipe) -> Tuple[bool, str]:
                """判断一个 recipe 现在是否能成功启动。

                返回 (ok, reason_if_not):
                  - ok=True: 当前环境可执行
                  - ok=False: reason 是中文短句,显示给用户

                探测点:
                  1. requires 命令必须在 PATH(基础)
                  2. ``docker start <name>``  → 检查容器存在
                  3. ``docker compose <op>``  → 检查 docker compose 可用 + service 在 yaml
                  4. ``bash -lc nohup <bin>`` → 检查 bin 在 PATH
                """
                # 1. requires 字段(基础依赖)
                if rec.requires and not shutil.which(rec.requires):
                    return False, f"PATH 中找不到 {rec.requires}"
                # 2. docker start <name> 模式 — 验证容器实际存在
                if (
                    len(rec.cmd) >= 3
                    and rec.cmd[0] == "docker"
                    and rec.cmd[1] == "start"
                ):
                    container_name = rec.cmd[2]
                    try:
                        # 43 题 P0.D:timeout 4.0 → 1.5 秒。docker ps 在 daemon 健康
                        # 时 < 100ms;daemon 慢时早失败,回 "稍后重试" 比卡 4 秒强 —
                        # 大幅降低多 recipe 串行探活总耗时(N×4s → N×1.5s)。
                        out = subprocess.run(  # noqa: S603
                            ["docker", "ps", "-a", "--filter",
                             f"name=^{container_name}$",
                             "--format", "{{.Names}}"],
                            capture_output=True, text=True, timeout=1.5,
                            check=False,
                        )
                        if out.returncode != 0 or not out.stdout.strip():
                            return False, f"docker 中没有名为 {container_name} 的容器"
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        return False, "docker 命令不可用或超时(daemon 慢,稍后重试)"
                # 3. docker compose <op> <service> 模式 — 验证 compose 可用 + service 在 yaml
                if (
                    len(rec.cmd) >= 2
                    and rec.cmd[0] == "docker"
                    and rec.cmd[1] == "compose"
                ):
                    if not shutil.which("docker"):
                        return False, "docker 不在 PATH"
                    # service 名是 cmd 末尾的 token
                    service = rec.cmd[-1] if rec.cmd else ""
                    try:
                        from chayuan.server.config_panel.compose_manager import (
                            get_service_definition,
                        )
                        if service and not get_service_definition(service):
                            return False, f"compose yaml 里未定义 service: {service}"
                    except Exception:  # noqa: BLE001
                        pass  # compose_manager 不可用就跳过此层校验
                # 3. bash -lc "nohup <bin> ..." 模式 — 抽 bin 名验证
                if (
                    len(rec.cmd) >= 3
                    and rec.cmd[0] == "bash"
                    and rec.cmd[1] in ("-lc", "-c")
                ):
                    shell_cmd = rec.cmd[2]
                    # 简单提取 nohup 后的第一个非选项 token
                    import re as _re
                    m = _re.search(r"nohup\s+(\S+)", shell_cmd)
                    if m:
                        bin_name = m.group(1)
                        # 排除已经是绝对路径或 python 模块调用
                        if (
                            "/" not in bin_name
                            and bin_name not in ("python", "python3", "sudo")
                            and not shutil.which(bin_name)
                        ):
                            return False, f"PATH 中找不到 {bin_name}(可能未安装)"
                return True, ""

            def _start_service() -> None:
                """启动服务 daemon — 智能选择能跑的 recipe(pip / docker / 源码 自动探测)。

                优先级:
                  1. 只有一个 recipe 能跑 → 直接执行
                  2. 多个 recipe 都能跑 → 默认选 list 中第一个(主 recipe);
                     若用户想用其它,弹菜单选
                  3. 没有 recipe 能跑 → 弹诊断窗,告诉用户每个 recipe 失败的原因
                """
                from chayuan.server.config_panel.install_task_manager import (
                    get_install_manager, get_start_recipes,
                )
                mgr = get_install_manager()
                recipes = get_start_recipes(framework)
                if not recipes:
                    task = mgr.start_service(framework=framework)
                    if task is None:
                        ui.notify(
                            f"{label} 没有启动配方;请用 [安装] 重新装",
                            type="warning",
                        )
                        return
                    state["task_id"] = task.task_id
                    ui.notify(f"已启动 {label}(后台)", type="positive")
                    _refresh_view()
                    return

                # 探测每个 recipe 能不能跑
                runnable: List[InstallRecipe] = []
                blocked: List[Tuple[InstallRecipe, str]] = []
                for r in recipes:
                    ok, reason = _recipe_runnable(r)
                    if ok:
                        runnable.append(r)
                    else:
                        blocked.append((r, reason))

                if len(runnable) == 1:
                    rec = runnable[0]
                    task = mgr.start(framework=framework, recipe=rec)
                    state["task_id"] = task.task_id
                    ui.notify(
                        f"已用「{rec.label}」启动 {label}",
                        type="positive",
                    )
                    _refresh_view()
                    return

                if len(runnable) >= 2:
                    # 多个都能跑 — 直接用主 recipe(list[0]),用户想换可手工选
                    rec = runnable[0]
                    task = mgr.start(framework=framework, recipe=rec)
                    state["task_id"] = task.task_id
                    ui.notify(
                        f"已自动选「{rec.label}」启动({len(runnable)} 种方式可用)",
                        type="positive",
                    )
                    _refresh_view()
                    return

                # runnable 为空 — 没有 recipe 能跑,弹诊断 dialog
                with ui.dialog().props("persistent") as diag_dlg:
                    with ui.card().style("min-width: 480px; max-width: 560px;"):
                        ui.label("无法启动 — 没有可执行的启动方式").style(
                            "font-size: 14px; font-weight: 600; color: #b91c1c;"
                        )
                        ui.label(
                            f"{label} 有 {len(blocked)} 种启动配方,但当前环境都不满足条件:"
                        ).classes("text-caption text-grey-7").style("margin-top: 4px;")
                        with ui.column().style("gap: 6px; margin-top: 8px;"):
                            for r, why in blocked:
                                with ui.row().classes("items-start no-wrap").style(
                                    "gap: 8px;"
                                ):
                                    ui.icon("close", size="14px").classes("text-negative").style(
                                        "margin-top: 2px;"
                                    )
                                    with ui.column().style("gap: 1px; flex: 1;"):
                                        ui.label(r.label).style(
                                            "font-size: 12px; font-weight: 500;"
                                        )
                                        ui.label(why).classes(
                                            "text-caption text-grey-6"
                                        ).style("font-size: 11px;")
                        ui.label(
                            "💡 通常是没装(点 [安装])或装了别的方式而不是探测到的方式。"
                        ).classes("text-caption").style(
                            "font-size: 11px; color: #1d4ed8; "
                            "background: #eff6ff; padding: 6px 8px; "
                            "border-radius: 4px; margin-top: 8px;"
                        )
                        with ui.row().classes("w-full justify-end").style(
                            "margin-top: 12px; gap: 8px;"
                        ):
                            ui.button("关闭", on_click=diag_dlg.close).props(
                                "dense flat"
                            )
                diag_dlg.open()

            def _stop_service() -> None:
                """停止服务 daemon。"""
                from chayuan.server.config_panel.install_task_manager import (
                    get_install_manager,
                )
                mgr = get_install_manager()
                task = mgr.stop_service(framework=framework)
                if task is None:
                    ui.notify(f"{label} 没有停止配方", type="warning")
                    return
                state["task_id"] = task.task_id
                ui.notify(f"已停止 {label}", type="info")
                _refresh_view()

            # 按钮统一用 size=sm + dense + 4 字以内的标签,
            # 视觉密度从原来 5 个大按钮(每个 ~80px 宽)压到 ~50px,
            # 同时按状态机隐藏不可用按钮(.visible),避免用户面对一排灰色 disable
            with ctrl_row:
                install_btn = ui.button(
                    "安装", icon="play_arrow", on_click=_start_install,
                ).props("unelevated dense size=sm color=primary").tooltip(
                    "下载 / 构建 / pip install"
                )
                # "重装"是 install 的同等动作,只是文案上对已装客户更准确;两个共用同一 _start_install
                reinstall_btn = ui.button(
                    "重装", icon="refresh", on_click=_start_install,
                ).props("flat dense size=sm color=primary").tooltip(
                    "重新跑安装命令(已装好可重装升级)"
                )
                cancel_btn = ui.button(
                    "终止", icon="stop", on_click=_cancel_install,
                ).props("flat dense size=sm color=negative").tooltip(
                    "终止当前安装任务(不卸载已装的部分)"
                )
                start_svc_btn = ui.button(
                    "启动", icon="play_circle", on_click=_start_service,
                ).props("unelevated dense size=sm color=positive").tooltip(
                    "起 daemon(已安装好的服务)"
                )
                stop_svc_btn = ui.button(
                    "停止", icon="stop_circle", on_click=_stop_service,
                ).props("flat dense size=sm color=warning").tooltip(
                    "关 daemon(不卸载)"
                )
                repair_btn = ui.button(
                    "修复", icon="healing", on_click=_start_repair,
                ).props("flat dense size=sm color=primary").tooltip(
                    "分析失败日志,自动执行修复命令"
                )
                ui.space()
                task_label = ui.label("").style(
                    "color: #6b7280; font-size: 11px;"
                )

            # ============== 失败诊断提示行(条件显示) ==============
            # 任务失败时调 install_diagnose.py --json 探测错误模式;
            # 命中已知模式 → 显示"💡 看起来是 X — 点 [🔧 修复] 自动处理"
            # 引导用户去点修复按钮,而不是对着 200 行栈猜原因
            hint_row = ui.row().classes("w-full items-center no-wrap").style(
                "padding: 6px 16px; gap: 8px; "
                "background: #fffbeb; border-bottom: 1px solid #fde68a; "
                "flex: 0 0 auto;"
            )
            hint_row.visible = False
            with hint_row:
                ui.icon("info", size="16px").classes("text-amber-7")
                hint_label = ui.label("").style(
                    "font-size: 12px; color: #92400e; flex: 1;"
                )
                # 修复按钮:click → 调上面定义的 _start_repair
                ui.button(
                    "🔧 立即修复", on_click=_start_repair,
                ).props("dense size=sm color=primary unelevated").style(
                    "flex: 0 0 auto;"
                )

            # ============== Tab 切换器(日志 / 配置)— 必须在 cfg_panel 和 log_container 之前 ==============
            # 引用占位:实际 _set_tab/log_tab_handle 在 cfg_panel 和 log_container 都创建后绑定
            log_tab_handle: Dict[str, Any] = {}
            # 上一次诊断的 task_id,避免每次 timer 都重跑 diagnose
            diag_state: Dict[str, Any] = {"last_failed_tid": "", "kind": ""}

            # 54-B:**移除独立 "🚀 部署" tab**,简化为 2 个 tab 即可:
            #   配置 tab:yaml 编辑器(顶部带 ▶ 启动 / ⏹ 停止 按钮直接 docker compose up/stop)
            #   日志 tab:现有 install_task_manager 任务日志 + docker 部署日志归并显示
            _is_docker_for_tab = _is_docker_framework(h.spec)
            with ui.row().classes("w-full items-center no-wrap").style(
                "padding: 4px 16px; gap: 4px; border-bottom: 1px solid #e5e7eb; "
                "background: #f9fafb; flex: 0 0 auto;"
            ):
                log_tab_btn = ui.button("📋 日志").props("flat dense size=sm")
                cfg_tab_btn = ui.button("⚙ 配置").props("flat dense size=sm")
                # 87 题:docker 类 framework 在顶栏加 "📡 容器日志" 按钮,
                # 点击拉取该服务的 docker compose logs --tail 100 追加到日志区,
                # 并自动切到日志 tab。已启动时一目了然查看容器输出。
                docker_logs_btn = None
                if _is_docker_for_tab:
                    docker_logs_btn = ui.button(
                        "📡 容器日志", icon="terminal",
                    ).props("flat dense size=sm color=secondary").tooltip(
                        "拉 docker compose logs --tail 100,运行中容器才有内容"
                    )
                ui.space()
                tab_status_label = ui.label("").style(
                    "font-size: 11px; color: #6b7280;"
                )

            # ============== 配置文件编辑器(由 tab 控制显隐,默认隐藏) ==============
            # 43 题 P0.C + 83 题:**docker 类 framework**(vllm/infinity/comfyui/llamacpp)
            # 走该 service 自己的 yaml(``<CHAYUAN_ROOT>/compose/<framework>.yaml``);
            # modality wrapper 类(funasr/cosyvoice/...)走 runtime/yaml。
            # 注意:必须传 framework 进 ``_compose_config_path``,否则会回到全局聚合
            # ``docker-compose.yaml``,而那个文件 83 题后已不再是 service 真源。
            _is_docker = _is_docker_framework(h.spec)
            if _is_docker:
                cfg_path = _compose_config_path(framework)
            else:
                cfg_path = _runtime_config_path(framework)

            # 54-B:docker 类 inline 部署按钮通过共享 ref 拿 log_html / _set_tab。
            # cfg_panel 内 mount 按钮时这俩还没创建 — 用 dict ref 延迟填充,
            # 用户点击 click handler 时(那时已创建)读 ref 即可。
            _docker_log_ref: Dict[str, Any] = {"log_html": None, "log_tab_select": None}

            with ui.column().classes("w-full").style(
                "flex: 1 1 auto; min-height: 0; overflow-y: auto;"
            ) as cfg_panel:
                if cfg_path is None:
                    ui.label(
                        f"无独立 yaml(由 {label} 自身配置体系管理,请参考官方文档)"
                    ).style("padding: 8px 16px; color: #9ca3af; font-size: 11px;")
                else:
                    if _is_docker:
                        # 87 题:配置 tab 只显示**配置文件本身**,不再有:
                        #   * inline 启动/停止按钮(顶栏已有,统一入口)
                        #   * 镜像源切换 chip(改 yaml 里 image: 字段即可)
                        #   * "docker compose 编排文件" 提示文字
                        # 改 yaml 后点顶栏 ▶ 启动 即可重起对应 service。
                        with ui.row().classes("w-full items-center").style(
                            "padding: 0 16px 6px 16px; gap: 6px;"
                        ):
                            ui.icon("info", size="14px").classes("text-blue-7")
                            ui.label(
                                f"独立 yaml(只这一服务);改后点顶栏 ▶ 启动 重起 [{framework}],"
                                f"chayuan 自身不必重启。镜像源直接改 yaml 里 image: 字段。"
                            ).style("color: #2563eb; font-size: 11px;")

                    cfg_path_str = str(cfg_path)
                    # 顶部一行:路径 + 保存并重启
                    with ui.row().classes("w-full items-center no-wrap").style(
                        "padding: 6px 16px; gap: 8px;"
                    ):
                        ui.icon("description", size="14px").classes("text-grey-7")
                        ui.label(cfg_path_str).style(
                            "color: #6b7280; font-size: 11px; "
                            "font-family: ui-monospace, monospace; "
                            "flex: 1; overflow: hidden; text-overflow: ellipsis;"
                        )
                        save_btn_holder: Dict[str, Any] = {}

                    # 编辑器区(textarea)
                    initial_text = ""
                    try:
                        if cfg_path.exists():
                            initial_text = cfg_path.read_text(encoding="utf-8")
                        else:
                            # 首次:从 wrapper 默认值合成一份
                            initial_text = _bootstrap_default_yaml(framework)
                    except Exception as e:  # noqa: BLE001
                        initial_text = f"# 读取配置失败: {e}\n"

                    # 54-C:用 ui.codemirror 替代 ui.textarea — 支持 yaml 语法高亮、
                    # 缩进辅助、行号。NiceGUI 1.4+ 自带,fallback 到 textarea(老版兼容)。
                    try:
                        cfg_textarea = ui.codemirror(
                            value=initial_text,
                            language="yaml",
                            line_wrapping=True,
                        ).style(
                            "margin: 4px 16px 8px 16px; "
                            "font-family: ui-monospace, monospace; font-size: 12px; "
                            "min-height: 220px;"
                        ).classes("w-full")
                    except Exception:  # noqa: BLE001 — NiceGUI 老版没 codemirror
                        cfg_textarea = ui.textarea(value=initial_text).props(
                            "outlined dense input-class=text-mono"
                        ).style(
                            "margin: 4px 16px 8px 16px; "
                            "font-family: ui-monospace, monospace; font-size: 11px; "
                            "min-height: 140px;"
                        ).classes("w-full")

                    cfg_status = ui.label("").style(
                        "padding: 0 16px 6px 16px; font-size: 11px; color: #6b7280;"
                    )

                    def _save_and_restart() -> None:
                        """写盘 → 停 → 启 → 探活提示。"""
                        text = cfg_textarea.value or ""
                        # 简单 yaml 语法校验,避免写入坏文件后 daemon 启不来
                        try:
                            import yaml
                            parsed = yaml.safe_load(text)
                            if not isinstance(parsed, dict):
                                cfg_status.set_text("✗ yaml 顶层必须是 mapping(键值对),保存中止")
                                cfg_status.style("color: #dc2626;")
                                return
                        except Exception as e:  # noqa: BLE001
                            cfg_status.set_text(f"✗ yaml 解析失败: {e}")
                            cfg_status.style("color: #dc2626;")
                            return
                        # 写盘
                        try:
                            cfg_path.parent.mkdir(parents=True, exist_ok=True)
                            cfg_path.write_text(text, encoding="utf-8")
                        except Exception as e:  # noqa: BLE001
                            cfg_status.set_text(f"✗ 写文件失败: {e}")
                            cfg_status.style("color: #dc2626;")
                            return
                        # 停 daemon(可能还没起 — 不当错)
                        try:
                            manager.stop_service(framework=framework)
                        except Exception:  # noqa: BLE001
                            pass
                        # 启 daemon
                        task = None
                        try:
                            task = manager.start_service(framework=framework)
                        except Exception as e:  # noqa: BLE001
                            cfg_status.set_text(f"✗ 启动失败: {e}")
                            cfg_status.style("color: #dc2626;")
                            return
                        if task is None:
                            cfg_status.set_text(
                                "✓ 已写入,但当前 framework 无启动配方,请用上方"
                                "▶ 一键安装重启"
                            )
                            cfg_status.style("color: #f59e0b;")
                            return
                        cfg_status.set_text(
                            f"✓ 配置已保存,daemon 已重启(端口 "
                            f"{_MANAGED_CONFIG_FRAMEWORKS.get(framework, '?')});"
                            f"等 1-2s 健康指示灯转绿"
                        )
                        cfg_status.style("color: #10b981;")
                        # 通知外部刷新卡片状态
                        if on_after_install:
                            try:
                                on_after_install()
                            except Exception:  # noqa: BLE001
                                pass

                    with ui.row().classes("w-full items-center no-wrap").style(
                        "padding: 0 16px 8px 16px; gap: 8px;"
                    ):
                        ui.button(
                            "💾 保存并重启", on_click=_save_and_restart,
                        ).props("unelevated color=primary dense size=sm")
                        ui.button(
                            "↺ 重置默认", on_click=lambda: cfg_textarea.set_value(
                                _bootstrap_default_yaml(framework)
                            ),
                        ).props("flat dense size=sm")

            # ============== 日志区(scroll_area,由 tab 控制显隐) ==============
            log_container = ui.scroll_area().classes("w-full").style(
                "flex: 1 1 auto; min-height: 0; "
                "background: #0a0a0a; color: #e5e7eb; "
                "font-family: ui-monospace, monospace; font-size: 11px; "
                "padding: 8px 12px;"
            )
            log_html = ui.html("").classes("w-full")
            log_container.clear()
            with log_container:
                log_html = ui.html("").style("white-space: pre-wrap;")

            # 54-B:不再独立 deploy tab。docker 类的"启动/停止"按钮已在配置 tab
            # yaml 编辑器顶部(_render_docker_deploy_inline 函数渲染),日志归到 log_container。

            # 绑定 tab 切换逻辑
            def _set_tab(name: str) -> None:
                """切换 tab。name = 'log' | 'config'。"""
                cfg_panel.visible = (name == "config")
                log_container.visible = (name == "log")
                # 按钮高亮
                if name == "log":
                    log_tab_btn.props("unelevated color=primary", remove="flat")
                    cfg_tab_btn.props("flat", remove="unelevated color=primary")
                else:
                    cfg_tab_btn.props("unelevated color=primary", remove="flat")
                    log_tab_btn.props("flat", remove="unelevated color=primary")

            log_tab_btn.on("click", lambda: _set_tab("log"))
            cfg_tab_btn.on("click", lambda: _set_tab("config"))
            log_tab_handle["select"] = _set_tab

            # 87 题:📡 容器日志 — 拉 docker compose -f <yaml> logs --tail 100,
            # 追加到 log_html(同时切到日志 tab 让用户立刻看到)
            if docker_logs_btn is not None:
                async def _load_docker_logs() -> None:
                    import asyncio as _asyncio
                    from chayuan.server.config_panel.compose_manager import (
                        get_compose_file_for_service,
                    )
                    _set_tab("log")
                    yaml_p = None
                    try:
                        yaml_p = get_compose_file_for_service(framework)
                    except Exception:  # noqa: BLE001
                        pass
                    if yaml_p is None or not yaml_p.exists():
                        existing = log_html.content if hasattr(log_html, "content") else ""
                        log_html.set_content(
                            (existing or "")
                            + "\n[容器日志] 没找到该服务的 yaml 文件;"
                            + "请先在 ① 运行时与服务 启动一次。"
                        )
                        return
                    log_html.set_content(
                        (log_html.content if hasattr(log_html, "content") else "")
                        + f"\n$ docker compose -f {yaml_p.name} logs --tail 100 {framework}\n"
                    )

                    def _run_sync() -> Tuple[int, str, str]:
                        import subprocess as _sp
                        try:
                            r = _sp.run(  # noqa: S603
                                ["docker", "compose", "-f", str(yaml_p),
                                 "logs", "--tail", "100", framework],
                                capture_output=True, text=True, timeout=10.0,
                                check=False,
                            )
                            return r.returncode, r.stdout or "", r.stderr or ""
                        except FileNotFoundError:
                            return 127, "", "docker 命令不可用(未安装或不在 PATH)"
                        except _sp.TimeoutExpired:
                            return 124, "", "docker compose logs 超时(10s)"
                        except Exception as e:  # noqa: BLE001
                            return 1, "", f"{type(e).__name__}: {e}"

                    rc, out, err = await _asyncio.to_thread(_run_sync)
                    body = (out + err).strip() or "(容器无输出 / 容器未运行)"
                    # 截断防止超长
                    if len(body) > 50_000:
                        body = body[-50_000:] + "\n...(已截断,只显示尾部 50KB)"
                    log_html.set_content(
                        (log_html.content if hasattr(log_html, "content") else "")
                        + body
                        + (f"\n$ exit {rc}\n" if rc != 0 else "\n")
                    )

                docker_logs_btn.on("click", _load_docker_logs)

            # 54-B:填充 docker 类 inline 按钮的 log ref(此时 log_html / _set_tab 已就绪)
            _docker_log_ref["log_html"] = log_html
            _docker_log_ref["log_tab_select"] = _set_tab

            # 默认显示日志 tab(用户最常看任务日志)
            _set_tab("log")

            # ============== 视图刷新 ==============
            #
            # NOTE: 整个回调用 try/except 包住:用户关掉弹窗后 NiceGUI 客户端
            # 会被销毁,但 ui.timer 可能仍会触发一次回调,此时 set_text /
            # set_content / props 等访问会抛 AttributeError 或触发
            # "Client has been deleted but is still being used" 警告。
            # 静默吞掉,不让用户看到警告日志。
            def _refresh_view() -> None:
                try:
                    _refresh_view_impl()
                except Exception as e:  # noqa: BLE001
                    logger.debug("refresh_view skipped (client likely gone): %s", e)

            def _service_health_state() -> str:
                """探活 framework 的服务态: running / installed / configured / missing。"""
                try:
                    # 55 题:用 get_framework_spec_by_name 替代静态 _FRAMEWORKS_BY_NAME,
                    # 支持用户在 compose/ 加的自定义 service 也能探活。
                    from chayuan.server.config_panel.runtime_framework_panel import (
                        probe_framework, get_framework_spec_by_name,
                    )
                    sp = get_framework_spec_by_name(framework)
                    if not sp:
                        return "missing"
                    return probe_framework(sp).state
                except Exception as e:  # noqa: BLE001
                    logger.debug("probe service state failed: %r", e)
                    return ""

            def _apply_button_visibility(task_state: str = "", svc_state: str = "") -> None:
                """统一的按钮状态机 — 按"任务态 + 服务态"决定每个按钮 visible。

                设计原则:**隐藏不可用,而非 disable** — 视觉清爽,用户不再面对一排灰按钮。

                | 场景               | 安装 | 重装 | 终止 | 启动 | 停止 | 修复 |
                |--------------------|------|------|------|------|------|------|
                | 安装运行中         |      |      |  ✓   |      |      |      |
                | 完全没装(missing) |  ✓   |      |      |      |      |      |
                | 装失败(failed)    |  ✓   |      |      |      |      |  ✓   |
                | 已装未启(installed)|      |  ✓   |      |  ✓   |      |      |
                | 运行中(running)   |      |  ✓   |      |      |  ✓   |      |
                | 启动失败           |      |  ✓   |      |  ✓   |      |  ✓   |
                """
                # 默认全隐藏,按场景打开
                for b in (install_btn, reinstall_btn, cancel_btn,
                          start_svc_btn, stop_svc_btn, repair_btn):
                    b.visible = False

                # 1. 安装/启动任务在跑 → 只显示终止
                if task_state in ("pending", "running"):
                    cancel_btn.visible = True
                    return

                # 2. 任务失败 → 修复 + 重装(对应 install)或重启(对应 service)
                if task_state == "failed":
                    repair_btn.visible = True
                    if svc_state == "running":
                        stop_svc_btn.visible = True
                    elif svc_state in ("installed", "configured"):
                        start_svc_btn.visible = True
                        reinstall_btn.visible = True
                    else:
                        install_btn.visible = True
                    return

                # 3. 无任务/已完成 — 按服务态展示
                if svc_state == "running":
                    stop_svc_btn.visible = True
                    reinstall_btn.visible = True
                elif svc_state in ("installed", "configured"):
                    start_svc_btn.visible = True
                    reinstall_btn.visible = True
                else:  # missing
                    install_btn.visible = True

            def _refresh_view_impl() -> None:
                tid = state["task_id"]
                svc_state = _service_health_state()

                if not tid:
                    if task_label is not None:
                        task_label.set_text("")  # 无任务不显示 task: xxx
                    log_html.set_content("")
                    hint_row.visible = False  # 无任务时藏 hint
                    diag_state["last_failed_tid"] = ""
                    _apply_button_visibility("", svc_state)
                    return
                task = manager.get(tid)
                if task is None:
                    if task_label is not None:
                        task_label.set_text("(任务已过期)")
                    return
                state_zh = {
                    "pending": "等待中", "running": "运行中",
                    "done": "已完成", "failed": "失败", "cancelled": "已终止",
                }.get(task.state, task.state)
                task_label.set_text(
                    f"task: {task.task_id[:8]} · {state_zh} · "
                    f"{task.recipe_label}"
                )
                # 统一状态机:任务态 + 服务态 → 按钮 visible
                _apply_button_visibility(task.state, svc_state)
                # 任务完成回调(仅 done 触发外部刷新)
                if task.is_terminal() and task.state == "done" and on_after_install:
                    try:
                        on_after_install()
                    except Exception:  # noqa: BLE001
                        pass
                # 任务失败 → 切日志 tab + 跑诊断 + 显示提示行
                # 任务非失败 → 隐藏提示行
                if task.is_terminal() and task.state == "failed":
                    try:
                        log_tab_handle["select"]("log")
                    except Exception:  # noqa: BLE001
                        pass
                    # 仅在 task 第一次进入 failed 时跑诊断(避免每 0.8s 重跑)
                    # 43 题 P0.D:**子进程诊断丢线程池**,不再在 ui.timer(0.8s)
                    # 主线程 callback 内同步等 10 秒 — 那一卡足以让 NiceGUI WebSocket
                    # 心跳超时,client 重连 → 用户看到"connection lost"。
                    if diag_state.get("last_failed_tid") != task.task_id:
                        diag_state["last_failed_tid"] = task.task_id
                        diag_state["kind"] = ""
                        diag_script = _find_diagnose_script()
                        if diag_script:
                            log_text = "\n".join(task.log)

                            def _on_diag_done(stdout: str, rc: int) -> None:
                                """子线程完成回调 — 已切回主 asyncio 事件循环。"""
                                try:
                                    if not stdout or rc not in (0, 1):
                                        return
                                    info = json.loads(stdout)
                                    kind = info.get("kind", "unknown")
                                    summary = info.get("summary", "")
                                    if kind != "unknown" and info.get("fixes"):
                                        diag_state["kind"] = kind
                                        hint_label.set_text(
                                            f"识别为「{summary}」 — "
                                            f"点 🔧 立即修复 自动执行 "
                                            f"{len(info['fixes'])} 个修复命令"
                                        )
                                        hint_row.visible = True
                                    else:
                                        hint_label.set_text(
                                            "未识别到已知错误模式 — "
                                            "请查看下方日志了解详情"
                                        )
                                        hint_row.visible = True
                                except Exception as e:  # noqa: BLE001
                                    logger.debug("inline diagnose update failed: %r", e)

                            def _diag_thread_run() -> None:
                                try:
                                    probe = subprocess.run(  # noqa: S603
                                        [sys.executable, str(diag_script),
                                         "--json", "--stdin"],
                                        input=log_text,
                                        capture_output=True, text=True, timeout=10,
                                    )
                                    stdout = probe.stdout or ""
                                    rc = probe.returncode
                                except Exception as e:  # noqa: BLE001
                                    logger.debug("inline diagnose subprocess failed: %r", e)
                                    return
                                # NiceGUI: 用 ui.timer(0, once=True) 把 UI 更新调度回主循环
                                try:
                                    ui.timer(
                                        0.01,
                                        lambda s=stdout, r=rc: _on_diag_done(s, r),
                                        once=True,
                                    )
                                except Exception:  # noqa: BLE001
                                    pass

                            try:
                                import threading
                                threading.Thread(
                                    target=_diag_thread_run,
                                    daemon=True,
                                    name=f"install-diag-{task.task_id[:6]}",
                                ).start()
                            except Exception as e:  # noqa: BLE001
                                logger.debug("spawn diag thread failed: %r", e)
                else:
                    # 任务非 failed(running / done / cancelled) → 隐藏提示行
                    hint_row.visible = False
                    diag_state["last_failed_tid"] = ""
                    diag_state["kind"] = ""
                # 日志(转义防 XSS)
                escaped = "\n".join(task.log).replace("&", "&amp;").replace(
                    "<", "&lt;").replace(">", "&gt;")
                log_html.set_content(escaped)
                # 自动滚到底
                try:
                    log_container.scroll_to(percent=1.0)
                except Exception:  # noqa: BLE001
                    pass

            # 注入定时器(弹窗关闭时 NiceGUI 会自动清理 timer)
            ui.timer(0.8, _refresh_view, active=True)

            # 初始化:默认选第一个 recipe
            _set_recipe(0)
            _refresh_view()
            # 自定义输入实时落到 state
            custom_input.on(
                "update:model-value",
                lambda e: state.update(custom_cmd=str(e.args or "").strip()),
            )

    dialog.open()


__all__ = ["open_install_dialog"]
