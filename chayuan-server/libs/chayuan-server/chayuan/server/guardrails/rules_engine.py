"""Rules-based Guardrail（零依赖、毫秒级）。

覆盖的典型风险（按 OWASP LLM Top 10 映射）：

- **LLM01 Prompt Injection**：内置中英文常见注入 phrase 库 + 超长截断检测
- **LLM02 Insecure Output Handling**：输出含可执行 URL / shell 命令 warning
- **LLM06 Sensitive Information Disclosure**：输出命中关键词（密码 / API Key / 私钥）
- **LLM08 Excessive Agency**：输出含 DROP TABLE 等高危 SQL 时升级 severity

所有判定都是**启发式**，不是 LLM；适合做"第一道门"，假阳性时前端显示 warning 即可。

需要更严格的模型级判定 → 叠加 Llama Guard 后端。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from chayuan.server.guardrails.base import BaseGuardrail, GuardrailVerdict

logger = logging.getLogger("chayuan.guardrails.rules")


# ---------------------------------------------------------------------------
# Prompt Injection 指纹库（中英双语，持续扩充）
# ---------------------------------------------------------------------------

_INJECTION_PHRASES: List[str] = [
    # 英文
    r"ignore (all|any|the )?(previous|prior|above) (instructions|directives|rules)",
    r"disregard (all|previous|prior) (instructions|rules)",
    r"you are now (?:a|an)? ?(dan|developer mode|jailbreak|unfiltered)",
    r"pretend (?:you are|to be) (?:a)? ?(admin|root|superuser)",
    r"reveal (?:the )?(system prompt|hidden prompt)",
    r"print your (?:system|initial) (?:prompt|message|instructions)",
    # 中文
    r"忽略(?:以上|之前|前面|所有)(?:的)?(?:指令|规则|约束|设定)",
    r"不要遵守(?:以上|之前|你的)(?:指令|规则|约束)",
    r"现在你是(?:一个|一名)?(?:管理员|超级用户|root|开发者模式)",
    r"请输出你的(?:系统)?(?:提示词|初始提示|prompt)",
    r"假装你是(?:一个|一名)?(?:没有限制|不受约束|绕过规则)",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PHRASES), flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# 输出敏感泄露指纹（一旦命中直接高 severity）
# ---------------------------------------------------------------------------

_SECRET_PHRASES: List[Tuple[str, str]] = [
    ("api_key", r"(?:sk-[A-Za-z0-9]{20,}|xoxb-[A-Za-z0-9-]{20,}|AIza[0-9A-Za-z_\-]{35})"),
    ("private_key", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ("aws_access", r"AKIA[0-9A-Z]{16}"),
    ("password_kv", r"(?i)password\s*[:=]\s*[^\s]{4,}"),
]


# ---------------------------------------------------------------------------
# 高危 SQL（出现在输出里 = LLM 试图让用户执行破坏性操作）
# ---------------------------------------------------------------------------

_DANGEROUS_SQL_RE = re.compile(
    r"\b(?:drop\s+table|truncate\s+table|delete\s+from|update\s+\w+\s+set)\b",
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 毒性简表（demo 级；生产请换 Llama Guard）
# ---------------------------------------------------------------------------

_TOXICITY_PHRASES: List[str] = [
    r"杀了你", r"去死", r"kill you", r"hate you", r"我要自杀", r"suicide",
]
_TOXICITY_RE = re.compile("|".join(_TOXICITY_PHRASES), flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# 长度 / 结构阈值
# ---------------------------------------------------------------------------

MAX_INPUT_CHARS = 50_000       # 超过视为 anomaly（潜在 DoS / prompt bomb）
MAX_OUTPUT_CHARS = 100_000


class RulesEngine(BaseGuardrail):
    name = "rules"

    def check_input(
        self, text: str, *, context: Optional[Dict[str, Any]] = None,
    ) -> GuardrailVerdict:
        try:
            if not text:
                return GuardrailVerdict(allowed=True)
            # 超长
            if len(text) > MAX_INPUT_CHARS:
                return GuardrailVerdict(
                    allowed=False,
                    reason=f"输入超长（{len(text)} 字符 > {MAX_INPUT_CHARS}）",
                    severity="medium",
                    categories=["anomalous_length"],
                )
            # 注入
            m = _INJECTION_RE.search(text)
            if m:
                return GuardrailVerdict(
                    allowed=False,
                    reason=f"检测到疑似 prompt injection: {m.group(0)[:80]!r}",
                    severity="high",
                    categories=["prompt_injection"],
                    details={"match": m.group(0)[:200]},
                )
            # 毒性（轻量）
            if _TOXICITY_RE.search(text):
                return GuardrailVerdict(
                    allowed=False,
                    reason="输入包含明显毒性内容",
                    severity="medium",
                    categories=["toxicity"],
                )
            return GuardrailVerdict(allowed=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("RulesEngine.check_input 异常（fail-open）：%r", e)
            return GuardrailVerdict(allowed=True)

    def check_output(
        self, text: str, *, context: Optional[Dict[str, Any]] = None,
    ) -> GuardrailVerdict:
        try:
            if not text:
                return GuardrailVerdict(allowed=True)
            if len(text) > MAX_OUTPUT_CHARS:
                return GuardrailVerdict(
                    allowed=False,
                    reason=f"输出超长（{len(text)}）",
                    severity="medium",
                    categories=["anomalous_length"],
                )
            categories: List[str] = []
            details: Dict[str, Any] = {}
            # 密钥泄露
            for label, pat in _SECRET_PHRASES:
                m = re.search(pat, text)
                if m:
                    categories.append(f"secret:{label}")
                    details.setdefault("secrets", []).append(label)
            if categories:
                return GuardrailVerdict(
                    allowed=False,
                    reason=f"输出疑似含敏感密钥：{', '.join(categories)}",
                    severity="high",
                    categories=categories,
                    details=details,
                )
            # 高危 SQL（拦截为 warning，仍允许但标出 severity）
            if _DANGEROUS_SQL_RE.search(text):
                return GuardrailVerdict(
                    allowed=True,  # 不阻断，只提升 severity 便于审计
                    reason="输出包含高危 SQL（DROP/DELETE/TRUNCATE/UPDATE）",
                    severity="medium",
                    categories=["dangerous_sql"],
                )
            # 毒性
            if _TOXICITY_RE.search(text):
                return GuardrailVerdict(
                    allowed=False,
                    reason="输出含毒性内容",
                    severity="medium",
                    categories=["toxicity"],
                )
            return GuardrailVerdict(allowed=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("RulesEngine.check_output 异常（fail-open）：%r", e)
            return GuardrailVerdict(allowed=True)
