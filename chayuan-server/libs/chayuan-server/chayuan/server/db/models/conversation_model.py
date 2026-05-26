from sqlalchemy import JSON, Column, DateTime, Integer, String, func

from chayuan.server.db.base import Base


class ConversationModel(Base):
    """
    聊天记录模型
    """

    __tablename__ = "conversation"
    id = Column(String(32), primary_key=True, comment="对话框ID")
    name = Column(String(50), comment="对话框名称")
    chat_type = Column(String(50), comment="聊天类型")
    # P3 多用户隔离：历史数据为 NULL 视为"匿名/legacy"会话，仅管理员可见
    user_id = Column(Integer, nullable=True, index=True, comment="归属用户 users.id")
    create_time = Column(DateTime, default=func.now(), comment="创建时间")

    def __repr__(self):
        return (
            f"<Conversation(id='{self.id}', name='{self.name}', chat_type='{self.chat_type}',"
            f" user_id={self.user_id}, create_time='{self.create_time}')>"
        )
