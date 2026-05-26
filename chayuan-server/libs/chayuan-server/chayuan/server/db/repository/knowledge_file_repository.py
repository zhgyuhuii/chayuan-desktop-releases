from typing import TYPE_CHECKING, Dict, List, Optional
import hashlib
import os

from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
from chayuan.server.db.models.knowledge_file_model import (
    FileDocModel,
    KnowledgeFileModel,
)
from chayuan.server.db.models.user_model import UserModel
from chayuan.server.db.session import with_session

# 41 题 P5:KnowledgeFile 仅作类型注解使用,改为 TYPE_CHECKING + 字符串 forward ref。
# 原 ``from chayuan.server.knowledge_base.utils import KnowledgeFile`` 顶层 import
# 拖整条 ``langchain_text_splitters → sentence_transformers → torch`` 链(~3.6 秒),
# 而本仓库文件运行时只读 KnowledgeFile 实例的 .filename / .kb_name 等属性,无需类。
if TYPE_CHECKING:
    from chayuan.server.knowledge_base.utils import KnowledgeFile  # noqa: F401


@with_session
def list_file_num_docs_id_by_kb_name_and_file_name(
    session,
    kb_name: str,
    file_name: str,
) -> List[int]:
    """
    列出某知识库某文件对应的所有Document的id。
    返回形式：[str, ...]
    """
    doc_ids = (
        session.query(FileDocModel.doc_id)
        .filter_by(kb_name=kb_name, file_name=file_name)
        .all()
    )
    return [int(_id[0]) for _id in doc_ids]


@with_session
def list_docs_from_db(
    session,
    kb_name: str,
    file_name: str = None,
    metadata: Dict = {},
) -> List[Dict]:
    """
    列出某知识库某文件对应的所有Document。
    返回形式：[{"id": str, "metadata": dict}, ...]
    """
    docs = session.query(FileDocModel).filter(FileDocModel.kb_name.ilike(kb_name))
    if file_name:
        docs = docs.filter(FileDocModel.file_name.ilike(file_name))
    for k, v in metadata.items():
        docs = docs.filter(FileDocModel.meta_data[k].as_string() == str(v))

    return [{"id": x.doc_id, "metadata": x.metadata} for x in docs.all()]


@with_session
def delete_docs_from_db(
    session,
    kb_name: str,
    file_name: str = None,
) -> List[Dict]:
    """
    删除某知识库某文件对应的所有Document，并返回被删除的Document。
    返回形式：[{"id": str, "metadata": dict}, ...]
    """
    docs = list_docs_from_db(kb_name=kb_name, file_name=file_name)
    query = session.query(FileDocModel).filter(FileDocModel.kb_name.ilike(kb_name))
    if file_name:
        query = query.filter(FileDocModel.file_name.ilike(file_name))
    query.delete(synchronize_session=False)
    session.commit()
    return docs


@with_session
def add_docs_to_db(session, kb_name: str, file_name: str, doc_infos: List[Dict]):
    """
    将某知识库某文件对应的所有Document信息添加到数据库。
    doc_infos形式：[{"id": str, "metadata": dict}, ...]
    """
    # ! 这里会出现doc_infos为None的情况，需要进一步排查
    if doc_infos is None:
        print(
            "输入的server.db.repository.knowledge_file_repository.add_docs_to_db的doc_infos参数为None"
        )
        return False
    for d in doc_infos:
        obj = FileDocModel(
            kb_name=kb_name,
            file_name=file_name,
            doc_id=d["id"],
            meta_data=d["metadata"],
        )
        session.add(obj)
    return True


@with_session
def count_files_from_db(session, kb_name: str) -> int:
    return (
        session.query(KnowledgeFileModel)
        .filter(KnowledgeFileModel.kb_name.ilike(kb_name))
        .count()
    )


@with_session
def list_files_from_db(session, kb_name):
    files = (
        session.query(KnowledgeFileModel)
        .filter(KnowledgeFileModel.kb_name.ilike(kb_name))
        .all()
    )
    docs = [f.file_name for f in files]
    return docs


