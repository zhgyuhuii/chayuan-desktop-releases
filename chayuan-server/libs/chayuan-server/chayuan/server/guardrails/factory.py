"""Guardrail 工厂：按 Settings 选择后端，单例返回。

- 任何构建失败都自动回退：nemo → llama_guard → rules → noop
- 运行时可调 ``reset_guardrail_cache()`` 让 kb_settings / yaml 修改生效
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from chayuan.server.guardrails.base import BaseGuardrail, NoopGuardrail

logger = logging.getLogger("chayuan.guardrails.factory")

_LOCK = threading.Lock()
_CACHE: Optional[BaseGuardrail] = None


def _build_rules():
    from chayuan.server.guardrails.rules_engine import RulesEngine
    return RulesEngine()


def _build_llama_guard(model_name: str):
    from chayuan.server.guardrails.llama_guard_engine import LlamaGuardEngine
    return LlamaGuardEngine(model_name=model_name)


def _build_nemo(config_path: str):
    from chayuan.server.guardrails.nemo_engine import NemoEngine
    return NemoEngine(config_path=config_path)


def get_guardrail() -> BaseGuardrail:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        _CACHE = _build_from_settings()
    return _CACHE


def _build_from_settings() -> BaseGuardrail:
    try:
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        enabled = bool(getattr(bs, "GUARDRAIL_ENABLED", False))
        backend = str(getattr(bs, "GUARDRAIL_BACKEND", "rules") or "rules").strip().lower()
    except Exception as e:  # noqa: BLE001
        logger.debug("读取 Guardrail Settings 失败（回退 noop）：%r", e)
        return NoopGuardrail()

    if not enabled or backend in ("off", "disabled", "noop"):
        return NoopGuardrail()

    # 按 backend 构建，失败自动降级
    if backend == "nemo":
        try:
            cfg = str(getattr(Settings.basic_settings, "GUARDRAIL_NEMO_CONFIG", "") or "")
            if not cfg:
                logger.warning("GUARDRAIL_BACKEND=nemo 但 GUARDRAIL_NEMO_CONFIG 未设置；降级 rules")
                return _build_rules()
            return _build_nemo(cfg)
        except Exception as e:  # noqa: BLE001
            logger.warning("NeMo Guardrails 加载失败（降级 rules）：%r", e)
            return _build_rules()

    if backend in ("llama_guard", "llama-guard", "llamaguard"):
        try:
            model = str(getattr(Settings.basic_settings, "GUARDRAIL_LLAMA_MODEL", "") or "")
            if not model:
                logger.warning("GUARDRAIL_BACKEND=llama_guard 但未配置 GUARDRAIL_LLAMA_MODEL；降级 rules")
                return _build_rules()
            return _build_llama_guard(model)
        except Exception as e:  # noqa: BLE001
            logger.warning("Llama Guard 构建失败（降级 rules）：%r", e)
            return _build_rules()

    # 默认 / 显式 rules / 未知后端
    return _build_rules()


def reset_guardrail_cache() -> None:
    """Settings 热更新时清空缓存。"""
    global _CACHE
    with _LOCK:
        _CACHE = None
