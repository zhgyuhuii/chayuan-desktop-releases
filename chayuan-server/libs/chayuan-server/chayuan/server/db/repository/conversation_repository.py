import uuid

from chayuan.server.db.models.conversation_model import ConversationModel
from chayuan.server.db.session import async_session_scope, with_session


@with_session
def add_conversation_to_db(
    session, chat_type, name="", conversation_id=None, user_id=None,
):
    """
    新增聊天记录
    """
    if not conversation_id:
        conversation_id = uuid.uuid4().hex
    c = ConversationModel(
        id=conversation_id, chat_type=chat_type, name=name, user_id=user_id,
    )

    session.add(c)
    return c.id


@with_session
def get_conversation_by_id(session, conversation_id):
    return (
        session.query(ConversationModel)
        .filter(ConversationModel.id == conversation_id)
        .one_or_none()
    )


@with_session
def delete_conversation(session, conversation_id):
    """硬删会话及其消息。"""
    from chayuan.server.db.models.message_model import MessageModel
    session.query(MessageModel).filter(
        MessageModel.conversation_id == conversation_id,
    ).delete(synchronize_session=False)
    return (
        session.query(ConversationModel)
        .filter(ConversationModel.id == conversation_id)
        .delete(synchronize_session=False)
    )


@with_session
def rename_conversation(session, conversation_id, name: str):
    c = (
        session.query(ConversationModel)
        .filter(ConversationModel.id == conversation_id)
        .one_or_none()
    )
    if c is None:
        return False
    c.name = (name or "").strip()[:64]
    return True


# ---------------------------------------------------------------------------
# Async hot-path（T7-c）
# ---------------------------------------------------------------------------


async def add_conversation_to_db_async(
    chat_type: str, name: str = "", conversation_id: str = None, user_id=None,
) -> str:
    async with async_session_scope() as s:
        if s is None:
            return add_conversation_to_db(
                chat_type=chat_type, name=name,
                conversation_id=conversation_id, user_id=user_id,
            )
        if not conversation_id:
            conversation_id = uuid.uuid4().hex
        c = ConversationModel(
            id=conversation_id, chat_type=chat_type, name=name, user_id=user_id,
        )
        s.add(c)
        await s.flush()
        return c.id


@with_session
def list_conversations_for_user(session, user_id, limit: int = 100, offset: int = 0):
    """列出某用户可见的会话。

    - user_id 是 admin 时，调用方自行传 None 并走管理员路径；
    - 其余情况严格按 user_id 过滤；历史 user_id=NULL 的会话**不会**返回（向前隔离）。
    """
    q = session.query(ConversationModel)
    if user_id is not None:
        q = q.filter(ConversationModel.user_id == user_id)
    q = q.order_by(ConversationModel.create_time.desc()).limit(limit).offset(offset)
    rows = q.all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "chat_type": r.chat_type,
            "user_id": r.user_id,
            "create_time": r.create_time.isoformat() if r.create_time else None,
        }
        for r in rows
    ]