@with_session
def add_file_to_db(
    session,
    kb_file: "KnowledgeFile",
    docs_count: int = 0,
    custom_docs: bool = False,
    doc_infos: List[Dict] = [],  # 形式：[{"id": str, "metadata": dict}, ...]
    file_hash: Optional[str] = None,
    uploader_id: Optional[int] = None,
):
    kb = session.query(KnowledgeBaseModel).filter_by(kb_name=kb_file.kb_name).first()
    if kb:
        # 如果已经存在该文件，则更新文件信息与版本号
        existing_file: KnowledgeFileModel = (
            session.query(KnowledgeFileModel)
            .filter(
                KnowledgeFileModel.kb_name.ilike(kb_file.kb_name),
                KnowledgeFileModel.file_name.ilike(kb_file.filename),
            )
            .first()
        )
        mtime = kb_file.get_mtime()
        size = kb_file.get_size()

        if existing_file:
            existing_file.file_mtime = mtime
            existing_file.file_size = size
            existing_file.docs_count = docs_count
            existing_file.custom_docs = custom_docs
            if file_hash:
                existing_file.file_hash = file_hash
            if uploader_id is not None:
                existing_file.uploader_id = uploader_id
            existing_file.file_version += 1
        # 否则，添加新文件
        else:
            new_file = KnowledgeFileModel(
                file_name=kb_file.filename,
                file_ext=kb_file.ext,
                kb_name=kb_file.kb_name,
                document_loader_name=kb_file.document_loader_name,
                text_splitter_name=kb_file.text_splitter_name or "SpacyTextSplitter",
                file_mtime=mtime,
                file_size=size,
                file_hash=file_hash,
                uploader_id=uploader_id,
                docs_count=docs_count,
                custom_docs=custom_docs,
            )
            # NULL-safe:老库迁移遗留的 file_count 可能是 NULL,None+1 会抛
            kb.file_count = (kb.file_count or 0) + 1
            session.add(new_file)
        add_docs_to_db(
            kb_name=kb_file.kb_name, file_name=kb_file.filename, doc_infos=doc_infos
        )
    return True


@with_session
def set_file_upload_meta(
    session,
    kb_name: str,
    filename: str,
    file_hash: str = "",
    uploader_id: Optional[int] = None,
) -> bool:
    row = (
        session.query(KnowledgeFileModel)
        .filter(
            KnowledgeFileModel.kb_name.ilike(kb_name),
            KnowledgeFileModel.file_name.ilike(filename),
        )
        .first()
    )
    if not row:
        return False
    if file_hash:
        row.file_hash = file_hash
    if uploader_id is not None:
        row.uploader_id = uploader_id
    return True


@with_session
def find_duplicate_files_by_hashes(
    session,
    hashes: List[str],
    accessible_kbs: Optional[List[str]] = None,
) -> Dict[str, List[Dict]]:
    clean = [h.lower() for h in hashes if h]
    if not clean:
        return {}
    q = (
        session.query(KnowledgeFileModel, KnowledgeBaseModel, UserModel)
        .join(KnowledgeBaseModel, KnowledgeBaseModel.kb_name == KnowledgeFileModel.kb_name)
        .outerjoin(UserModel, UserModel.id == KnowledgeFileModel.uploader_id)
        .filter(KnowledgeFileModel.file_hash.in_(clean))
    )
    if accessible_kbs is not None:
        q = q.filter(KnowledgeFileModel.kb_name.in_(accessible_kbs))
    out: Dict[str, List[Dict]] = {}
    for f, kb, user in q.all():
        h = f.file_hash or ""
        out.setdefault(h, []).append({
            "kb_name": f.kb_name,
            "file_name": f.file_name,
            "file_size": f.file_size,
            "file_hash": h,
            "uploader_id": f.uploader_id,
            "uploader_name": getattr(user, "username", "") if user else "",
            "upload_time": f.create_time,
            "visibility": kb.visibility,
            "owner_id": kb.owner_id,
        })
    return out


