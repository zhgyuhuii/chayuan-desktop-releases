"""察元 AI 一体化平台桥接模块（M0 · 迁移自 chayuan-ai-platform/）。

历史背景
========

``chayuan-ai-platform`` 原本是一个独立的 uv-workspace，把 9 类多模态能力
（chat / text-embedding / image-embedding / rerank / t2i / t2v / tts /
asr / ocr）做成 OpenAI 协议网关。它被并入察元主仓 ``chayuan-server``，
作为 11 个 sibling 包放在 ``libs/chayuan-{core,identify,registry,
discovery,modelmgr,runtime,supervisor,gateway,cli,packager,preflight}``。

本模块（``chayuan.server.ai_platform``）是这 11 个包与已有 ``chayuan-server``
代码之间的薄桥接层：

* :func:`register_ai_platform_routes` —— 把 ``chayuan_gateway`` 的 9 类
  OpenAI-compatible 路由 mount 到察元的 FastAPI app（前缀 ``/v1/*``）。
* :func:`register_ai_platform_cli` —— 把 ``chayuan_cli`` 的 ``model /
  service / doctor / info`` 子命令注入到主 ``chayuan`` Click 群组下，
  暴露为 ``chayuan ai-platform <subcommand> ...``。
* :func:`bootstrap_ai_platform_config` —— 首次启动时把
  ``chayuan/data/ai_platform_config/*.yaml`` 复制到 ``<CHAYUAN_ROOT>/config/``，
  让用户在熟悉的位置就能改 supervisor / model_rules / releases。
* :func:`is_ai_platform_available` —— 探测 11 个包是否在 sys.path 上；
  缺包时上层走"功能降级而不是崩"。

> **不耦合保证**：本模块只引 ``chayuan_*`` 顶级包做导入；它**不**反过来 import
> ``chayuan.server.*``。chayuan-ai-platform 残留任何一个包没装时，本模块函数
> 会优雅降级（跳过 mount / 跳过注入），不会让 chayuan-server 启动失败。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger("chayuan.ai_platform")

if TYPE_CHECKING:  # pragma: no cover
    import click
    from fastapi import FastAPI


__all__ = [
    "register_ai_platform_routes",
    "register_ai_platform_cli",
    "bootstrap_ai_platform_config",
    "is_ai_platform_available",
    "AI_PLATFORM_PACKAGES",
    "apply_runtime_config",
    "enable_event_bridge",
]


AI_PLATFORM_PACKAGES = (
    "chayuan_core",
    "chayuan_identify",
    "chayuan_registry",
    "chayuan_discovery",
    "chayuan_modelmgr",
    "chayuan_runtime",
    "chayuan_supervisor",
    "chayuan_gateway",
    "chayuan_cli",
    "chayuan_packager",
    "chayuan_preflight",
)


def is_ai_platform_available(*, packages: tuple = AI_PLATFORM_PACKAGES) -> dict:
    """探测每个 ai-platform 包是否安装。

    Returns:
        ``{pkg_name: bool}``，全部为 True 时整套能力可用。
    """
    import importlib.util

    return {p: importlib.util.find_spec(p) is not None for p in packages}


def _config_seed_dir() -> Path:
    """``chayuan/data/ai_platform_config/`` 的绝对路径。"""
    return Path(__file__).resolve().parent.parent.parent / "data" / "ai_platform_config"


def bootstrap_ai_platform_config(*, target_dir: Optional[Path] = None,
                                  overwrite: bool = False) -> dict:
    """把内置 yaml seed 复制到用户配置目录。

    Args:
        target_dir: 目标目录；默认 ``<CHAYUAN_ROOT>/config/ai_platform/``。
        overwrite: 已存在时是否覆盖（默认 False，保留用户改动）。

    Returns:
        ``{"copied": [...], "skipped": [...], "target": "<path>"}``
    """
    seed = _config_seed_dir()
    if target_dir is None:
        try:
            from chayuan.settings import CHAYUAN_ROOT
            target_dir = Path(CHAYUAN_ROOT) / "config" / "ai_platform"
        except Exception:  # noqa: BLE001
            target_dir = Path.cwd() / "config" / "ai_platform"
    target_dir.mkdir(parents=True, exist_ok=True)

    copied, skipped = [], []
    if not seed.is_dir():
        logger.warning("[ai-platform] seed dir 不存在：%s", seed)
        return {"copied": copied, "skipped": skipped, "target": str(target_dir)}

    for src in seed.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(seed)
        dst = target_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not overwrite:
            skipped.append(str(rel))
            continue
        shutil.copy2(src, dst)
        copied.append(str(rel))
    logger.info("[ai-platform] bootstrap config to %s: copied=%d skipped=%d",
                target_dir, len(copied), len(skipped))
    return {"copied": copied, "skipped": skipped, "target": str(target_dir)}


def register_ai_platform_routes(app: "FastAPI", *, prefix: str = "/v1") -> dict:
    """把 chayuan_gateway 的 9 类 OpenAI-兼容路由挂到察元 FastAPI。

    设计要点：
    * 把网关 router 直接 mount 到 chayuan-server 的同一个 ASGI app，
      避免再起一个 :38080 子进程；少一份证书 / CORS / Auth 配置 / 内存。
    * gateway 自身的 ``/v1/*`` 与 chayuan-server 既有的 ``/openai/*``
      并存（前者面向 OpenAI SDK，后者是历史接口），两个层不互相吃。
    * 任何包导入失败都不要让主进程崩；记一条 warning 即可。

    Args:
        app: 已经创建好的 FastAPI 实例。
        prefix: 网关挂载前缀，默认 ``/v1``（OpenAI 标准）。

    Returns:
        ``{"mounted": [router_name, ...], "skipped": [(name, reason), ...]}``
    """
    mounted, skipped = [], []
    avail = is_ai_platform_available()
    if not avail.get("chayuan_gateway"):
        logger.info("[ai-platform] chayuan_gateway 未安装，跳过 /v1 路由挂载")
        return {"mounted": mounted, "skipped": [("chayuan_gateway", "not installed")]}

    try:
        from chayuan_gateway.routers import (
            admin as gw_admin,
            audio as gw_audio,
            chat as gw_chat,
            embeddings as gw_embeddings,
            images as gw_images,
            models as gw_models,
            ocr as gw_ocr,
            rerank as gw_rerank,
            system as gw_system,
            videos as gw_videos,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[ai-platform] 导入 gateway routers 失败：%r", e)
        return {"mounted": mounted, "skipped": [("import", repr(e))]}

    pairs = [
        ("system", gw_system.router),
        ("models", gw_models.router),
        ("chat", gw_chat.router),
        ("embeddings", gw_embeddings.router),
        ("rerank", gw_rerank.router),
        ("audio", gw_audio.router),
        ("images", gw_images.router),
        ("videos", gw_videos.router),
        ("ocr", gw_ocr.router),
        ("admin", gw_admin.router),
    ]
    for name, router in pairs:
        try:
            # 防止双层 /v1/v1 ：
            #   1) 若 router 自己声明了以 /v1 开头的 prefix（如 admin: "/v1/admin"），
            #      include_router 不需要再加 prefix；
            #   2) 若 router 无 prefix，但路由装饰器里写的是绝对路径 "/v1/xxx"
            #      （chayuan_gateway 大部分 router 都是这种风格），同样不需要再加。
            #   3) 否则才把传入的 prefix（默认 /v1）作为前缀挂上。
            existing_prefix = (getattr(router, "prefix", "") or "")
            routes_already_versioned = any(
                getattr(rt, "path", "").startswith(prefix)
                for rt in getattr(router, "routes", [])
            )
            if existing_prefix.startswith(prefix) or routes_already_versioned:
                kwargs = {}
            else:
                kwargs = {"prefix": prefix}
            app.include_router(router, **kwargs)
            mounted.append(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("[ai-platform] mount router %s 失败：%r", name, e)
            skipped.append((name, repr(e)))

    # ── 关键：把 chayuan_gateway 的 ``get_repo`` Depends 替换成"读 chayuan-server
    # local_index"的版本，这样 9 类路由不需要再起独立 SQLite 也能工作。──────────
    try:
        from chayuan_gateway.deps import get_repo as gateway_get_repo
        from chayuan.server.ai_platform.repo_bridge import local_repo_factory
        app.dependency_overrides[gateway_get_repo] = local_repo_factory
        logger.info("[ai-platform] 注入 LocalIndexRepository → gateway.get_repo")
    except Exception as e:  # noqa: BLE001
        logger.warning("[ai-platform] dependency_overrides 失败：%r", e)
        skipped.append(("repo_override", repr(e)))

    # ── 让 chayuan_runtime 的 11 个 adapter base_url 指向 chayuan-server runtime.json
    try:
        from chayuan.server.ai_platform.runtime_config import apply_runtime_config
        apply_runtime_config(mock=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("[ai-platform] apply_runtime_config 失败：%r", e)
        skipped.append(("runtime_config", repr(e)))

    # ── 接通 local_index → 事件总线 → SSE
    try:
        from chayuan.server.ai_platform.event_bridge import enable_event_bridge
        enable_event_bridge()
    except Exception as e:  # noqa: BLE001
        logger.warning("[ai-platform] enable_event_bridge 失败：%r", e)
        skipped.append(("event_bridge", repr(e)))

    if mounted:
        logger.info("[ai-platform] /v1 routers mounted: %s", ",".join(mounted))
    return {"mounted": mounted, "skipped": skipped}


# 顶层重新导出，方便外部脚本：``from chayuan.server.ai_platform import apply_runtime_config``
def apply_runtime_config(*, mock: bool = False):  # type: ignore[no-redef]
    """转发到 ``runtime_config.apply_runtime_config``。"""
    from chayuan.server.ai_platform.runtime_config import apply_runtime_config as _impl
    return _impl(mock=mock)


def enable_event_bridge():  # type: ignore[no-redef]
    """转发到 ``event_bridge.enable_event_bridge``。"""
    from chayuan.server.ai_platform.event_bridge import (
        enable_event_bridge as _impl,
    )
    return _impl()


def register_ai_platform_cli(parent: "click.Group") -> bool:
    """把 chayuan_cli 的 Click 群组注册为 ``chayuan ai-platform`` 子命令。

    Args:
        parent: 主 ``chayuan`` Click 群组（来自 ``chayuan/cli.py`` 的 ``main``）。

    Returns:
        True 注册成功 / False 跳过（包未装）
    """
    avail = is_ai_platform_available()
    missing = [k for k, v in avail.items() if not v]
    if not avail.get("chayuan_cli"):
        logger.info("[ai-platform] chayuan_cli 未安装，跳过 CLI 注册（缺包：%s）",
                    ",".join(missing))
        return False
    try:
        from chayuan_cli.__main__ import cli as ai_cli
    except Exception as e:  # noqa: BLE001
        logger.warning("[ai-platform] 加载 chayuan_cli.cli 失败：%r", e)
        return False

    # 给 ai_cli 换个名字（默认 "cli"）以便 chayuan ai-platform --help 显示更清晰
    ai_cli.name = "ai-platform"
    parent.add_command(ai_cli)
    logger.info("[ai-platform] CLI 注入：`chayuan ai-platform <subcommand>` 可用")
    return True
