"""``modality_task`` 表 — 模态生成任务的持久化记录。

为什么独立一张表
================
模态任务跟 ``conversation`` / ``message`` 是两条生命周期:
  - 任务可能在没有任何 conversation 关联的情况下运行(管理面板试跑、API 直调)
  - 任务结束后 message 可能已经被删除,但出图/出音频仍然要可访问

所以单独一张 ``modality_task``,字段是任务自描述的:capability / model / status /
upstream_task_id / files,任何客户端拿到 task_id 就能完整还原渲染。

字段说明
========
- ``id``                : task uuid(主键)
- ``capability``        : t2i / image_edit / t2v / tts / asr / vl / chat
- ``model``             : 上游模型名(qwen-image-max / wanx-v1 / cosyvoice-v1 …)
- ``platform_name``     : 用户在 model_platform 表里给这条平台起的别名(`bailian` / `openai-cn-1` …)
- ``status``            : pending → running → succeeded / failed / cancelled
- ``progress_percent``  : 0-100
- ``progress_message``  : 当前阶段说明("提交请求" / "下载产物" / "解码音频" …)
- ``upstream_task_id``  : 上游真正的 task id(wanx async / paraformer 流式有)
- ``params_json``       : 提交时的参数,用于回放 / 调试
- ``attachments_meta``  : 附件的 mime / url / name 列表(不存原始 bytes,artifacts 表才是)
- ``files_json``        : 产物列表(list[dict])
- ``text_out``          : 文字产物(ASR / chat 模式)
- ``error_text``/``error_code`` : 失败时的诊断
- ``user_id``           : 归属用户(多用户隔离用,允许 NULL = legacy)
- ``conversation_id``   : 关联会话(允许 NULL)
- ``message_id``        : 关联消息(允许 NULL)

为什么没有 ``events_json``
==========================
事件流(text-delta / data-progress …)按 task 串可能上千条,塞到一行 JSON 里既
不利于追加也容易把行膨成 MB 级。事件总线在内存里做 ring buffer(见
``event_bus.py``),订阅者通过 "replay 最近 N 条 + 转播实时" 拿到完整流;
落库的只有"结果状态"——文字 / 文件 / 错误,这些回放够用。

为什么不分多表(modality_task_event / modality_task_file)
========================================================
单机桌面级,任务量 <1k/日;一张宽表足够,join 反而拖性能。多表方案留给上云。
"""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, func

from chayuan.server.db.base import Base


class ModalityTaskModel(Base):
    """模态生成任务持久化记录。"""

    __tablename__ = "modality_task"

    id = Column(String(40), primary_key=True, comment="任务 UUID(无连字符的 hex)")
    capability = Column(String(20), nullable=False, index=True, comment="t2i/t2v/tts/asr/image_edit/...")
    model = Column(String(80), nullable=False, comment="上游模型名")
    platform_name = Column(String(80), nullable=True, comment="model_platform 表的平台别名")

    status = Column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        comment="pending|running|succeeded|failed|cancelled",
    )
    progress_percent = Column(Integer, nullable=False, default=0, comment="0-100")
    progress_message = Column(String(120), nullable=True, comment="阶段说明")

    upstream_task_id = Column(String(120), nullable=True, comment="上游真实 task id(wanx async / paraformer)")

    prompt = Column(Text, nullable=True, comment="提交文本(t2i prompt / tts text / asr 热词)")
    params_json = Column(JSON, nullable=True, comment="modality_params 透传字段")
    attachments_meta = Column(JSON, nullable=True, comment="[{mime,url,name}] — 附件清单不含 bytes")

    files_json = Column(JSON, nullable=True, comment="[{mediaType,url,metadata}] — 产物清单")
    text_out = Column(Text, nullable=True, comment="文字产物(ASR / chat 模式)")

    error_text = Column(Text, nullable=True, comment="失败诊断")
    error_code = Column(String(60), nullable=True, comment="connector 给的错误码")

    user_id = Column(Integer, nullable=True, index=True, comment="归属 users.id;NULL=legacy")
    conversation_id = Column(String(32), nullable=True, index=True, comment="关联会话")
    message_id = Column(String(40), nullable=True, comment="关联消息")

    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    finished_at = Column(DateTime, nullable=True, comment="终态时间;非终态为 NULL")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ModalityTask(id={self.id} cap={self.capability} model={self.model} "
            f"status={self.status} percent={self.progress_percent})>"
        )
