from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, func

from chayuan.server.db.base import Base


class KnowledgeBaseModel(Base):
    """
    知识库模型
    """

    __tablename__ = "knowledge_base"
    id = Column(Integer, primary_key=True, autoincrement=True, comment="知识库ID")
    kb_name = Column(String(200), comment="知识库名称")
    kb_info = Column(String(200), comment="知识库简介(用于Agent)")
    vector_namespace = Column(String(64), nullable=True, unique=True, index=True, comment="向量库内部集合/索引名")
    vs_type = Column(String(50), comment="向量库类型")
    embed_model = Column(String(50), comment="嵌入模型名称")
    file_count = Column(Integer, default=0, comment="文件数量")
    # P3 多用户：owner_id=NULL 视为 legacy/共享；visibility 控制默认可见范围
    owner_id = Column(Integer, nullable=True, index=True, comment="创建者 users.id")
    visibility = Column(
        String(16), default="private", nullable=False, index=True,
        comment="private：仅 owner + 授权用户；public：所有登录用户可读",
    )
    # 存储后端：NULL / ''  = 跟随全局 Settings.FILE_STORAGE_BACKEND
    #           'local'    = 强制本地磁盘
    #           'minio'    = 强制 MinIO / S3
    # 允许不同 KB 挂不同后端（敏感库留本地、海量库上对象存储）
    storage_backend = Column(
        String(16), nullable=True, index=True,
        comment="按 KB 存储后端覆盖：NULL=全局默认 / 'local' / 'minio'",
    )
    create_time = Column(DateTime, default=func.now(), comment="创建时间")

    def __repr__(self):
        return (
            f"<KnowledgeBase(id='{self.id}', kb_name='{self.kb_name}',"
            f"kb_intro='{self.kb_info} vs_type='{self.vs_type}',"
            f" embed_model='{self.embed_model}', file_count='{self.file_count}',"
            f" owner_id={self.owner_id}, visibility={self.visibility},"
            f" create_time='{self.create_time}')>"
        )


# 创建一个对应的 Pydantic 模型
class KnowledgeBaseSchema(BaseModel):
    id: int
    kb_name: str
    kb_info: Optional[str]
    vector_namespace: Optional[str] = None
    vs_type: Optional[str]
    embed_model: Optional[str]
    file_count: Optional[int]
    owner_id: Optional[int] = None
    visibility: Optional[str] = "private"
    storage_backend: Optional[str] = None
    create_time: Optional[datetime]

    class Config:
        from_attributes = True  # 确保可以从 ORM 实例进行验证
