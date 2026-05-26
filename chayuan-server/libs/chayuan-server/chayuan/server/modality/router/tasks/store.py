"""``modality_task`` 表的 CRUD 封装。

线程模型
========
chayuan-server 走 ``SessionLocal`` 同步 session,而 TaskManager 在 asyncio
loop 里跑后台轮询任务。**不要**在 loop 里直接持有 session(SQLite 单写者
+ 跨线程 cursor 容易死锁);本模块所有 DB 调用都通过 ``asyncio.to_thread``
包成 awaitable,把同步 IO 推到 thread pool。

写频率
======
模态任务 progress event 可能 1-2 秒一条,SSE 流可能秒级 token-delta。
**不每个事件都落库**:
  - text-delta 不落,只在 text-end 时把整段写 ``text_out``
  - progress 节流:5% 一档,或每 3 秒最多一条
  - file_part / error / status 转换:必落
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from chayuan.server.db.base import SessionLocal
from chayuan.server.modality.router.tasks.db_model import ModalityTaskModel

logger = logging.getLogger("chayuan.modality.tasks.store")


# ──────────────────────────────────────────────────────────────
# 同步实现 — 在 thread pool 跑(避免 SQLite cursor 跨线程)
# ──────────────────────────────────────────────────────────────


def _row_to_dict(row: ModalityTaskModel) -> Dict[str, Any]:
    return {
        "id": row.id,
        "capability": row.capability,
        "model": row.model,
        "platform_name": row.platform_name,
        "status": row.status,
        "progress_percent": int(row.progress_percent or 0),
        "progress_message": row.progress_message,
        "upstream_task_id": row.upstream_task_id,
        "prompt": row.prompt,
        "params": row.params_json or {},
        "attachments": row.attachments_meta or [],
        "files": row.files_json or [],
        "text_out": row.text_out,
        "error_text": row.error_text,
        "error_code": row.error_code,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "message_id": row.message_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def _create_sync(
    *,
    task_id: str,
    capability: str,
    model: str,
    platform_name: Optional[str],
    prompt: Optional[str],
    params: Optional[Dict[str, Any]],
    attachments: Optional[List[Dict[str, Any]]],
    user_id: Optional[int],
    conversation_id: Optional[str],
    message_id: Optional[str],
) -> Dict[str, Any]:
    session: Session = SessionLocal()
    try:
        row = ModalityTaskModel(
            id=task_id,
            capability=capability,
            model=model,
            platform_name=platform_name,
            status="pending",
            progress_percent=0,
            prompt=prompt,
            params_json=params or {},
            attachments_meta=attachments or [],
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    finally:
        session.close()


def _patch_sync(task_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """部分更新 — 只覆盖传入 keys;终态(succeeded/failed/cancelled)自动写 finished_at。"""
    session: Session = SessionLocal()
    try:
        row = session.query(ModalityTaskModel).filter_by(id=task_id).one_or_none()
        if row is None:
            return None
        # 常规字段
        for k, v in fields.items():
            if v is None and k not in ("error_text", "error_code", "upstream_task_id"):
                # 大部分字段不允许"显式置 None";None=不动
                continue
            if hasattr(row, k):
                setattr(row, k, v)
        # 终态时间戳
        if fields.get("status") in ("succeeded", "failed", "cancelled"):
            row.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    finally:
        session.close()


def _append_file_sync(task_id: str, file_part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    session: Session = SessionLocal()
    try:
        row = session.query(ModalityTaskModel).filter_by(id=task_id).one_or_none()
        if row is None:
            return None
        files = list(row.files_json or [])
        files.append(file_part)
        row.files_json = files
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    finally:
        session.close()


def _get_sync(task_id: str) -> Optional[Dict[str, Any]]:
    session: Session = SessionLocal()
    try:
        row = session.query(ModalityTaskModel).filter_by(id=task_id).one_or_none()
        return _row_to_dict(row) if row is not None else None
    finally:
        session.close()


def _list_unfinished_sync(limit: int) -> List[Dict[str, Any]]:
    session: Session = SessionLocal()
    try:
        rows = (
            session.query(ModalityTaskModel)
            .filter(ModalityTaskModel.status.in_(["pending", "running"]))
            .order_by(ModalityTaskModel.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_row_to_dict(r) for r in rows]
    finally:
        session.close()


# ──────────────────────────────────────────────────────────────
# Async 包装 — 给 TaskManager 调用
# ──────────────────────────────────────────────────────────────


async def create_task(
    *,
    task_id: str,
    capability: str,
    model: str,
    platform_name: Optional[str] = None,
    prompt: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    user_id: Optional[int] = None,
    conversation_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        _create_sync,
        task_id=task_id,
        capability=capability,
        model=model,
        platform_name=platform_name,
        prompt=prompt,
        params=params,
        attachments=attachments,
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


async def patch_task(task_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_patch_sync, task_id, **fields)


async def append_file(task_id: str, file_part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_append_file_sync, task_id, file_part)


async def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_get_sync, task_id)


async def list_unfinished(limit: int = 200) -> List[Dict[str, Any]]:
    """列出未终态任务 — 进程重启后恢复轮询用。"""
    return await asyncio.to_thread(_list_unfinished_sync, limit)
