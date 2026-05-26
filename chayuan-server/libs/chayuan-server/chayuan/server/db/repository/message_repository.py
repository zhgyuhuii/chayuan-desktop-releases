import uuid
from typing import Dict, List

from chayuan.server.db.models.message_model import MessageModel
from chayuan.server.db.session import async_session_scope, with_session


@with_session
def add_message_to_db(
    session,
    conversation_id: str,
    chat_type,
    query,
    response="",
    message_id=None,
    metadata: Dict = {},
    user_id=None,
):
    """
    新增聊天记录
    """
    if not message_id:
        message_id = uuid.uuid4().hex
    m = MessageModel(
        id=message_id,
        chat_type=chat_type,
        query=query,
        response=response,
        conversation_id=conversation_id,
        user_id=user_id,
        meta_data=metadata,
    )
    session.add(m)
    session.commit()
    return m.id


@with_session
def update_message(session, message_id, response: str = None, metadata: Dict = None):
    """
    更新已有的聊天记录
    """
    m = get_message_by_id(message_id)
    if m is not None:
        if response is not None:
            m.response = response
        if isinstance(metadata, dict):
            m.meta_data = metadata
        session.add(m)
        session.commit()
        return m.id


@with_session
def get_message_by_id(session, message_id) -> MessageModel:
    """
    查询聊天记录
    """
    m = session.query(MessageModel).filter_by(id=message_id).first()
    return m


@with_session
def feedback_message_to_db(session, message_id, feedback_score, feedback_reason):
    """
    反馈聊天记录
    """
    m = session.query(MessageModel).filter_by(id=message_id).first()
    if m:
        m.feedback_score = feedback_score
        m.feedback_reason = feedback_reason
    session.commit()
    return m.id


# ---------------------------------------------------------------------------
# Async hot-path（T7-c）：dispatch_chat / runner 读写消息的主链路
# ---------------------------------------------------------------------------


async def add_message_to_db_async(
    conversation_id: str,
    chat_type,
    query,
    response: str = "",
    message_id=None,
    metadata: Dict = {},
    user_id=None,
) -> str:
    """async 版 ``add_message_to_db``。async 引擎不可用时透明回退 sync。"""
    async with async_session_scope() as s:
        if s is None:
            return add_message_to_db(
                conversation_id=conversation_id, chat_type=chat_type,
                query=query, response=response, message_id=message_id,
                metadata=metadata, user_id=user_id,
            )
        if not message_id:
            message_id = uuid.uuid4().hex
        m = MessageModel(
            id=message_id, chat_type=chat_type, query=query, response=response,
            conversation_id=conversation_id, user_id=user_id, meta_data=metadata,
        )
        s.add(m)
        await s.flush()
        return m.id


async def update_message_async(
    message_id: str, response: str = None, metadata: Dict = None,
) -> str:
    """async 版 ``update_message``。"""
    async with async_session_scope() as s:
        if s is None:
            return update_message(
                message_id=message_id, response=response, metadata=metadata,
            )
        from sqlalchemy import select
        m = (await s.execute(
            select(MessageModel).where(MessageModel.id == message_id)
        )).scalar_one_or_none()
        if m is None:
            return message_id
        if response is not None:
            m.response = response
        if isinstance(metadata, dict):
            m.meta_data = metadata
        await s.flush()
        return m.id


async def filter_message_async(
    conversation_id: str, limit: int = 10,
) -> List[Dict]:
    """async 版 ``filter_message``。"""
    async with async_session_scope() as s:
        if s is None:
            return filter_message(conversation_id=conversation_id, limit=limit)
        from sqlalchemy import select
        stmt = (
            select(MessageModel)
            .where(MessageModel.conversation_id == conversation_id)
            .where(MessageModel.response != "")
            .order_by(MessageModel.create_time.desc())
        )
        if limit >= 0:
            stmt = stmt.limit(limit)
        rows = (await s.execute(stmt)).scalars().all()
        return [
            {"query": m.query, "response": m.response, "metadata": m.meta_data}
            for m in rows
        ]


@with_session
def filter_message(session, conversation_id: str, limit: int = 10):
    # limit < 0 表示不限制条数（API 默认 history_len=-1）。SQLite 曾用 LIMIT -1 表示无上限；
    # PostgreSQL 禁止 LIMIT 为负，故仅在 limit >= 0 时加 limit。
    q = (
        session.query(MessageModel)
        .filter_by(conversation_id=conversation_id)
        # 用户最新的 query 也会插入到 db，忽略这个 message record
        .filter(MessageModel.response != "")
        .order_by(MessageModel.create_time.desc())
    )
    if limit >= 0:
        q = q.limit(limit)
    messages = q.all()
    # 直接返回 List[MessageModel] 报错
    data = []
    for m in messages:
        data.append({"query": m.query, "response": m.response, "metadata": m.meta_data})
    return data
