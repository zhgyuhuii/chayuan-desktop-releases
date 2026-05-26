"""PII（个人可识别信息）识别。

设计：
- **内置规则** 覆盖中文常见：手机号 / 身份证 / 邮箱 / 银行卡 / IPv4
- **可选 Presidio** 后端（`pip install presidio-analyzer presidio-anonymizer`）扩充
  英文场景（SSN / US phone / DOB 等）
- 失败 fail-open（返回空列表），不阻塞业务

返回标准格式：
    [{"type": "PHONE_CN", "start": 12, "end": 23, "value": "13800138000", "confidence": 0.95}]
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger("chayuan.governance.pii")


# ---------------------------------------------------------------------------
# 内置规则
# ---------------------------------------------------------------------------

_BUILTIN_PATTERNS: List[tuple] = [
    # 中国大陆手机号（11 位，1 开头，第二位 3-9）
    ("PHONE_CN", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), 0.95),
    # 中国身份证（18 位，末位可为 X）
    ("ID_CARD_CN", re.compile(r"(?<!\d)(\d{15}|\d{17}[\dXx])(?!\d)"), 0.98),
    # 邮箱
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]{1,64}@[a-zA-Z0-9.-]{1,255}\.[a-zA-Z]{2,24}"), 0.95),
    # 银行卡号：13-19 位数字（粗粒度，误报率偏高 → 低 confidence）
    ("BANK_CARD", re.compile(r"(?<!\d)\d{13,19}(?!\d)"), 0.5),
    # IPv4
    ("IPV4", re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
                          r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?!\d)"), 0.9),
    # 国内车牌号（简化）
    ("PLATE_CN", re.compile(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
                             r"[A-HJ-NP-Z]\d{5}(?:[0-9A-HJ-NP-Z]|[学警挂领])?"), 0.9),
]


@dataclass
class PIIEntity:
    type: str
    start: int
    end: int
    value: str
    confidence: float = 0.9
    source: str = "builtin"

    def to_dict(self) -> Dict:
        return {
            "type": self.type, "start": self.start, "end": self.end,
            "value": self.value, "confidence": self.confidence, "source": self.source,
        }


def scan_text(
    text: str,
    *,
    min_confidence: float = 0.85,
    enable_presidio: bool = True,
) -> List[Dict]:
    """扫描 text，返回命中的 PII 实体列表。"""
    if not text:
        return []
    entities: List[PIIEntity] = []

    # 内置规则
    for etype, pat, conf in _BUILTIN_PATTERNS:
        for m in pat.finditer(text):
            ent = PIIEntity(
                type=etype, start=m.start(), end=m.end(),
                value=m.group(0), confidence=conf, source="builtin",
            )
            entities.append(ent)

    # 可选：Presidio 扩增
    if enable_presidio:
        try:
            pre = _presidio_scan(text)
            entities.extend(pre)
        except Exception as e:  # noqa: BLE001
            logger.debug("presidio 不可用（忽略）：%r", e)

    # 过滤 & 去重（按 span 唯一）
    seen = set()
    deduped: List[PIIEntity] = []
    for e in sorted(entities, key=lambda x: (-x.confidence, x.start)):
        if e.confidence < min_confidence:
            continue
        key = (e.start, e.end)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    deduped.sort(key=lambda x: x.start)
    return [e.to_dict() for e in deduped]


# ---------------------------------------------------------------------------
# Presidio 后端（懒加载）
# ---------------------------------------------------------------------------

_PRESIDIO_ANALYZER = None
_PRESIDIO_TRIED = False


def _presidio_scan(text: str) -> List[PIIEntity]:
    global _PRESIDIO_ANALYZER, _PRESIDIO_TRIED
    if _PRESIDIO_TRIED and _PRESIDIO_ANALYZER is None:
        return []
    if _PRESIDIO_ANALYZER is None:
        _PRESIDIO_TRIED = True
        try:
            from presidio_analyzer import AnalyzerEngine  # type: ignore
            _PRESIDIO_ANALYZER = AnalyzerEngine()
        except Exception:
            _PRESIDIO_ANALYZER = None
            return []

    try:
        results = _PRESIDIO_ANALYZER.analyze(text=text, language="en")
    except Exception as e:  # noqa: BLE001
        logger.debug("presidio analyze 失败：%r", e)
        return []

    out: List[PIIEntity] = []
    for r in results or []:
        try:
            out.append(PIIEntity(
                type=str(getattr(r, "entity_type", "") or "UNKNOWN"),
                start=int(getattr(r, "start", 0)),
                end=int(getattr(r, "end", 0)),
                value=text[int(r.start): int(r.end)],
                confidence=float(getattr(r, "score", 0.5) or 0.5),
                source="presidio",
            ))
        except Exception:  # noqa: BLE001
            continue
    return out
