from chayuan.server.db.models.knowledge_base_model import (
    KnowledgeBaseModel,
    KnowledgeBaseSchema,
)
from chayuan.server.db.session import with_session


@with_session
def add_kb_to_db(session, kb_name, kb_info, vs_type, embed_model,
                  storage_backend=None, vector_namespace=None):
    """新建或更新 KB 元数据。

    ``storage_backend``：
      - ``None`` / ``""``：保持旧值（新建时即为"跟随全局"）
      - ``"local"`` / ``"minio"``：显式覆盖
    """
    kb = (
        session.query(KnowledgeBaseModel)
        .filter(KnowledgeBaseModel.kb_name.ilike(kb_name))
        .first()
    )
    if not kb:
        kb = KnowledgeBaseModel(
            kb_name=kb_name, kb_info=kb_info,
            vs_type=vs_type, embed_model=embed_model,
            vector_namespace=vector_namespace,
            storage_backend=(storage_backend or None),
        )
        session.add(kb)
    else:
        kb.kb_info = kb_info
        kb.vs_type = vs_type
        kb.embed_model = embed_model
        if vector_namespace and not kb.vector_namespace:
            kb.vector_namespace = vector_namespace
        if storage_backend:
            kb.storage_backend = storage_backend
    return True


@with_session
def get_kb_vector_namespace(session, kb_name: str) -> str:
    kb = (
        session.query(KnowledgeBaseModel.vector_namespace)
        .filter(KnowledgeBaseModel.kb_name.ilike(kb_name))
        .first()
    )
    if kb and kb[0]:
        return str(kb[0]).strip()
    return ""


@with_session
def get_kb_storage_backend(session, kb_name: str) -> str:
    """返回 KB 的 storage_backend；NULL/空回空串。不加锁查询快速路径。"""
    kb = (
        session.query(KnowledgeBaseModel.storage_backend)
        .filter(KnowledgeBaseModel.kb_name.ilike(kb_name))
        .first()
    )
    if kb and kb[0]:
        return str(kb[0]).strip().lower()
    return ""


@with_session
def set_kb_storage_backend(session, kb_name: str, backend: str) -> bool:
    """更新 KB 的 storage_backend。``backend`` 空串 = 恢复为全局默认。"""
    backend = (backend or "").strip().lower() or None
    if backend and backend not in ("local", "minio"):
        raise ValueError(f"storage_backend 只能是 local / minio，收到 {backend!r}")
    kb = (
        session.query(KnowledgeBaseModel)
        .filter(KnowledgeBaseModel.kb_name.ilike(kb_name))
        .first()
    )
    if not kb:
        return False
    kb.storage_backend = backend
    return True


@with_session
def list_kbs_from_db(session, min_file_count: int = -1):
    q = session.query(KnowledgeBaseModel)
    # min_file_count < 0 是"列出全部"语义。绝不能再用 file_count > min_file_count
    # 当过滤 —— 一旦某 KB 的 file_count 因统计漂移变成负数 / NULL,它会从所有
    # 列表与 UI 里消失,但 load_kb_from_db / create_kb 的重名检测仍查得到,
    # 表现为"知识库看不见、却又建不了同名的"幽灵库。只有调用方显式要求
    # min_file_count >= 0(按文件数过滤)时才加这个 WHERE。
    if min_file_count is not None and min_file_count >= 0:
        q = q.filter(KnowledgeBaseModel.file_count > min_file_count)
    kbs = []
    for kb in q.all():
        schema = KnowledgeBaseSchema.model_validate(kb)
        # 统计漂移兜底:对外恒不暴露负数 / NULL 文件数
        if schema.file_count is None or schema.file_count < 0:
            schema.file_count = 0
        kbs.append(schema)
    return kbs


@with_session
def kb_exists(session, kb_name):
    kb = (
        session.query(KnowledgeBaseModel)
        .filter(KnowledgeBaseModel.kb_name.ilike(kb_name))
        .first()
    )
    status = True if kb else False
    return status


@with_session
def load_kb_from_db(session, kb_name):
    kb = (
        session.query(KnowledgeBaseModel)
        .filter(KnowledgeBaseModel.kb_name.ilike(kb_name))
        .first()
    )
    if kb:
        kb_name, vs_type, embed_model = kb.kb_name, kb.vs_type, kb.embed_model
    else:
        kb_name, vs_type, embed_model = None, None, None
    return kb_name, vs_type, embed_model


@with_session
def delete_kb_from_db(session, kb_name):
    kb = (
        session.query(KnowledgeBaseModel)
        .filter(KnowledgeBaseModel.kb_name.ilike(kb_name))
        .first()
    )
    if kb:
        session.delete(kb)
    return True


@with_session
def get_kb_detail(session, kb_name: str) -> dict:
    kb: KnowledgeBaseModel = (
        session.query(KnowledgeBaseModel)
        .filter(KnowledgeBaseModel.kb_name.ilike(kb_name))
        .first()
    )
    if kb:
        return {
            "kb_name": kb.kb_name,
            "kb_info": kb.kb_info,
            "vector_namespace": kb.vector_namespace or "",
            "vs_type": kb.vs_type,
            "embed_model": kb.embed_model,
            "file_count": kb.file_count,
            "storage_backend": kb.storage_backend or "",
            "create_time": kb.create_time,
        }
    else:
        return {}
