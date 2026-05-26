"""项目初始化时一次性生成所有运行时资产 yaml 文件(35 题)。

何时被调用
==========
* ``chayuan init --non-interactive`` 流程末尾(``cli.py``)— 用户首次安装,
  init 完后所有 yaml 已就位,可直接 ``chayuan start``
* 守护进程首次启动时兜底再调一次 — 处理用户跳过 init 直接拷贝 ``CHAYUAN_ROOT``
  目录的场景(例如复制虚拟机镜像)

写入位置
========
* ``<CHAYUAN_ROOT>/compose/docker-compose.yaml`` — 4 个 docker 服务的容器编排
  (vllm / infinity / comfyui / llamacpp)
* ``<CHAYUAN_ROOT>/runtime/<framework>.yaml`` — 5 个 modality wrapper 配置
  (funasr / cosyvoice / voxcpm2 / rapidocr / paddleocr)

幂等性
======
所有写入都是 "不存在则写,存在则跳过" — 多次调用安全,不会覆盖用户已编辑的配置。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.init_assets")


@dataclass
class AssetReport:
    """项目初始化资产生成报告 — 给 CLI banner 用。"""

    compose_file: Optional[Path] = None
    compose_action: str = ""             # "created" / "exists" / "migrated" / "skipped"
    runtime_files: Optional[List[Path]] = None
    runtime_actions: Optional[Dict[str, str]] = None  # framework -> action
    errors: Optional[List[str]] = None

    def __post_init__(self) -> None:
        if self.runtime_files is None:
            self.runtime_files = []
        if self.runtime_actions is None:
            self.runtime_actions = {}
        if self.errors is None:
            self.errors = []

    def has_changes(self) -> bool:
        return (
            self.compose_action in ("created", "migrated")
            or any(a in ("created", "migrated") for a in (self.runtime_actions or {}).values())
        )


# 跑配置生成的 modality wrapper 列表 — 与 _runtime_server_base.make_runtime_app 一致
# 每项: (framework_name, default_config_loader)
def _modality_specs() -> List[tuple[str, Callable[[], Dict[str, Any]]]]:
    """延迟 import — modality wrapper 模块各自带可选依赖,避免初始化时炸。"""
    specs: List[tuple[str, Callable[[], Dict[str, Any]]]] = []

    def _safe_load(mod_path: str, attr: str) -> Callable[[], Dict[str, Any]]:
        def _load() -> Dict[str, Any]:
            mod = __import__(mod_path, fromlist=[attr])
            return getattr(mod, attr)
        return _load

    for name in ("funasr", "cosyvoice", "voxcpm2", "rapidocr", "paddleocr"):
        specs.append((
            name,
            _safe_load(f"chayuan.server.modality.{name}_server", "_DEFAULT_CONFIG"),
        ))
    return specs


def _ensure_runtime_yaml(framework: str, default_cfg: Dict[str, Any]) -> tuple[Path, str]:
    """写一个 modality wrapper 的默认 yaml(存在跳过)。返回 (path, action)。"""
    import yaml  # 延迟 import

    from chayuan.paths import runtime_config_path

    path = runtime_config_path(f"{framework}.yaml")
    if path.exists():
        return path, "exists"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# {framework} wrapper 配置 — 由 chayuan init 自动生成\n"
            "# UI 上 [⚙ 配置] 标签可在线编辑,保存后停->启 daemon 即生效\n\n"
        )
        path.write_text(
            header + yaml.safe_dump(default_cfg, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return path, "created"
    except OSError as e:
        logger.warning("[init_assets] write %s failed: %r", path, e)
        return path, "error"


def init_runtime_assets() -> AssetReport:
    """生成 docker-compose + 5 个 modality wrapper yaml。

    幂等。被 ``chayuan init`` 和守护进程启动时各调一次。
    """
    report = AssetReport()

    # 55 题:首先把 server-compose/*.yaml 复制到 <CHAYUAN_ROOT>/compose/
    # (每服务一个独立 yaml,UI 扫描动态发现)
    try:
        from chayuan.server.config_panel.compose_manager import (
            seed_compose_services_from_templates,
        )
        seed_rpt = seed_compose_services_from_templates()
        if seed_rpt.get("copied", 0) > 0:
            logger.info(
                "[init_assets] server-compose 模板复制 %d 个新文件",
                seed_rpt["copied"],
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[init_assets] seed_compose_services 失败:%r", e)

    # 1) docker-compose.yaml — 专属目录 <CHAYUAN_ROOT>/compose/
    try:
        from chayuan.server.config_panel.compose_manager import (
            ensure_compose_file,
            get_compose_file_path,
        )
        target = get_compose_file_path()
        existed_before = target.exists()
        actual = ensure_compose_file()  # 内部含老路径迁移
        report.compose_file = actual
        if existed_before:
            report.compose_action = "exists"
        elif actual.exists():
            # ensure_compose_file 通过迁移或写默认让文件就位
            # 没法 100% 区分两条路径,简单按 target 是否原本就存在来判断
            report.compose_action = "created"
    except Exception as e:  # noqa: BLE001
        msg = f"compose ensure failed: {type(e).__name__}: {e}"
        logger.warning("[init_assets] %s", msg)
        report.errors.append(msg)

    # 2) modality wrapper yaml — <CHAYUAN_ROOT>/runtime/<framework>.yaml
    for name, loader in _modality_specs():
        try:
            cfg = loader()
            path, action = _ensure_runtime_yaml(name, cfg)
            report.runtime_files.append(path)
            report.runtime_actions[name] = action
        except ImportError as e:
            # wrapper 模块依赖缺失(例如 voxcpm2 还没装)— 跳过,不影响 init
            msg = f"{name}: skip (import failed: {e})"
            logger.info("[init_assets] %s", msg)
            report.runtime_actions[name] = "skipped"
        except Exception as e:  # noqa: BLE001
            msg = f"{name} ensure failed: {type(e).__name__}: {e}"
            logger.warning("[init_assets] %s", msg)
            report.errors.append(msg)
            report.runtime_actions[name] = "error"

    # 3) 单机版重构后,「全网模型目录 seed」已移除——
    #    前端模型广场只展示已配置的厂商,不再依赖 model_registry seed。

    return report


def format_init_report(report: AssetReport) -> str:
    """人类可读的多行汇总 — 给 CLI 输出用。"""
    lines = []
    if report.compose_file:
        tag = {
            "created": "新建",
            "exists":  "已存在",
            "migrated": "已迁移",
        }.get(report.compose_action, report.compose_action or "?")
        lines.append(f"  [docker-compose] {tag}: {report.compose_file}")
    actions = report.runtime_actions or {}
    if actions:
        created = [k for k, v in actions.items() if v == "created"]
        existed = [k for k, v in actions.items() if v == "exists"]
        skipped = [k for k, v in actions.items() if v == "skipped"]
        if created:
            lines.append(f"  [runtime/]      新建: {', '.join(created)}")
        if existed:
            lines.append(f"  [runtime/]      已存在: {', '.join(existed)}")
        if skipped:
            lines.append(f"  [runtime/]      跳过(依赖缺失): {', '.join(skipped)}")
    if report.errors:
        lines.append(f"  [errors]        {len(report.errors)} 个 — 见日志")
    return "\n".join(lines) if lines else "  (无变化)"


__all__ = [
    "AssetReport",
    "init_runtime_assets",
    "format_init_report",
]
