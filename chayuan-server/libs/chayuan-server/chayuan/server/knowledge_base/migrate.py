import os
from datetime import datetime
from typing import TYPE_CHECKING, List, Literal, Optional

from dateutil.parser import parse

from chayuan.settings import Settings
from chayuan.server.db.base import Base, engine

# 副作用 import:把全部 ORM model 类挂到 ``Base.metadata`` 上,
# 否则 ``create_all`` 漏建 model_platform / model_metadata / annotation 等表,
# 单机 SQLite 启动会出现 ``no such table: model_platform``。
from chayuan.server.db import models as _all_models  # noqa: F401

from chayuan.server.db.models.conversation_model import ConversationModel
from chayuan.server.db.models.message_model import MessageModel
from chayuan.server.db.repository.knowledge_file_repository import (
    add_file_to_db,
)

# ensure Models are imported
from chayuan.server.db.repository.knowledge_metadata_repository import (
    add_summary_to_db,
)
# ensure Models are imported
from chayuan.server.db.repository.mcp_connection_repository import (
    create_mcp_profile,
)
from chayuan.server.db.session import session_scope
from chayuan.utils import build_logger

# 41 题 P5:把 ``kb_service.base`` 和 ``knowledge_base.utils`` 推到使用它们的
# 函数内部 — 那两个模块顶层 import 链路最终会拖出 ``langchain_text_splitters``
# → ``sentence_transformers`` → ``transformers`` → ``torch`` ≈ 3.6 秒。
# **`create_tables` / `reset_tables` 完全不用** 它们,但 chayuan start -a
# 路径里 ``from migrate import create_tables`` 现状会一并触发整条 ML 链。
# 用 TYPE_CHECKING block 仅给类型注解用,运行时类型注解走字符串 forward ref。
if TYPE_CHECKING:
    from chayuan.server.knowledge_base.utils import KnowledgeFile  # noqa: F401


logger = build_logger()


def create_tables():
    # 启动链路 bootstrap：按 basic_settings.yaml 的 URI 先确保目标库存在；
    # 若 PG/MySQL 报「database does not exist / Unknown database」，就按 init_scripts
    # 里的建库 SQL 自动建好，避免 Base.metadata.create_all 直接崩启动。
    # sqlite 走 touch 文件路径，也收敛在这里。
    try:
        from chayuan.server.config_panel.db_config import ensure_database_from_uri

        uri = Settings.basic_settings.SQLALCHEMY_DATABASE_URI
        res = ensure_database_from_uri(uri, timeout=5)
        if res.get("created"):
            logger.warning(
                f"[db-bootstrap] 目标数据库不存在，已自动创建：{res.get('message')}"
            )
        elif not res.get("ok"):
            # 让 create_all 抛出原始错误，保留调用栈方便排查；
            # 这里仅记一条 warning 告知 bootstrap 失败、启动即将中断。
            logger.warning(
                f"[db-bootstrap] 连接/建库未成功：{res.get('message')}；"
                f"详情：{res.get('detail')}"
            )
    except Exception as e:  # noqa: BLE001
        # bootstrap 本身出异常不应掩盖真正的 DDL 错误，只记日志。
        logger.warning(f"[db-bootstrap] 跳过自动建库：{type(e).__name__}: {e}")

    Base.metadata.create_all(bind=engine)


def reset_tables():
    Base.metadata.drop_all(bind=engine)
    create_tables()


def import_from_db(
    sqlite_path: str = None,
    # csv_path: str = None,
) -> bool:
    """
    在知识库与向量库无变化的情况下，从备份数据库中导入数据到 info.db。
    适用于版本升级时，info.db 结构变化，但无需重新向量化的情况。
    请确保两边数据库表名一致，需要导入的字段名一致
    当前仅支持 sqlite
    """
    import sqlite3 as sql
    from pprint import pprint

    models = list(Base.registry.mappers)

    try:
        con = sql.connect(sqlite_path)
        con.row_factory = sql.Row
        cur = con.cursor()
        tables = [
            x["name"]
            for x in cur.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        ]
        for model in models:
            table = model.local_table.fullname
            if table not in tables:
                continue
            print(f"processing table: {table}")
            with session_scope() as session:
                for row in cur.execute(f"select * from {table}").fetchall():
                    data = {k: row[k] for k in row.keys() if k in model.columns}
                    if "create_time" in data:
                        data["create_time"] = parse(data["create_time"])
                    pprint(data)
                    session.add(model.class_(**data))
        con.close()
        return True
    except Exception as e:
        print(f"无法读取备份数据库：{sqlite_path}。错误信息：{e}")
        return False


def file_to_kbfile(kb_name: str, files: List[str]) -> "List[KnowledgeFile]":
    # 41 题 P5:lazy import — 仅在真处理文件时触发 langchain 链
    from chayuan.server.knowledge_base.utils import KnowledgeFile

    kb_files = []
    for file in files:
        try:
            kb_file = KnowledgeFile(filename=file, knowledge_base_name=kb_name)
            kb_files.append(kb_file)
        except Exception as e:
            msg = f"{e}，已跳过"
            logger.error(f"{e.__class__.__name__}: {msg}")
    return kb_files


