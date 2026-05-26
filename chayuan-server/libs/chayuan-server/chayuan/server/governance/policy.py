"""治理策略读取。

策略的有效路径（从细到粗）：
1. user:<id>     个人策略
2. role:<name>   角色策略
3. global        全局兜底

查询时按此顺序返回第一条 enabled=1 的记录。未命中则返回内置默认策略（宽松）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from chayuan.server.db.models.governance_model import GovernancePolicyModel
from chayuan.server.db.session import session_scope, with_session

logger = logging.getLogger("chayuan.governance.policy")


@dataclass
class EffectivePolicy:
    scope: str = "default"
    daily_token_budget: int = -1
    qps: int = -1
    masking_level: str = "loose"
    allowed_modes: Optional[List[str]] = None
    extra: Dict = None

    def is_mode_allowed(self, mode: str) -> bool:
        if not self.allowed_modes:
            return True
        return str(mode or "") in self.allowed_modes


DEFAULT_POLICY = EffectivePolicy(
    scope="default", daily_token_budget=-1, qps=-1, masking_level="loose",
    allowed_modes=None, extra={},
)


@with_session
def get_policy(session: Session, user_id: Optional[int], user_role: str = "") -> EffectivePolicy:
    for scope in _candidate_scopes(user_id, user_role):
        row = session.query(GovernancePolicyModel).filter(
            GovernancePolicyModel.scope == scope,
            GovernancePolicyModel.enabled == 1,
        ).one_or_none()
        if row is not None:
            return _row_to_policy(row)
    return DEFAULT_POLICY


def _candidate_scopes(user_id: Optional[int], role: str) -> List[str]:
    out: List[str] = []
    if user_id is not None:
        out.append(f"user:{int(user_id)}")
    if role:
        out.append(f"role:{role}")
    out.append("global")
    return out


def _row_to_policy(row: GovernancePolicyModel) -> EffectivePolicy:
    allowed_modes = None
    try:
        if row.allowed_modes_json:
            allowed_modes = list(json.loads(row.allowed_modes_json))
    except Exception:  # noqa: BLE001
        allowed_modes = None
    extra: Dict = {}
    try:
        if row.extra_json:
            extra = json.loads(row.extra_json)
    except Exception:  # noqa: BLE001
        pass
    return EffectivePolicy(
        scope=row.scope,
        daily_token_budget=int(row.daily_token_budget or -1),
        qps=int(row.qps or -1),
        masking_level=str(row.masking_level or "loose"),
        allowed_modes=allowed_modes,
        extra=extra,
    )


@with_session
def upsert_policy(
    session: Session, *,
    scope: str,
    daily_token_budget: int = -1,
    qps: int = -1,
    masking_level: str = "loose",
    allowed_modes: Optional[List[str]] = None,
    extra: Optional[Dict] = None,
    enabled: int = 1,
) -> int:
    row = session.query(GovernancePolicyModel).filter(
        GovernancePolicyModel.scope == scope,
    ).one_or_none()
    if row is None:
        row = GovernancePolicyModel(scope=scope)
        session.add(row)
    row.daily_token_budget = int(daily_token_budget)
    row.qps = int(qps)
    row.masking_level = masking_level
    row.allowed_modes_json = json.dumps(allowed_modes or [], ensure_ascii=False)
    row.extra_json = json.dumps(extra or {}, ensure_ascii=False)
    row.enabled = int(enabled)
    session.flush()
    return int(row.id)


@with_session
def list_policies(session: Session) -> List[Dict]:
    return [
        {
            "id": r.id, "scope": r.scope,
            "daily_token_budget": r.daily_token_budget,
            "qps": r.qps, "masking_level": r.masking_level,
            "enabled": r.enabled,
            "allowed_modes": json.loads(r.allowed_modes_json or "[]"),
            "extra": json.loads(r.extra_json or "{}"),
        }
        for r in session.query(GovernancePolicyModel).order_by(GovernancePolicyModel.id.asc()).all()
    ]
