"""Guardrail 抽象基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class GuardrailVerdict:
    """Guardrail 判定结果。

    - ``allowed=False`` 时 reason 必填；调用方决定是返回 403 还是替换内容
    - ``severity``：low/medium/high；high 建议直接拒绝，medium 可降级（脱敏 / 改写）
    - ``categories``：触发的规则分类（prompt_injection / toxicity / pii_leak / ...）
    """

    allowed: bool = True
    reason: str = ""
    severity: str = "low"
    categories: List[str] = None
    details: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed, "reason": self.reason,
            "severity": self.severity,
            "categories": list(self.categories or []),
            "details": dict(self.details or {}),
        }


class BaseGuardrail(ABC):
    """统一 Guardrail 契约。所有实现都**必须 fail-open**（失败即放行）。"""

    name: str = "base"

    @abstractmethod
    def check_input(self, text: str, *, context: Optional[Dict[str, Any]] = None) -> GuardrailVerdict:
        """对用户输入做检查。典型拦截：prompt injection / 敏感关键词 / 超长请求。"""

    @abstractmethod
    def check_output(self, text: str, *, context: Optional[Dict[str, Any]] = None) -> GuardrailVerdict:
        """对模型输出做检查。典型拦截：毒性 / 越权泄露 / 违反事实约束。"""


class NoopGuardrail(BaseGuardrail):
    """未启用或构建失败时的兜底实现：一律放行。"""

    name = "noop"

    def check_input(self, text: str, *, context=None) -> GuardrailVerdict:
        return GuardrailVerdict(allowed=True)

    def check_output(self, text: str, *, context=None) -> GuardrailVerdict:
        return GuardrailVerdict(allowed=True)
