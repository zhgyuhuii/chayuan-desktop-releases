"""Guardrail 子系统（P1-7）。

分层：
- BaseGuardrail 抽象：check_input / check_output，返回 (allowed, reason, severity)
- 三后端：
  1. RulesEngine    — 内置规则，零依赖、毫秒级；作为默认与最底线
  2. NemoEngine     — 需 ``pip install nemoguardrails``；加载用户 YAML rails
  3. LlamaGuardEngine — 调用自配置 LLM（如 Meta Llama-Guard-3）做毒性 / 注入判定
- get_guardrail()：按 Settings.basic_settings.GUARDRAIL_BACKEND 返回单例；
  未启用或构建失败 → 返回 NoopGuardrail（全部放行）
"""

from chayuan.server.guardrails.base import (  # noqa: F401
    BaseGuardrail, GuardrailVerdict, NoopGuardrail,
)
from chayuan.server.guardrails.factory import get_guardrail  # noqa: F401
