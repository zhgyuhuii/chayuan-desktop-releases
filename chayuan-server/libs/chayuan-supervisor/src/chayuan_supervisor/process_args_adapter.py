"""Supervisor ↔ chayuan-server **进程参数**适配层。

兄弟模块 :mod:`runtime_adapter` 负责让 supervisor 与 chayuan-server 共享一份
``runtime.json``。本模块解决的是另一个集成问题:

* ``supervisor.yaml`` 里的 ``llamacpp`` / ``infinity`` / ``ollama`` 等推理引
  擎,启动时**默认没有绑定模型** —— yaml 只写了 host/port,不写 ``--model``。
* 这导致 ``llama-server`` 起来后等于空跑,任何 chat 请求都返回 404。

chayuan-server 在场时,它的
:mod:`chayuan.server.model_registry.process_args` 模块能根据 capability
defaults + local_index,解析出"该进程应该用哪个模型文件"。supervisor 在
:meth:`ProcessManager.plan` 把 spec 物化成 ``ManagedProcess`` 时调一次本模块,
把解析到的 args / env 追加到 spec —— 子进程就能带着 ``--model <path>`` 启动。

设计要点
========

* 跟 ``runtime_adapter`` 一样,通过 import 探测决定启用与否:chayuan-server
  不在场(standalone supervisor) → 返回空,supervisor 沿用 yaml 原样;
* 解析失败(模型还没下载 / 没设 default)也返回空,不阻塞 supervisor 启动:
  子进程会以空 args 起来 —— 由前端 GUI 引导用户去配模型;
* 本模块**只读**:不写盘,不重启进程,纯函数化。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger("chayuan_supervisor.process_args_adapter")


def resolve_extra_for(process_name: str) -> Tuple[List[str], Dict[str, str]]:
    """返回该进程的"动态追加" ``(extra_args, extra_env)``。

    陈年 spec 不在 chayuan-server 的解析覆盖里时(或 chayuan-server 不可
    导入时),返回 ``([], {})`` —— supervisor 直接走 yaml 原样。

    Args:
        process_name: ``supervisor.yaml`` 里的 ``name`` 字段(``llamacpp`` /
            ``infinity`` / ``ollama`` / 其它)。

    Returns:
        ``(extra_args, extra_env)``。
        * ``extra_args``: 追加到 :class:`ProcessSpec.args` 之后的字符串列表;
        * ``extra_env``: 合并到 :class:`ProcessSpec.env` 的键值对(已存在的键
          **不**覆盖,保留 yaml 原值)。
    """
    try:
        from chayuan.server.model_registry.process_args import resolve_all
    except Exception as e:  # noqa: BLE001
        logger.debug("[process_args_adapter] chayuan-server 不可导入: %r", e)
        return [], {}

    try:
        snapshot = resolve_all()
    except Exception as e:  # noqa: BLE001
        logger.warning("[process_args_adapter] resolve_all 失败: %r", e)
        return [], {}

    r = snapshot.get(process_name)
    if r is None:
        return [], {}

    if r.missing:
        logger.info(
            "[process_args_adapter] %s 解析缺项: %s (reason=%s)",
            process_name, r.missing, r.reason,
        )
    if r.args or r.env:
        logger.info(
            "[process_args_adapter] %s 追加 args=%d env=%d (resolved=%s)",
            process_name, len(r.args), len(r.env), r.resolved_models,
        )
    return list(r.args), dict(r.env)


__all__ = ["resolve_extra_for"]
