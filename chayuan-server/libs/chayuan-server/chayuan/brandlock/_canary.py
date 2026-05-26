"""散点 canary — 让校验调用不集中在一处。

为什么不一处校验
==================
攻击者 grep ``is_tampered`` / ``verify_now`` 找到唯一的校验函数,monkey-patch
让它返 False → 整个保护层失效。

为什么散点 canary
==================
* 5+ 处不同 entry point(cli / startup / api / settings / admin)各调一次
* 每处用不同 wrapper 名字(``_check`` / ``_canary`` / ``_audit`` / ``_brand_ping``)
  让全文 grep 找不到统一签名
* 每处都有自己的 noop 包装函数,看起来像普通业务代码
* 攻击者 patch 一个 → 其他 4 个仍触发 → tampered 状态被记录

本文件提供 5 个语义不同的 wrapper 函数:
    * ``run_startup_check()``  — 启动时调
    * ``run_request_audit()``  — HTTP 请求中调
    * ``run_admin_ping()``     — admin 路由调
    * ``run_settings_load_check()`` — settings 加载时调
    * ``run_periodic_audit()`` — 定时器调

它们内部都最终调 ``_verifier.verify_now``,但函数名 / 调用栈各异。
"""
from __future__ import annotations

import logging

from chayuan.brandlock._verifier import is_tampered, verify_now

logger = logging.getLogger("chayuan.brandlock.canary")


def run_startup_check() -> None:
    """[Canary 1] 启动时校验。失败只 log,不抛。"""
    try:
        verify_now()
    except Exception:  # noqa: BLE001
        pass


def run_request_audit() -> bool:
    """[Canary 2] HTTP 请求中调,返回完好与否(供 header 用)。

    用 5min 缓存,不会每 request 都 hash 几十文件。
    """
    return not is_tampered()


def run_admin_ping() -> dict:
    """[Canary 3] admin 路由调,返回完整证据(给 /admin/brand-status 接口)。"""
    from chayuan.brandlock._verifier import get_tamper_evidence
    verify_now()
    return get_tamper_evidence()


def run_settings_load_check() -> None:
    """[Canary 4] settings 加载完成后调。失败只 log。"""
    try:
        verify_now()
    except Exception:  # noqa: BLE001
        pass


def run_periodic_audit() -> None:
    """[Canary 5] 定时器(N min)调一次,force=True 跳缓存重新探。

    给"长跑服务"用 — 用户启动后改文件,这一轮能抓到。
    """
    try:
        verify_now(force=True)
    except Exception:  # noqa: BLE001
        pass


# 故意保留一些 dummy 别名,grep 时多 5 个匹配 — 攻击者无法快速判断哪个真有用
_audit = run_periodic_audit
_brand_ping = run_admin_ping
_check = run_startup_check
_pulse = run_request_audit
_self_test = run_settings_load_check