@with_session
def backfill_missing_hashes_by_sizes(
    session,
    sizes: List[int],
    accessible_kbs: Optional[List[str]] = None,
    limit: int = 2000,
) -> int:
    """为老数据懒回填 file_hash。

    只扫描与本次上传同 size 且 file_hash 为空的记录,避免预检时全库读文件。
    """
    clean_sizes = [int(x) for x in sizes if int(x or 0) > 0]
    if not clean_sizes:
        return 0
    q = (
        session.query(KnowledgeFileModel)
        .filter(KnowledgeFileModel.file_hash.is_(None))
        .filter(KnowledgeFileModel.file_size.in_(clean_sizes))
    )
    if accessible_kbs is not None:
        q = q.filter(KnowledgeFileModel.kb_name.in_(accessible_kbs))
    rows = q.limit(max(1, int(limit))).all()
    if not rows:
        return 0

    from chayuan.server.knowledge_base.utils import get_file_path

    updated = 0
    for row in rows:
        path = get_file_path(row.kb_name, row.file_name)
        h = hashlib.sha256()
        if path and os.path.isfile(path):
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
        else:
            try:
                from chayuan.server.file_storage import NS, get_storage
                h.update(get_storage().get(NS.KB_CONTENT, f"{row.kb_name}/{row.file_name}"))
            except Exception:
                continue
        row.file_hash = h.hexdigest()
        updated += 1
    return updated


@with_session
def delete_file_from_db(session, kb_file: "KnowledgeFile"):
    existing_file = (
        session.query(KnowledgeFileModel)
        .filter(
            KnowledgeFileModel.file_name.ilike(kb_file.filename),
            KnowledgeFileModel.kb_name.ilike(kb_file.kb_name),
        )
        .first()
    )
    if existing_file:
        session.delete(existing_file)
        delete_docs_from_db(kb_name=kb_file.kb_name, file_name=kb_file.filename)
        session.commit()

        kb = (
            session.query(KnowledgeBaseModel)
            .filter(KnowledgeBaseModel.kb_name.ilike(kb_file.kb_name))
            .first()
        )
        if kb:
            # 下限保护:file_count 绝不允许变负。add_doc 是"先 delete_doc 再
            # do_add_doc 再 add_file_to_db(+1)"——若 do_add_doc 失败(典型:
            # embedding 服务对超长块返 500),后面的 +1 不会执行,这里的 -1 就
            # 成了净漂移。一旦 file_count 变负,该 KB 会被 list_kbs_from_db 的
            # 过滤藏掉(用户看不到、以为已删除),但 load_kb_from_db 仍查得到
            # → 建同名库报"已存在",成了既看不见又建不了的幽灵库。
            kb.file_count = max(0, (kb.file_count or 0) - 1)
            session.commit()
    return True


@with_session
def delete_files_from_db(session, knowledge_base_name: str):
    session.query(KnowledgeFileModel).filter(
        KnowledgeFileModel.kb_name.ilike(knowledge_base_name)
    ).delete(synchronize_session=False)
    session.query(FileDocModel).filter(
        FileDocModel.kb_name.ilike(knowledge_base_name)
    ).delete(synchronize_session=False)
    kb = (
        session.query(KnowledgeBaseModel)
        .filter(KnowledgeBaseModel.kb_name.ilike(knowledge_base_name))
        .first()
    )
    if kb:
        kb.file_count = 0

    session.commit()
    return True


@with_session
def file_exists_in_db(session, kb_file: "KnowledgeFile"):
    existing_file = (
        session.query(KnowledgeFileModel)
        .filter(
            KnowledgeFileModel.file_name.ilike(kb_file.filename),
            KnowledgeFileModel.kb_name.ilike(kb_file.kb_name),
        )
        .first()
    )
    return True if existing_file else False


@with_session
def get_file_detail(session, kb_name: str, filename: str) -> dict:
    file: KnowledgeFileModel = (
        session.query(KnowledgeFileModel)
        .filter(
            KnowledgeFileModel.file_name.ilike(filename),
            KnowledgeFileModel.kb_name.ilike(kb_name),
        )
        .first()
    )
    if file:
        return {
            "kb_name": file.kb_name,
            "file_name": file.file_name,
            "file_ext": file.file_ext,
            "file_version": file.file_version,
            "document_loader": file.document_loader_name,
            "text_splitter": file.text_splitter_name,
            "create_time": file.create_time,
            "file_mtime": file.file_mtime,
            "file_size": file.file_size,
            "file_hash": file.file_hash,
            "uploader_id": file.uploader_id,
            "custom_docs": file.custom_docs,
            "docs_count": file.docs_count,
        }
    else:
        return {}
