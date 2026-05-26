"""模型装完后的"默认值自动指派"钩子。

为什么需要
==========

当 ``chayuan_packaging`` 把模型释放到 ``<CHAYUAN_ROOT>/models/`` 之后:

* :mod:`local_index.scan_once` 能扫到 → 候选可见;
* 但 :mod:`config_panel` 的 ``DEFAULT_*_MODEL`` 还是空 →
  :func:`capability_router.resolve_model` 拿不到值 → retrieval / chat
  会跑空。

老路径上,只有 GUI 打开 ``GET /admin/capability_defaults`` 时,后端才会
"auto_assigned"——把第一个候选 promote 成默认。这意味着用户安装完直接打开
chat 会失败,必须先去设置页一次。

本模块把这个 promote 逻辑抽成纯函数,让 :mod:`install_job` 在下载成功后
立刻调一次,**装完即用**,不强求用户先走一遍设置页。
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, Optional

logger = logging.getLogger("chayuan.model_registry.auto_assign")


def promote_defaults_from_local(
    *,
    capabilities: Optional[Iterable[str]] = None,
    overwrite: bool = False,
) -> Dict[str, str]:
    """为每个 panel capability,若当前无默认且本地有候选,promote 第一条。

    Args:
        capabilities: 限定要处理的 panel capability(``chat`` / ``embedding`` /
            ``rerank`` / ``asr`` / ``ocr`` / ``clip`` / ``t2i`` / ``t2v`` / ``tts``)。
            ``None`` 表示对 :data:`LOCAL_TO_PANEL_CAP` 里所有 panel cap 都尝试。
        overwrite: ``True`` 时即使有默认也覆写为本地首选(慎用)。默认 False。

    Returns:
        ``{panel_cap: 新写入的 model_id}``;无变更项不在返回里。

    实现要点:
    * 完全复用 :mod:`local_index` 的扫盘结果,不重新扫盘;
    * 写入走 :func:`config_panel.runtime_framework_panel._save_capability_default`,
      保持与 GUI 入口一致(同一 yaml + LocalIndexRepository 同步路径);
    * 任一 cap 失败不阻塞其它(逐条 try/except)。
    """
    from chayuan.server.model_registry.candidates_bridge import (
        LOCAL_TO_PANEL_CAP,
    )
    from chayuan.server.model_registry.local_index import get_local_index

    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            _load_capability_defaults,
            _save_capability_default,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[auto_assign] config_panel 不可用: %r", e)
        return {}

    panel_caps_filter = set(capabilities) if capabilities else None
    defaults = _load_capability_defaults() or {}
    idx = get_local_index()

    promoted: Dict[str, str] = {}
    seen_panel_caps = set()
    for local_cap, panel_cap in LOCAL_TO_PANEL_CAP.items():
        if panel_caps_filter is not None and panel_cap not in panel_caps_filter:
            continue
        if panel_cap in seen_panel_caps:
            continue
        seen_panel_caps.add(panel_cap)

        current = (defaults.get(panel_cap) or "").strip()
        if current and not overwrite:
            continue

        candidates = idx.by_capability(local_cap)
        if not candidates:
            continue

        choice = candidates[0]
        try:
            ok, msg = _save_capability_default(panel_cap, choice.model_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[auto_assign] save_capability_default(%s, %s) 抛异常: %r",
                panel_cap, choice.model_id, e,
            )
            continue
        if not ok:
            logger.warning(
                "[auto_assign] save_capability_default(%s, %s) 失败: %s",
                panel_cap, choice.model_id, msg,
            )
            continue
        promoted[panel_cap] = choice.model_id
        logger.info(
            "[auto_assign] %s → %s (panel cap promoted from local_index)",
            panel_cap, choice.model_id,
        )

    return promoted


__all__ = ["promote_defaults_from_local"]
