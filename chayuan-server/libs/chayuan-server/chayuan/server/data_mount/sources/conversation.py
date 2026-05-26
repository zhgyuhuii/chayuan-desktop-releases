"""历史对话数据源 —— 抽取用户给好评的对话作 fewshot。"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List

from chayuan.server.data_mount.base import (
    DocumentRecord, ProbeResult, SampleResult, SourceSpec,
)
from chayuan.server.data_mount.schema_analyzer import analyze_schema

logger = logging.getLogger("chayuan.data_mount.sources.conversation")


class ConversationSource:
    type_id = "conversation"
    label = "历史对话"
    description = "用户/管理员标过 thumbs-up 的历史对话 → fewshot / preference 训练样本"
    icon = "message-square"
    capabilities = ["fewshot", "preference"]

    def spec_form(self) -> Dict[str, Any]:
        return {"fields": [
            {"name": "min_thumbs", "label": "最少点赞数", "type": "int", "default": 1,
             "help": "0 = 不过滤,默认只挑过 thumbs-up ≥ 1 的"},
            {"name": "user_id", "label": "限定用户 ID(空=全部)", "type": "int", "default": 0},
            {"name": "task_type", "label": "限定任务类型 (chat / agent)", "type": "string",
             "default": ""},
        ]}

    def probe(self, spec: SourceSpec) -> ProbeResult:
        try:
            from chayuan.server.db.session import session_scope
            from chayuan.server.db.models.conversation_model import ConversationModel  # type: ignore

            with session_scope() as session:
                count = session.query(ConversationModel).count()
            return ProbeResult(status="ok", message=f"对话总数 {count}", counted=count)
        except Exception as e:  # noqa: BLE001
            return ProbeResult(status="warning", message=f"对话表不可用或为空: {e}")

    def sample(self, spec: SourceSpec, n: int = 20) -> SampleResult:
        items = self._fetch(spec, limit=n)
        return SampleResult(items=items, fields=analyze_schema(items))

    async def load(self, spec: SourceSpec) -> AsyncIterator[DocumentRecord]:
        for rec in self._fetch(spec, limit=int(spec.max_items or 200)):
            yield rec

    def _fetch(self, spec: SourceSpec, *, limit: int) -> List[DocumentRecord]:
        try:
            from chayuan.server.db.session import session_scope
            from chayuan.server.db.models.conversation_model import ConversationModel  # type: ignore
            from chayuan.server.db.models.message_model import MessageModel  # type: ignore
        except Exception as e:  # noqa: BLE001
            logger.warning("conversation models import failed: %s", e)
            return []
        opts = spec.options
        out: List[DocumentRecord] = []
        try:
            with session_scope() as session:
                q = session.query(ConversationModel)
                uid = int(opts.get("user_id") or 0)
                if uid:
                    q = q.filter(ConversationModel.user_id == uid)
                tt = (opts.get("task_type") or "").strip()
                if tt:
                    q = q.filter(ConversationModel.task_type == tt)
                q = q.order_by(ConversationModel.create_time.desc()).limit(limit * 2)
                conversations = q.all()
                min_thumbs = int(opts.get("min_thumbs") or 1)
                for conv in conversations:
                    if len(out) >= limit:
                        break
                    msgs = session.query(MessageModel).filter(
                        MessageModel.conversation_id == conv.id
                    ).order_by(MessageModel.create_time.asc()).all()
                    # 简单成对: user → assistant
                    for i in range(len(msgs) - 1):
                        m_user = msgs[i]
                        m_asst = msgs[i + 1]
                        if (getattr(m_user, "role", "") != "user" or
                            getattr(m_asst, "role", "") != "assistant"):
                            continue
                        thumbs = int(getattr(m_asst, "thumbs", 0) or 0)
                        if thumbs < min_thumbs:
                            continue
                        out.append(DocumentRecord(
                            text=f"Q: {getattr(m_user, 'content', '')}\nA: {getattr(m_asst, 'content', '')}",
                            metadata={
                                "query": getattr(m_user, "content", ""),
                                "answer": getattr(m_asst, "content", ""),
                                "thumbs": thumbs,
                                "conversation_id": str(conv.id),
                            },
                            id=str(getattr(m_asst, "id", "")) or None,
                        ))
                        if len(out) >= limit:
                            break
        except Exception as e:  # noqa: BLE001
            logger.warning("conversation fetch failed: %s", e)
        return out


ADAPTER = ConversationSource