def folder2db(
    kb_names: List[str],
    mode: Literal["recreate_vs", "update_in_db", "increment"],
    vs_type: Literal["faiss", "milvus", "pg", "chromadb"] = Settings.kb_settings.DEFAULT_VS_TYPE,
    embed_model: Optional[str] = None,
    chunk_size: int = Settings.kb_settings.CHUNK_SIZE,
    chunk_overlap: int = Settings.kb_settings.OVERLAP_SIZE,
    zh_title_enhance: bool = Settings.kb_settings.ZH_TITLE_ENHANCE,
):
    """
    use existed files in local folder to populate database and/or vector store.
    set parameter `mode` to:
        recreate_vs: recreate all vector store and fill info to database using existed files in local folder
        fill_info_only(disabled): do not create vector store, fill info to db using existed files only
        update_in_db: update vector store and database info using local files that existed in database only
        increment: create vector store and database info for local files that not existed in database only
    """
    # 41 题 P5:把 ML 链 import 推到这里 — folder2db 真被调用时才触发 langchain
    from chayuan.server.knowledge_base.kb_service.base import (
        KBServiceFactory, SupportedVSType,
    )
    from chayuan.server.knowledge_base.utils import (
        KnowledgeFile, files2docs_in_thread, list_files_from_folder, list_kbs_from_folder,
    )
    if embed_model is None:
        from chayuan.server.utils import get_default_embedding
        embed_model = get_default_embedding()

    def files2vs(kb_name: str, kb_files: "List[KnowledgeFile]") -> List:
        result = []
        for success, res in files2docs_in_thread(
            kb_files,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            zh_title_enhance=zh_title_enhance,
        ):
            if success:
                _, filename, docs = res
                print(
                    f"正在将 {kb_name}/{filename} 添加到向量库，共包含{len(docs)}条文档"
                )
                kb_file = KnowledgeFile(filename=filename, knowledge_base_name=kb_name)
                kb_file.splited_docs = docs
                kb.add_doc(kb_file=kb_file, not_refresh_vs_cache=True)
                result.append({"kb_name": kb_name, "file": filename, "docs": docs})
            else:
                print(res)
        return result

    kb_names = kb_names or list_kbs_from_folder()
    for kb_name in kb_names:
        start = datetime.now()
        kb = KBServiceFactory.get_service(kb_name, vs_type, embed_model)
        if not kb.exists():
            kb.create_kb()

        # 清除向量库，从本地文件重建
        if mode == "recreate_vs":
            kb.clear_vs()
            kb.create_kb()
            kb_files = file_to_kbfile(kb_name, list_files_from_folder(kb_name))
            result = files2vs(kb_name, kb_files)
            kb.save_vector_store()
        # # 不做文件内容的向量化，仅将文件元信息存到数据库
        # # 由于现在数据库存了很多与文本切分相关的信息，单纯存储文件信息意义不大，该功能取消。
        # elif mode == "fill_info_only":
        #     files = list_files_from_folder(kb_name)
        #     kb_files = file_to_kbfile(kb_name, files)
        #     for kb_file in kb_files:
        #         add_file_to_db(kb_file)
        #         print(f"已将 {kb_name}/{kb_file.filename} 添加到数据库")
        # 以数据库中文件列表为基准，利用本地文件更新向量库
        elif mode == "update_in_db":
            files = kb.list_files()
            kb_files = file_to_kbfile(kb_name, files)
            result = files2vs(kb_name, kb_files)
            kb.save_vector_store()
        # 对比本地目录与数据库中的文件列表，进行增量向量化
        elif mode == "increment":
            db_files = kb.list_files()
            folder_files = list_files_from_folder(kb_name)
            files = list(set(folder_files) - set(db_files))
            kb_files = file_to_kbfile(kb_name, files)
            result = files2vs(kb_name, kb_files)
            kb.save_vector_store()
        else:
            print(f"unsupported migrate mode: {mode}")
        end = datetime.now()
        kb_path = (
            f"知识库路径\t：{kb.kb_path}\n"
            if kb.vs_type() == SupportedVSType.FAISS
            else ""
        )
        file_count = len(kb_files)
        success_count = len(result)
        docs_count = sum([len(x["docs"]) for x in result])
        print("\n" + "-" * 100)
        print(
            (
                f"知识库名称\t：{kb_name}\n"
                f"知识库类型\t：{kb.vs_type()}\n"
                f"向量模型：\t：{kb.embed_model}\n"
            )
            + kb_path
            + (
                f"文件总数量\t：{file_count}\n"
                f"入库文件数\t：{success_count}\n"
                f"知识条目数\t：{docs_count}\n"
                f"用时\t\t：{end-start}"
            )
        )
        print("-" * 100 + "\n")


def prune_db_docs(kb_names: List[str]):
    """
    delete docs in database that not existed in local folder.
    it is used to delete database docs after user deleted some doc files in file browser
    """
    # 41 题 P5:lazy import,避免顶层拖 langchain
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    from chayuan.server.knowledge_base.utils import list_files_from_folder

    for kb_name in kb_names:
        kb = KBServiceFactory.get_service_by_name(kb_name)
        if kb is not None:
            files_in_db = kb.list_files()
            files_in_folder = list_files_from_folder(kb_name)
            files = list(set(files_in_db) - set(files_in_folder))
            kb_files = file_to_kbfile(kb_name, files)
            for kb_file in kb_files:
                kb.delete_doc(kb_file, not_refresh_vs_cache=True)
                print(f"success to delete docs for file: {kb_name}/{kb_file.filename}")
            kb.save_vector_store()


def prune_folder_files(kb_names: List[str]):
    """
    delete doc files in local folder that not existed in database.
    it is used to free local disk space by delete unused doc files.
    """
    # 41 题 P5:lazy import
    from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    from chayuan.server.knowledge_base.utils import (
        get_file_path, list_files_from_folder,
    )

    for kb_name in kb_names:
        kb = KBServiceFactory.get_service_by_name(kb_name)
        if kb is not None:
            files_in_db = kb.list_files()
            files_in_folder = list_files_from_folder(kb_name)
            files = list(set(files_in_folder) - set(files_in_db))
            for file in files:
                os.remove(get_file_path(kb_name, file))
                print(f"success to delete file: {kb_name}/{file}")
