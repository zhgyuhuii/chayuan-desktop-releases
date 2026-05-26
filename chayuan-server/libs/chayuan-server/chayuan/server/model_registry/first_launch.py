"""server 首次启动钩子 — "装机即用"链路的最后一公里。

集成版(standard / pro)装机自带模型,但 capability defaults 在 yaml 里还
是空的;轻量版若用户手动放模型到 ``<CHAYUAN_ROOT>/models/``,也存在同样
"已装但未指派"的过渡态。

本钩子在每次 server 启动时跑一次:

1. ``seed_bundled_models`` 把项目/打包内 ``vendor/bundled_models/`` 拷到
   ``<CHAYUAN_ROOT>/models/bundled/``(用户已替换的文件不动)
2. ``scan_once`` 刷新 :mod:`local_index`
3. ``promote_defaults_from_local`` 为空 default 的 capability 自动指首选
4. ``deploy_user_manuals`` 把包内手册资源部署到 ``<CHAYUAN_ROOT>/manuals/``

幂等保证:四个步骤都是 idempotent。bundled 已存在且未变会跳过;yaml 已有
default 不会被覆盖;手册已是最新版本会跳过。

调用方:
* :func:`chayuan.server.api_server.server_app.create_app` 的 startup
  事件;失败仅记日志,不阻塞启动。
* 也可直接 ``python -c "from ... import run_first_launch_hooks;
  run_first_launch_hooks()"`` 手动重跑。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("chayuan.first_launch")


# bundled 子目录 → (LocalRuntimeSettings 字段名, auto_start_store key)
# 字段名为 None 表示该 cap 没 preload yaml 字段,只翻 auto_start_store(OCR
# sidecar 走 server_app 单独的 _auto_start_rapidocr hook 起)。
# tts 没有 preload/auto_start 机制 — 按需调用,不在这里翻。
_BUNDLED_PRELOAD_MAP: tuple = (
    # cap_dir,    settings_attr,             store_key
    ("chat",      "preload_on_startup",      "chat"),
    ("embedding", "preload_embedding",       "embedding"),
    ("rerank",    "preload_rerank",          "rerank"),
    ("asr",       "preload_asr",             "asr"),
    ("image",     "preload_image_embedding", "image-embedding"),
    ("ocr",       None,                       "rapidocr"),
)


@dataclass
class FirstLaunchReport:
    """``run_first_launch_hooks`` 的返回快照。"""

    seeded_source: Optional[str] = None
    seeded_target: Optional[str] = None
    seeded_copied: List[str] = field(default_factory=list)
    seeded_skipped: List[str] = field(default_factory=list)
    # 外部介质种子(ISO 旁挂的 ≥ 2 GB 大文件)— 装机后仍缺的文件清单
    # + 这次找到的介质路径。UI 可用 external_pending 判断要不要提示用户重挂 ISO。
    seeded_external_pending: List[str] = field(default_factory=list)
    seeded_external_source: Optional[str] = None
    # 本次"装机即用"翻过的 cap → 字段名/store key。空 dict = 都已在历史中翻过
    # 或所有 bundled cap 子目录都没内容。前端可拿这个列表做"已为你开启 X/Y/Z"
    # 提示。
    preload_bootstrapped: Dict[str, str] = field(default_factory=dict)
    scanned: bool = False
    scan_added: int = 0
    scan_updated: int = 0
    scan_removed: int = 0
    promoted: Dict[str, str] = field(default_factory=dict)
    manuals_md: List[str] = field(default_factory=list)
    manuals_docx: List[str] = field(default_factory=list)
    manuals_skipped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "seeded_source": self.seeded_source,
            "seeded_target": self.seeded_target,
            "seeded_copied": list(self.seeded_copied),
            "seeded_skipped": list(self.seeded_skipped),
            "seeded_external_pending": list(self.seeded_external_pending),
            "seeded_external_source": self.seeded_external_source,
            "preload_bootstrapped": dict(self.preload_bootstrapped),
            "scanned": self.scanned,
            "scan_added": self.scan_added,
            "scan_updated": self.scan_updated,
            "scan_removed": self.scan_removed,
            "promoted": dict(self.promoted),
            "manuals_md": list(self.manuals_md),
            "manuals_docx": list(self.manuals_docx),
            "manuals_skipped": list(self.manuals_skipped),
            "errors": list(self.errors),
        }


def _dir_has_real_file(root: Path) -> bool:
    """目录树下是否至少有一个非隐藏文件;空 / 只有 .gitkeep → False。"""
    if not root.is_dir():
        return False
    try:
        for p in root.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                return True
    except OSError:
        return False
    return False


def _cap_has_bundled_content(cap_dir: str) -> bool:
    """该 capability 是否打进了安装包。

    检查顺序:先看 source(``vendor/bundled_models/<cap_dir>/`` 或 PyInstaller
    ``_MEIPASS/bundled_models/<cap_dir>/``),再看 destination(``<CHAYUAN_ROOT>
    /models/bundled/<cap_dir>/``)。任一处有非隐藏文件即认为已交付。

    source 兜底 destination 是为了应对 seed_bundled_models 在某步失败(权限、
    路径、被杀软挡)的情况 —— 即便目标尚未落地,只要 source 里我们打包了这个
    cap,就应该把 preload/auto_start 翻好,等下次 seed 重试时即可用。
    """
    try:
        from chayuan.server.model_registry.local_index import (
            bundled_models_dir, models_dir,
        )
    except Exception:  # noqa: BLE001
        return False

    src_root = bundled_models_dir()
    if src_root is not None and _dir_has_real_file(src_root / cap_dir):
        return True

    dst_root = models_dir() / "bundled"
    return _dir_has_real_file(dst_root / cap_dir)


def _bootstrap_preload_from_bundled(report: FirstLaunchReport) -> None:
    """检测 bundled cap 已交付时,把对应的 preload_*/auto_start.* 翻成 True。

    幂等保障由 :mod:`auto_start_store` 的 ``bootstrapped_caps`` 标记提供 ——
    每个 cap 只在一次"装机即用"过程中翻一次,用户后续在 UI 关掉之后不会被翻回。

    LocalRuntimeSettings 共享同一份 ``local_runtime.yaml``,通过 chat manager
    的 ``set_config`` 写一次即可完成所有字段持久化;同时直接 setattr 同步给其它
    4 个 manager 的 _settings,避免它们读到陈旧值。

    可见性:同 ``_auto_start_rapidocr`` 模式,关键事件 print 到 stdout,Tauri
    sidecar 日志能直接看到 hook 跑没跑、翻了哪几个 cap。
    """
    import sys as _sys

    def _say(msg: str) -> None:
        logger.info("[bootstrap-preload] %s", msg)
        try:
            print(f"[bootstrap-preload] {msg}", flush=True, file=_sys.stdout)
        except Exception:  # noqa: BLE001
            pass

    try:
        from chayuan.server.runtime import auto_start_store
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"bootstrap_preload import: {type(e).__name__}: {e}")
        _say(f"import 失败:{e!r}")
        return

    settings_updates: Dict[str, bool] = {}
    bootstrapped: Dict[str, str] = {}
    checked: List[str] = []

    for cap_dir, settings_attr, store_key in _BUNDLED_PRELOAD_MAP:
        checked.append(cap_dir)
        if auto_start_store.is_bootstrapped(cap_dir):
            continue
        if not _cap_has_bundled_content(cap_dir):
            continue
        if settings_attr:
            settings_updates[settings_attr] = True
        try:
            auto_start_store.set_(store_key, True)
        except Exception as e:  # noqa: BLE001
            report.errors.append(
                f"bootstrap_preload set_ {store_key}: {type(e).__name__}: {e}"
            )
            _say(f"set_({store_key}, True) 异常:{e!r}")
            continue
        auto_start_store.mark_bootstrapped(cap_dir)
        bootstrapped[cap_dir] = settings_attr or f"auto_start.{store_key}"

    if settings_updates:
        try:
            from chayuan.server.model_registry.local_runtime_registry import (
                LocalRuntimeRegistry, get_registry,
            )
            reg = get_registry()
            # 写 yaml + 更新 chat manager 的 _settings(set_config 内部做了)
            reg.get("chat").set_config(settings_updates)
            # 其它 manager 也是同一份 yaml 上各自加载的副本,registry 构造时
            # 已经 load 过,内存里那份是旧的。直接 setattr 同步过去,避免步骤 5
            # 之外的代码路径读到陈旧值。
            for cap in LocalRuntimeRegistry.CAPABILITIES:
                if cap == "chat":
                    continue
                try:
                    m_settings = reg.get(cap).settings
                    for attr, val in settings_updates.items():
                        if hasattr(m_settings, attr):
                            setattr(m_settings, attr, val)
                except Exception as e:  # noqa: BLE001
                    _say(f"同步 {cap}.settings 异常:{e!r}")
        except Exception as e:  # noqa: BLE001
            report.errors.append(
                f"bootstrap_preload save settings: {type(e).__name__}: {e}"
            )
            _say(f"save settings 异常:{e!r}")

    _say(
        f"checked={checked} bootstrapped={list(bootstrapped.keys())} "
        f"updates={list(settings_updates.keys())}"
    )

    if bootstrapped:
        report.preload_bootstrapped = bootstrapped


def run_first_launch_hooks() -> FirstLaunchReport:
    """跑一次完整首启序列。所有失败都被吞并写到 :class:`FirstLaunchReport.errors`,
    任何环节挂掉不会向上抛。
    """
    report = FirstLaunchReport()

    # 1) seed bundled_models -> <CHAYUAN_ROOT>/models/bundled
    try:
        from chayuan.server.model_registry.bundled_seed import seed_bundled_models
        seed = seed_bundled_models()
        report.seeded_source = seed.source
        report.seeded_target = seed.target
        report.seeded_copied = list(seed.copied)
        report.seeded_skipped = list(seed.skipped)
        report.seeded_external_pending = list(seed.external_pending)
        report.seeded_external_source = seed.external_source
        for err in seed.errors:
            report.errors.append(f"seed_bundled: {err}")
    except Exception as e:  # noqa: BLE001
        logger.warning("[first_launch] seed_bundled_models 失败: %r", e)
        report.errors.append(f"seed_bundled_models: {type(e).__name__}: {e}")

    # 1.5) bundled cap 有内容 → 自动开 preload/auto_start(装机即用)
    # 必须放在 step 5 preload_map 之前,这样翻完的 preload_* 能立刻被拉起。
    try:
        _bootstrap_preload_from_bundled(report)
    except Exception as e:  # noqa: BLE001
        logger.warning("[first_launch] bootstrap_preload 失败: %r", e)
        report.errors.append(f"bootstrap_preload: {type(e).__name__}: {e}")

    # 2) scan local_index
    try:
        from chayuan.server.model_registry.local_index import scan_once
        delta = scan_once()
        report.scanned = True
        report.scan_added = len(delta.added)
        report.scan_updated = len(delta.updated)
        report.scan_removed = len(delta.removed)
    except Exception as e:  # noqa: BLE001
        logger.warning("[first_launch] scan_once 失败: %r", e)
        report.errors.append(f"scan_once: {type(e).__name__}: {e}")

    # 3) auto-assign capability defaults from local_index
    try:
        from chayuan.server.model_registry.auto_assign import (
            promote_defaults_from_local,
        )
        report.promoted = promote_defaults_from_local()
    except Exception as e:  # noqa: BLE001
        logger.warning("[first_launch] promote_defaults 失败: %r", e)
        report.errors.append(f"promote_defaults: {type(e).__name__}: {e}")

    # 3.5) PyTorch wheels seed + 自动安装
    # ------------------------------------
    # 把 <install>/torch_wheels/<sub>/ 的 wheel(Tauri 资源)拷到
    # <CHAYUAN_ROOT>/torch_wheels/<sub>/(运行时目录),然后按 GPU 检测结果挑
    # variant(默认 cpu,有 GPU 且驱动 OK 又有对应 wheel 时 cu*)启动后台 pip install。
    # 安装本身走 InstallTask 异步跑,本 hook 只触发,不等待 — 启动链路不阻塞。
    try:
        from chayuan.server.runtime.pytorch_installer import (
            seed_torch_wheels,
            auto_install_on_startup,
            ensure_torch_target_on_syspath,
        )
        # 把 <CHAYUAN_ROOT>/py_packages/(用户点「安装 PyTorch」的 --target 落点)
        # 加进 sys.path。frozen 单机版由 PyInstaller runtime hook 已提前注入一次,
        # 这里幂等再补一次,顺带覆盖非 frozen(dev / pip 装)环境。
        added = ensure_torch_target_on_syspath()
        if added:
            logger.info("[first_launch] py_packages 已加入 sys.path: %s", added)
        seed_rep = seed_torch_wheels()
        if seed_rep.get("copied"):
            logger.info(
                "[first_launch] torch_wheels seeded: %d files src=%s dst=%s",
                len(seed_rep.get("copied", [])),
                seed_rep.get("source"),
                seed_rep.get("target"),
            )
        auto = auto_install_on_startup()
        action = auto.get("action")
        if action == "installing":
            logger.info(
                "[first_launch] auto-installing PyTorch %s (task=%s)",
                auto.get("variant"), auto.get("task_id"),
            )
        elif action == "skipped":
            logger.info("[first_launch] PyTorch 已就绪,跳过自动安装")
        elif action == "deferred":
            logger.info("[first_launch] PyTorch 自动安装延迟:%s", auto.get("reason"))
        elif action == "failed":
            logger.warning("[first_launch] PyTorch 自动安装链路异常:%s", auto.get("error"))
    except Exception as e:  # noqa: BLE001
        logger.warning("[first_launch] torch seed/auto-install 失败: %r", e)
        report.errors.append(f"torch_seed_auto_install: {type(e).__name__}: {e}")

    # 4) deploy user manuals
    try:
        from chayuan.server.manuals.deploy import deploy_user_manuals
        dep = deploy_user_manuals()
        report.manuals_md = list(dep.md_written)
        report.manuals_docx = list(dep.docx_written)
        report.manuals_skipped = list(dep.skipped)
        for err in dep.errors:
            report.errors.append(f"manuals: {err}")
    except Exception as e:  # noqa: BLE001
        logger.warning("[first_launch] deploy_user_manuals 失败: %r", e)
        report.errors.append(f"deploy_user_manuals: {type(e).__name__}: {e}")

    # 4b) 把产品介绍 + 使用手册塞进「使用帮助」文档知识库(首启只建一次;
    #     用户删掉后不会复活 —— 见 help_kb._MARKER_NAME)
    try:
        from chayuan.server.manuals.help_kb import seed_help_kb
        hk = seed_help_kb()
        if hk.get("action") == "created":
            logger.info("[first_launch] 已创建「使用帮助」知识库: %s", hk.get("files"))
        elif hk.get("action") == "failed":
            report.errors.append(f"seed_help_kb: {hk.get('error')}")
    except Exception as e:  # noqa: BLE001
        logger.warning("[first_launch] seed_help_kb 失败: %r", e)
        report.errors.append(f"seed_help_kb: {type(e).__name__}: {e}")

    # 5) 按 LocalRuntimeSettings.preload_* 异步拉起本地 runtime (chat / embedding / rerank)
    try:
        import asyncio
        from chayuan.server.model_registry.local_runtime_registry import get_registry
        reg = get_registry()
        # settings 共享，从 chat manager 拿一次即可
        settings = reg.get("chat").settings
        loop = asyncio.get_event_loop()
        if not loop.is_running():
            logger.info("[first_launch] 不在 event loop 中，跳过 runtime 预热")
        else:
            preload_map = {
                "chat": settings.preload_on_startup,
                "embedding": settings.preload_embedding,
                "rerank": settings.preload_rerank,
                "asr": settings.preload_asr,
                # image-embedding Level C 已下线(_CAP_ENGINE 里也注释了),
                # 这里若仍尝试 reg.get('image-embedding') 会 raise
                # ValueError('unknown capability: image-embedding'),整个
                # 预热循环被外层 except 吞掉 + log warning,影响:
                #   - 干扰排查(每次 first_launch 都打 noisy warning)
                #   - 任何排在 image-embedding 之后的 cap 不会被预热
                #     (这里 dict 序 image 在最后,目前无后续 cap 被影响,
                #     但语义上不该 raise)
                # 恢复图像功能时连同 _CAP_ENGINE / spec 一起放开。
                # "image-embedding": settings.preload_image_embedding,
            }
            for cap, on in preload_map.items():
                if on:
                    loop.create_task(reg.get(cap).start())
                    logger.info("[first_launch] 启动本地 runtime: %s (preload=True)", cap)
                else:
                    logger.info("[first_launch] 跳过预热 %s (preload=False)", cap)
    except Exception as e:  # noqa: BLE001
        logger.warning("[first_launch] 本地 runtime 预热失败: %r", e)
        report.errors.append(f"local_runtime preload: {type(e).__name__}: {e}")

    if report.seeded_copied or report.promoted or report.manuals_md or report.manuals_docx:
        logger.info(
            "[first_launch] seeded=%d/skip=%d promoted=%s manuals_md=%s docx=%s skipped=%s",
            len(report.seeded_copied), len(report.seeded_skipped),
            report.promoted, report.manuals_md, report.manuals_docx,
            report.manuals_skipped,
        )
    return report


# 公开别名:server_app._auto_start_capabilities 兜底用,语义跟 _前缀私版一致,
# 留私版是因为 first_launch 内部其它地方还在用,且单元测试用 _ 前缀的引用更多。
bootstrap_preload_from_bundled = _bootstrap_preload_from_bundled
cap_has_bundled_content = _cap_has_bundled_content


__all__ = [
    "FirstLaunchReport",
    "run_first_launch_hooks",
    "bootstrap_preload_from_bundled",
    "cap_has_bundled_content",
]
