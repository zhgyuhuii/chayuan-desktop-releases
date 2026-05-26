"""角色级脱敏。

策略语义（按用户角色差异化）：
- **admin**：不脱敏（看明文，用于审计 / 调试）
- **analyst / manager**：中度脱敏（手机/卡号中间 4 位打码，邮箱半屏蔽，身份证只留前 6 后 4）
- **user / guest / ""**：严格脱敏（手机全屏蔽 / 身份证全屏蔽 / 邮箱只留域名后缀）

策略等级也可以通过 `governance_policy.masking_level` 覆盖：
- `off`     不脱敏
- `loose`   等价于 analyst
- `strict`  等价于 user

对一个 PII 实体，本模块按"类型 × 等级"决定替换规则。
"""
from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger("chayuan.governance.masking")


# -----------------------------------------------------------------------
# 角色 → level
# -----------------------------------------------------------------------

_ROLE_LEVEL = {
    "admin": "off",
    "analyst": "loose",
    "manager": "loose",
    "editor": "loose",
    "user": "strict",
    "guest": "strict",
    "": "strict",
}


def level_for_role(role: str) -> str:
    return _ROLE_LEVEL.get((role or "").lower(), "strict")


# -----------------------------------------------------------------------
# 各类型 × 等级 的脱敏规则
# -----------------------------------------------------------------------

def _mask_phone_cn(val: str, level: str) -> str:
    if level == "off" or len(val) != 11:
        return val
    if level == "loose":
        return val[:3] + "****" + val[-4:]
    return "***********"  # strict


def _mask_id_card(val: str, level: str) -> str:
    if level == "off":
        return val
    if level == "loose":
        if len(val) >= 10:
            return val[:6] + "*" * (len(val) - 10) + val[-4:]
        return "*" * len(val)
    return "*" * len(val)


def _mask_email(val: str, level: str) -> str:
    if level == "off" or "@" not in val:
        return val
    local, _, domain = val.partition("@")
    if level == "loose":
        if len(local) <= 2:
            return local[0] + "*@" + domain
        return local[0] + "*" * (len(local) - 2) + local[-1] + "@" + domain
    # strict：长度对齐（方便前端按 span 做高亮 / 还原）
    # 用 "*" 填充 local + "@" + "*" 填充 domain 主体 + 原 .tld
    dot_idx = domain.rfind(".")
    if dot_idx <= 0:
        return "*" * len(val)
    tld = domain[dot_idx:]             # 含点：".com"
    main = domain[:dot_idx]            # 域主体
    return "*" * len(local) + "@" + "*" * len(main) + tld


def _mask_bank_card(val: str, level: str) -> str:
    if level == "off":
        return val
    if level == "loose" and len(val) >= 8:
        return val[:4] + "*" * (len(val) - 8) + val[-4:]
    return "*" * len(val)


def _mask_ipv4(val: str, level: str) -> str:
    if level == "off":
        return val
    parts = val.split(".")
    if len(parts) != 4:
        return val
    if level == "loose":
        return f"{parts[0]}.{parts[1]}.***.***"
    return "***.***.***.***"


def _mask_plate(val: str, level: str) -> str:
    if level == "off":
        return val
    if level == "loose" and len(val) >= 3:
        return val[:2] + "*" * (len(val) - 2)
    return "*" * len(val)


def _mask_generic(val: str, level: str) -> str:
    """Presidio 返回的未知类型兜底。"""
    if level == "off":
        return val
    return "*" * len(val)


_MASKERS = {
    "PHONE_CN": _mask_phone_cn,
    "ID_CARD_CN": _mask_id_card,
    "EMAIL": _mask_email,
    "BANK_CARD": _mask_bank_card,
    "IPV4": _mask_ipv4,
    "PLATE_CN": _mask_plate,
}


# -----------------------------------------------------------------------
# 对外入口
# -----------------------------------------------------------------------

def apply_masking(
    text: str,
    entities: List[Dict],
    *,
    user_role: str = "",
    override_level: str = "",
) -> str:
    """按 entities 的 span 做替换；保证输出长度与原文**对齐**（便于前端高亮对应）。

    entities 来自 ``pii.scan_text``；必须含 start/end/type。
    """
    if not text or not entities:
        return text
    level = override_level or level_for_role(user_role)
    if level == "off":
        return text

    # 按 start 排序后从右往左替换，避免 index 漂移
    sorted_ents = sorted(entities, key=lambda x: int(x.get("start") or 0), reverse=True)
    buf = list(text)
    for e in sorted_ents:
        try:
            s, en = int(e["start"]), int(e["end"])
            if s < 0 or en <= s or en > len(text):
                continue
            original = text[s:en]
            etype = (e.get("type") or "").upper()
            masker = _MASKERS.get(etype, _mask_generic)
            masked = masker(original, level)
            buf[s:en] = list(masked)
        except Exception as exc:  # noqa: BLE001
            logger.debug("mask %s 失败：%r", e, exc)
            continue
    return "".join(buf)


def mask_row_values(
    rows: List[List], columns: List[str], *,
    user_role: str = "", override_level: str = "",
) -> List[List]:
    """按列值做 PII 扫描 + 脱敏（SQL 结果集用）。

    对 query 返回的结果表，按单元格扫描并脱敏；commit 给前端前做一次。
    """
    from chayuan.server.governance.pii import scan_text
    level = override_level or level_for_role(user_role)
    if level == "off":
        return rows
    out: List[List] = []
    for r in rows or []:
        new_row = list(r)
        for i, v in enumerate(new_row):
            if v is None or not isinstance(v, (str, int, float)):
                continue
            sv = str(v)
            if not sv:
                continue
            ents = scan_text(sv, min_confidence=0.9, enable_presidio=False)
            if ents:
                new_row[i] = apply_masking(sv, ents, override_level=level)
        out.append(new_row)
    return out
