"""血缘记录写入（P1-9）。

每次 chat 结束，落一条主记录 + 若干 touch 子记录：
- 主记录：lineage_id, mode, query, answer_preview, sources_json, tokens, pii_count
- touch 记录：被读到的 table / column / file / collection / index

通过 ``orchestrator.sources_meta`` 里 ``citation.meta.columns`` 等字段反推
被读到的列；不产出任何副作用（只写 DB）。

查询侧提供：
- list_lineage(user_id, time_range, mode)  — 管理面板 / 审计
- top_touched_objects(time_range)          — "哪些字段最常被 AI 读到"
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from chayuan.server.db.models.governance_model import (
    LineageRecordModel,
    LineageTouchModel,
    UsageCounterModel,
)
from chayuan.server.db.session import session_scope, with_session

logger = logging.getLogger("chayuan.governance.lineage")


@with_session
def record_chat(
    session: Session,
    *,
    user_id: Optional[int],
    username: str,
    conversation_id: str,
    request_id: str,
    mode: str,
    query: str,
    answer_preview: str,
    llm_model: str,
    sources: List[Dict[str, Any]],
    retrieved_chunks: List[Dict[str, Any]],
    pii_count: int = 0,
    tokens_total: int = 0,
) -> Optional[int]:
    try:
        row = LineageRecordModel(
            user_id=(int(user_id) if user_id is not None else None),
            username=(username or "")[:128],
            conversation_id=(conversation_id or "")[:64],
            request_id=(request_id or "")[:64],
            mode=(mode or "")[:32],
            llm_model=(llm_model or "")[:80],
            tokens_total=int(tokens_total or 0),
            pii_count=int(pii_count or 0),
            query=(query or "")[:4000],
            answer_preview=(answer_preview or "")[:500],
            sources_json=_dump(sources)[:8000],
        )
        session.add(row)
        session.flush()
        lineage_id = int(row.id)

        # touch 子记录
        for t in _extract_touches(sources, retrieved_chunks):
            session.add(LineageTouchModel(
                lineage_id=lineage_id,
                source_id=t.get("source_id"),
                object_type=str(t.get("object_type") or "")[:16],
                object_name=str(t.get("object_name") or "")[:255],
                qualified_name=str(t.get("qualified_name") or "")[:512],
            ))

        # 累计用量（日粒度）
        _bump_usage(session, user_id, mode=mode, tokens=int(tokens_total or 0))

        return lineage_id
    except Exception as e:  # noqa: BLE001
        logger.debug("lineage.record_chat 失败：%r", e)
        return None


def _dump(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return ""


def _extract_touches(
    sources: List[Dict], chunks: List[Dict],
) -> List[Dict[str, Any]]:
    """从 orchestrator.sources_meta 与 retrieved_chunks 里抽"动过哪些对象"。"""
    out: List[Dict[str, Any]] = []
    for s in sources or []:
        sid = s.get("source_id")
        kind = s.get("kind")
        cname = s.get("name") or ""
        gq = s.get("generated_query") or ""
        # SQL：citation.meta.columns 存在时逐列记
        for ch in s.get("chunks") or []:
            cite = ch.get("citation") or {}
            meta = cite.get("meta") or {}
            if kind == "sql":
                cols = meta.get("columns") or []
                for col in cols:
                    out.append({
                        "source_id": sid, "object_type": "column",
                        "object_name": str(col),
                        "qualified_name": f"source:{cname}.{col}",
                    })
                if not cols and gq:
                    out.append({
                        "source_id": sid, "object_type": "table",
                        "object_name": "inferred",
                        "qualified_name": f"source:{cname}",
                    })
            elif kind == "mongo":
                col = meta.get("collection") or ""
                if col:
                    out.append({
                        "source_id": sid, "object_type": "collection",
                        "object_name": str(col),
                        "qualified_name": f"source:{cname}.{col}",
                    })
            elif kind == "es":
                idx = meta.get("index") or ""
                if idx:
                    out.append({
                        "source_id": sid, "object_type": "index",
                        "object_name": str(idx),
                        "qualified_name": f"source:{cname}.{idx}",
                    })
            elif kind == "vector":
                title = cite.get("title") or ""
                if title:
                    out.append({
                        "source_id": sid, "object_type": "file",
                        "object_name": str(title),
                        "qualified_name": f"source:{cname}/{title}",
                    })
    # chunks 里兜底一次（单 KB 场景 sources 列表可能为空）
    for ch in chunks or []:
        cite = ch.get("citation") or {}
        if (ch.get("source_kind") or "") == "vector":
            title = cite.get("title") or ""
            if title:
                out.append({
                    "source_id": ch.get("source_id"),
                    "object_type": "file",
                    "object_name": str(title),
                    "qualified_name": f"vector/{title}",
                })
    # 去重
    seen = set()
    dedup: List[Dict[str, Any]] = []
    for o in out:
        k = (o.get("source_id"), o.get("object_type"), o.get("object_name"))
        if k in seen:
            continue
        seen.add(k)
        dedup.append(o)
    return dedup


def _bump_usage(session: Session, user_id: Optional[int], mode: str, tokens: int) -> None:
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = session.query(UsageCounterModel).filter(
            UsageCounterModel.user_id == (int(user_id) if user_id is not None else None),
            UsageCounterModel.date_bucket == today,
            UsageCounterModel.mode == (mode or ""),
        ).one_or_none()
        if row is None:
            row = UsageCounterModel(
                user_id=(int(user_id) if user_id is not None else None),
                date_bucket=today, mode=(mode or ""),
                request_count=1, tokens_total=int(tokens or 0),
            )
            session.add(row)
        else:
            row.request_count = int(row.request_count or 0) + 1
            row.tokens_total = int(row.tokens_total or 0) + int(tokens or 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("_bump_usage 失败（忽略）：%r", e)


# ---------------------------------------------------------------------------
# 查询（管理面板 / 审计）
# ---------------------------------------------------------------------------

@with_session
def list_lineage(
    session: Session,
    user_id: Optional[int] = None,
    mode: Optional[str] = None,
    hours: int = 24,
    limit: int = 200,
) -> List[Dict]:
    since = datetime.utcnow() - timedelta(hours=int(hours or 24))
    q = session.query(LineageRecordModel).filter(LineageRecordModel.created_at >= since)
    if user_id is not None:
        q = q.filter(LineageRecordModel.user_id == int(user_id))
    if mode:
        q = q.filter(LineageRecordModel.mode == mode)
    rows = q.order_by(LineageRecordModel.id.desc()).limit(int(limit)).all()
    return [
        {
            "id": r.id, "user_id": r.user_id, "username": r.username,
            "mode": r.mode, "llm_model": r.llm_model,
            "tokens_total": r.tokens_total, "pii_count": r.pii_count,
            "query": r.query, "answer_preview": r.answer_preview,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@with_session
def top_touched_objects(
    session: Session,
    object_type: str = "",
    hours: int = 168,  # 一周
    limit: int = 50,
) -> List[Dict]:
    """返回被读最多的对象（表/列/文件）排行。"""
    from sqlalchemy import func
    since = datetime.utcnow() - timedelta(hours=int(hours or 168))
    q = session.query(
        LineageTouchModel.object_type,
        LineageTouchModel.qualified_name,
        func.count(LineageTouchModel.id).label("hits"),
    ).join(
        LineageRecordModel, LineageRecordModel.id == LineageTouchModel.lineage_id,
    ).filter(LineageRecordModel.created_at >= since)
    if object_type:
        q = q.filter(LineageTouchModel.object_type == object_type)
    q = q.group_by(LineageTouchModel.object_type, LineageTouchModel.qualified_name)\
         .order_by(func.count(LineageTouchModel.id).desc()).limit(int(limit))
    return [
        {"object_type": r[0], "qualified_name": r[1], "hits": int(r[2])}
        for r in q.all()
    ]
