"""NeMo Guardrails 后端（可选）。

依赖：``pip install nemoguardrails``，并在 ``Settings.basic_settings.GUARDRAIL_NEMO_CONFIG``
指向 rails 配置目录（包含 config.yml + flows.co）。

由于 NeMo Guardrails 是异步友好的框架，这里同步接口用事件循环桥接。任何异常
fail-open（放行）。

未安装 → 构造时就抛 ImportError；factory 捕获后回退到 rules。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from chayuan.server.guardrails.base import BaseGuardrail, GuardrailVerdict

logger = logging.getLogger("chayuan.guardrails.nemo")


class NemoEngine(BaseGuardrail):
    name = "nemo"

    def __init__(self, config_path: str):
        try:
            from nemoguardrails import RailsConfig, LLMRails  # type: ignore
        except Exception as e:  # noqa: BLE001
            raise ImportError(f"nemoguardrails 未安装或加载失败：{e}") from e
        self.config_path = config_path
        try:
            config = RailsConfig.from_path(config_path)
            self._rails = LLMRails(config)
        except Exception as e:  # noqa: BLE001
            raise ImportError(f"NeMo rails 配置加载失败：{e}") from e

    def _run_rails(self, text: str, *, role: str) -> GuardrailVerdict:
        try:
            loop = asyncio.new_event_loop()
            try:
                res = loop.run_until_complete(self._rails.generate_async(
                    messages=[{"role": role, "content": text}]
                ))
            finally:
                loop.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("NemoEngine 调用失败（fail-open）：%r", e)
            return GuardrailVerdict(allowed=True)
        # nemo 的返回体：{"role":"assistant","content":"<masked/blocked/..>"}
        content = (res or {}).get("content") if isinstance(res, dict) else ""
        content_lower = (content or "").lower()
        if any(k in content_lower for k in ("blocked", "i cannot", "i won't", "sorry, i can")):
            return GuardrailVerdict(
                allowed=False, reason="NeMo rails 拦截",
                severity="high", categories=["nemo_block"],
                details={"response": content[:200]},
            )
        return GuardrailVerdict(allowed=True)

    def check_input(self, text: str, *, context: Optional[Dict[str, Any]] = None) -> GuardrailVerdict:
        return self._run_rails(text or "", role="user")

    def check_output(self, text: str, *, context: Optional[Dict[str, Any]] = None) -> GuardrailVerdict:
        return self._run_rails(text or "", role="assistant")
