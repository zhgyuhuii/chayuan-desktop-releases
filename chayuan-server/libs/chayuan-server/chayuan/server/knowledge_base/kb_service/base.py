import operator
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from langchain_core.documents import Document

from chayuan.settings import Settings
from chayuan.utils import build_logger
from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseSchema
from chayuan.server.db.repository.knowledge_base_repository import (
    add_kb_to_db,
    delete_kb_from_db,
    get_kb_vector_namespace,
    get_kb_detail,
    kb_exists,
    list_kbs_from_db,
    load_kb_from_db,
)
from chayuan.server.db.repository.knowledge_file_repository import (
    add_file_to_db,
    count_files_from_db,
    delete_file_from_db,
    delete_files_from_db,
    file_exists_in_db,
    get_file_detail,
    list_docs_from_db,
    list_files_from_db,
)
from chayuan.server.knowledge_base.model.kb_document_model import DocumentWithVSId
from chayuan.server.knowledge_base.utils import (
    KnowledgeFile,
    get_doc_path,
    get_kb_path,
    list_files_from_folder,
    list_kbs_from_folder,
    new_vector_namespace,
    safe_vs_collection_name,
)
from chayuan.server.utils import (
    check_embed_model as _check_embed_model,
    get_default_embedding,
)


logger = build_logger()


def _invalidate_hybrid_caches(kb_name: str) -> None:
    """文件变动时清 hybrid 缓存；hybrid_service 未加载时静默跳过。"""
    try:
        from chayuan.server.file_rag.hybrid_service import invalidate_bm25_cache
        invalidate_bm25_cache(kb_name)
    except Exception:  # noqa: BLE001
        pass
    try:
        from chayuan.server.knowledge_source.cache import (
            invalidate_result_cache_by_kb,
        )
        invalidate_result_cache_by_kb(kb_name)
    except Exception:  # noqa: BLE001
        pass


class SupportedVSType:
    FAISS = "faiss"
    MILVUS = "milvus"
    DEFAULT = "default"
    ZILLIZ = "zilliz"
    PG = "pg"
    RELYT = "relyt"
    ES = "es"
    CHROMADB = "chromadb"
    # Phase 4 新增:嵌入式 sqlite-vec(单机版默认;详见 retrieval/vector_store_local.py)。
    # 完整 KBService 适配器 (do_search/do_add_doc/...) 在 Phase 5.x 接入,
    # 当前 ``KBServiceFactory.get_service`` 命中此值会抛 NotImplementedError。
    SQLITE_VEC = "sqlite-vec"


class KBService(ABC):
    def __init__(
        self,
        knowledge_base_name: str,
        kb_info: str = None,
        embed_model: str = get_default_embedding(),
    ):
        self.kb_name = knowledge_base_name
        self.kb_info = kb_info or Settings.kb_settings.KB_INFO.get(
            knowledge_base_name, f"关于{knowledge_base_name}的知识库"
        )
        self.embed_model = embed_model
        # 用户可见 kb_name 与底层向量集合/索引名解耦。新库先生成随机 ASCII namespace;
        # 存量库没有该字段时继续走旧的确定性安全映射,避免找不到已有 collection。
        stored_namespace = get_kb_vector_namespace(knowledge_base_name)
        if stored_namespace:
            self.vector_namespace = stored_namespace
        elif kb_exists(knowledge_base_name):
            self.vector_namespace = safe_vs_collection_name(knowledge_base_name)
        else:
            self.vector_namespace = new_vector_namespace()
        self.kb_path = get_kb_path(self.kb_name)
        self.doc_path = get_doc_path(self.kb_name)
        self.do_init()

    def __repr__(self) -> str:
        return f"{self.kb_name} @ {self.embed_model}"

    def save_vector_store(self):
        """
        保存向量库:FAISS保存到磁盘，milvus保存到数据库。PGVector暂未支持
        """
        pass

    def check_embed_model(self) -> Tuple[bool, str]:
        return _check_embed_model(self.embed_model)

    def create_kb(self):
        """
        创建知识库
        """
        if not os.path.exists(self.doc_path):
            os.makedirs(self.doc_path)
        status = add_kb_to_db(
            self.kb_name, self.kb_info, self.vs_type(), self.embed_model,
            vector_namespace=self.vector_namespace,
        )

        if status:
            self.do_create_kb()
        return status

    def clear_vs(self):
        """
        删除向量库中所有内容。

        容错策略:向量库后端不可达(Milvus 没起 / PG 连不上 等)不应阻塞用户清理
        知识库 — 只是失去"清向量"这个能力。把 do_clear_vs 包成 best-effort,
        失败 logger.warning 后继续清 DB 行(file metadata)。
        """
        try:
            self.do_clear_vs()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "clear_vs: 向量库清理失败(后端不可达?),继续清 DB file metadata。kb=%s err=%r",
                self.kb_name, e,
            )
        status = delete_files_from_db(self.kb_name)
        return status

    def drop_kb(self):
        """
        删除知识库。

        容错策略(2026-04 加固):向量库不可达时仍允许删除 — 只是 collection 残留,
        不阻塞用户。do_drop_kb 失败 → warning 继续清磁盘 + FileStorage + DB 行。
        这样用户不会因为 Milvus 临时挂掉就再也删不掉历史 KB。
        """
        try:
            self.do_drop_kb()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "drop_kb: 向量库 collection 清理失败(后端不可达?),继续清本地文件 + DB 行。"
                "如果向量库恢复后想清干净,手动 drop 同名 collection。kb=%s err=%r",
                self.kb_name, e,
            )
        # 子类 do_drop_kb 不一定清磁盘(milvus/chroma/zilliz/relyt 都只清向量),
        # 这里统一兜底删整个 kb 目录。**绝不能用 ignore_errors=True** —— 它把
        # 删除失败静默吞掉,残留的 content/ 文件会在同名重建时被
        # get_kb_file_details → list_files_from_folder 重新读出来(= "删了知识库、
        # 重新建同名的、旧文件又回来了" 的根因)。用 force_rmtree:处理只读 +
        # 重试,删不净时如实 logger.error,不装成功。
        if os.path.exists(self.kb_path) and not force_rmtree(self.kb_path):
            logger.error(
                "drop_kb: 知识库目录未删净 kb=%s path=%s —— 残留文件会在同名"
                "重建时复活;多半是个别文件被占用,关掉占用进程后重删。",
                self.kb_name, self.kb_path,
            )
        # FileStorage(KB_CONTENT 命名空间)与本地 kb_path 是两份;local 后端落在
        # KB_ROOT_PATH/../storage 下,minio 在远端 bucket。一并清掉,避免残留。
        try:
            from chayuan.server.file_storage import NS, get_storage
            storage = get_storage()
            prefix = f"{self.kb_name}/"
            for obj in storage.list(NS.KB_CONTENT, prefix=prefix, limit=10000):
                storage.delete(NS.KB_CONTENT, obj.key)
        except Exception as e:  # noqa: BLE001
            logger.warning("FileStorage cleanup for kb=%s failed: %r", self.kb_name, e)
        status = delete_kb_from_db(self.kb_name)
        return status

    def add_doc(self, kb_file: KnowledgeFile, docs: List[Document] = [], **kwargs):
        """
        向知识库添加文件
        如果指定了docs，则不再将文本向量化，并将数据库对应条目标为custom_docs=True
        """
        if not self.check_embed_model()[0]:
            return False

        if docs:
            custom_docs = True
        else:
            docs = kb_file.file2text()
            custom_docs = False

        if docs:
            # 将 metadata["source"] 改为相对路径
            for doc in docs:
                try:
                    doc.metadata.setdefault("source", kb_file.filename)
                    source = doc.metadata.get("source", "")
                    if os.path.isabs(source):
                        rel_path = Path(source).relative_to(self.doc_path)
                        doc.metadata["source"] = str(rel_path.as_posix().strip("/"))
                except Exception as e:
                    print(
                        f"cannot convert absolute path ({source}) to relative path. error is : {e}"
                    )
            self.delete_doc(kb_file)
            doc_infos = self.do_add_doc(docs, **kwargs)
            status = add_file_to_db(
                kb_file,
                custom_docs=custom_docs,
                docs_count=len(docs),
                doc_infos=doc_infos,
                file_hash=kwargs.get("file_hash"),
                uploader_id=kwargs.get("uploader_id"),
            )
            # 文档集变化 → 让 BM25 / hybrid 相关缓存失效
            _invalidate_hybrid_caches(self.kb_name)
        else:
            status = False
        return status

    def delete_doc(
        self, kb_file: KnowledgeFile, delete_content: bool = False, **kwargs
    ):
        """
        从知识库删除文件
        """
        self.do_delete_doc(kb_file, **kwargs)
        status = delete_file_from_db(kb_file)
        if delete_content and os.path.exists(kb_file.filepath):
            os.remove(kb_file.filepath)
        _invalidate_hybrid_caches(self.kb_name)
        return status

    def update_info(self, kb_info: str):
        """
        更新知识库介绍
        """
        self.kb_info = kb_info
        status = add_kb_to_db(
            self.kb_name, self.kb_info, self.vs_type(), self.embed_model
        )
        return status

    def update_doc(self, kb_file: KnowledgeFile, docs: List[Document] = [], **kwargs):
        """
        使用content中的文件更新向量库
        如果指定了docs，则使用自定义docs，并将数据库对应条目标为custom_docs=True
        """
        if not self.check_embed_model()[0]:
            return False

        if os.path.exists(kb_file.filepath):
            self.delete_doc(kb_file, **kwargs)
            return self.add_doc(kb_file, docs=docs, **kwargs)

    def exist_doc(self, file_name: str):
        return file_exists_in_db(
            KnowledgeFile(knowledge_base_name=self.kb_name, filename=file_name)
        )

    def list_files(self):
        return list_files_from_db(self.kb_name)

    def count_files(self):
        return count_files_from_db(self.kb_name)

    def search_docs(
        self,
        query: str,
        top_k: int = Settings.kb_settings.VECTOR_SEARCH_TOP_K,
        score_threshold: float = Settings.kb_settings.SCORE_THRESHOLD,
        use_hybrid: Optional[bool] = None,
        use_rerank: Optional[bool] = None,
        use_expand: Optional[bool] = None,
    ) -> List[Document]:
        """检索入口。

        三个 use_* 开关:
          - None  → 使用 Settings.kb_settings 全局默认(USE_HYBRID_RETRIEVER 等)
          - True/False → 显式覆盖,前端可按请求开关 rerank
        全部 False/None 且 settings 也都关时,行为退化到旧 do_search。

        失败原因可观测:
        - 任何返回 [] 的情况都会把"为什么 0 命中"的具体原因写到
          ``self._last_search_reason``,调用方(_process_one_ku 等)读出来回传前端,
          避免"搜不到也不知道为什么"的黑盒。
        """
        # 先清残留;一次新调用一份新 reason
        self._last_search_reason: Optional[str] = None

        ok, msg = self.check_embed_model()
        if not ok:
            self._last_search_reason = (
                f"嵌入模型不可达:{msg or '<未知>'}。"
                f"KB 当前 embed_model={getattr(self, 'embed_model', '?')!r}。"
                f"修法:① 在 /v1/models 确认该模型已注册;② 平台 api_key 是否有效;"
                f"③ 百炼 text-embedding-v3 必须走原生端点(已自动路由,见 dashscope_embeddings)。"
            )
            logger.warning("search_docs aborted - %s", self._last_search_reason)
            return []

        if use_hybrid is None:
            use_hybrid = bool(getattr(Settings.kb_settings, "USE_HYBRID_RETRIEVER", False))
        if use_rerank is None:
            use_rerank = bool(getattr(Settings.kb_settings, "USE_RERANKER", False))
        if use_expand is None:
            use_expand = bool(getattr(Settings.kb_settings, "USE_CONTEXT_EXPANSION", False))
        if use_hybrid or use_rerank or use_expand:
            try:
                from chayuan.server.file_rag.hybrid_service import hybrid_search_docs
                docs = hybrid_search_docs(
                    kb_service=self, query=query,
                    top_k=top_k, score_threshold=score_threshold,
                    use_hybrid=use_hybrid, use_rerank=use_rerank, use_expand=use_expand,
                )
                if not docs:
                    self._last_search_reason = (
                        "向量库返回 0 条(hybrid 路径)。可能原因:① collection 还没有数据;"
                        "② score_threshold 过严;③ embed 维度与 KB 索引维度不匹配。"
                    )
                return docs
            except Exception as e:  # noqa: BLE001
                logger.warning("hybrid_search_docs 失败,回退纯向量:%r", e)
                self._last_search_reason = f"hybrid_search 异常,已回退纯向量:{type(e).__name__}: {e}"
        try:
            docs = self.do_search(query, top_k, score_threshold)
        except Exception as e:  # noqa: BLE001
            self._last_search_reason = (
                f"do_search 异常({self.vs_type()}):{type(e).__name__}: {e}"
            )
            logger.exception("do_search failed for kb=%s", self.kb_name)
            return []
        if not docs:
            self._last_search_reason = (
                "向量库返回 0 条(纯向量路径)。可能原因:① collection 空 / KB 未入库;"
                "② SCORE_THRESHOLD 过严(当前 "
                f"{score_threshold});③ embed 维度与 KB 索引维度不一致。"
            )
        return docs

    def get_doc_by_ids(self, ids: List[str]) -> List[Document]:
        return []

    def del_doc_by_ids(self, ids: List[str]) -> bool:
        raise NotImplementedError

    def update_doc_by_ids(self, docs: Dict[str, Document]) -> bool:
        """
        传入参数为： {doc_id: Document, ...}
        如果对应 doc_id 的值为 None，或其 page_content 为空，则删除该文档
        """
        if not self.check_embed_model()[0]:
            return False

        self.del_doc_by_ids(list(docs.keys()))
        pending_docs = []
        ids = []
        for _id, doc in docs.items():
            if not doc or not doc.page_content.strip():
                continue
            ids.append(_id)
            pending_docs.append(doc)
        self.do_add_doc(docs=pending_docs, ids=ids)
        return True

    def list_docs(
        self, file_name: str = None, metadata: Dict = {}
    ) -> List[DocumentWithVSId]:
        """
        通过file_name或metadata检索Document
        """
        doc_infos = list_docs_from_db(
            kb_name=self.kb_name, file_name=file_name, metadata=metadata
        )
        docs = []
        for x in doc_infos:
            doc_info = self.get_doc_by_ids([x["id"]])[0]
            if doc_info is not None:
                # 处理非空的情况
                doc_with_id = DocumentWithVSId(**{**doc_info.dict(), "id":x["id"]})
                docs.append(doc_with_id)
            else:
                # 处理空的情况
                # 可以选择跳过当前循环迭代或执行其他操作
                pass
        return docs

    def get_relative_source_path(self, filepath: str):
        """
        将文件路径转化为相对路径，保证查询时一致
        """
        relative_path = filepath
        if os.path.isabs(relative_path):
            try:
                relative_path = Path(filepath).relative_to(self.doc_path)
            except Exception as e:
                print(
                    f"cannot convert absolute path ({relative_path}) to relative path. error is : {e}"
                )

        relative_path = str(relative_path.as_posix().strip("/"))
        return relative_path

    @abstractmethod
    def do_create_kb(self):
        """
        创建知识库子类实自己逻辑
        """
        pass

    @staticmethod
    def list_kbs_type():
        return list(Settings.kb_settings.kbs_config.keys())

    @classmethod
    def list_kbs(cls):
        return list_kbs_from_db()

    def exists(self, kb_name: str = None):
        kb_name = kb_name or self.kb_name
        return kb_exists(kb_name)

    @abstractmethod
    def vs_type(self) -> str:
        pass

    @abstractmethod
    def do_init(self):
        pass

    @abstractmethod
    def do_drop_kb(self):
        """
        删除知识库子类实自己逻辑
        """
        pass

    @abstractmethod
    def do_search(
        self,
        query: str,
        top_k: int,
        score_threshold: float,
    ) -> List[Tuple[Document, float]]:
        """
        搜索知识库子类实自己逻辑
        """
        pass

    @abstractmethod
    def do_add_doc(
        self,
        docs: List[Document],
        **kwargs,
    ) -> List[Dict]:
        """
        向知识库添加文档子类实自己逻辑
        """
        pass

    @abstractmethod
    def do_delete_doc(self, kb_file: KnowledgeFile):
        """
        从知识库删除文档子类实自己逻辑
        """
        pass

    @abstractmethod
    def do_clear_vs(self):
        """
        从知识库删除全部向量子类实自己逻辑
        """
        pass


class KBServiceFactory:
    @staticmethod
    def get_service(
        kb_name: str,
        vector_store_type: Union[str, SupportedVSType],
        embed_model: str = get_default_embedding(),
        kb_info: str = None,
    ) -> KBService:
        if isinstance(vector_store_type, str):
            # 归一化:"sqlite-vec" → SUPPORTEDVSTYPE.SQLITE_VEC(``-`` 在 Python attr 中非法)
            attr_name = vector_store_type.upper().replace("-", "_")
            vector_store_type = getattr(SupportedVSType, attr_name)
        params = {
            "knowledge_base_name": kb_name,
            "embed_model": embed_model,
            "kb_info": kb_info,
        }
        if SupportedVSType.FAISS == vector_store_type:
            from chayuan.server.knowledge_base.kb_service.faiss_kb_service import (
                FaissKBService,
            )

            return FaissKBService(**params)
        elif SupportedVSType.PG == vector_store_type:
            from chayuan.server.knowledge_base.kb_service.pg_kb_service import (
                PGKBService,
            )

            return PGKBService(**params)
        elif SupportedVSType.RELYT == vector_store_type:
            from chayuan.server.knowledge_base.kb_service.relyt_kb_service import (
                RelytKBService,
            )

            return RelytKBService(**params)
        elif SupportedVSType.MILVUS == vector_store_type:
            from chayuan.server.knowledge_base.kb_service.milvus_kb_service import (
                MilvusKBService,
            )

            return MilvusKBService(**params)
        elif SupportedVSType.ZILLIZ == vector_store_type:
            from chayuan.server.knowledge_base.kb_service.zilliz_kb_service import (
                ZillizKBService,
            )

            return ZillizKBService(**params)
        elif vector_store_type == SupportedVSType.DEFAULT:
            # 与 kb_settings.DEFAULT_VS_TYPE 对齐；历史上误写为固定 Milvus，导致未启动 Milvus 时建库失败
            resolved = (Settings.kb_settings.DEFAULT_VS_TYPE or SupportedVSType.FAISS).lower()
            if resolved == SupportedVSType.DEFAULT:
                resolved = SupportedVSType.FAISS
            return KBServiceFactory.get_service(
                kb_name, resolved, embed_model, kb_info=kb_info
            )
        elif SupportedVSType.ES == vector_store_type:
            from chayuan.server.knowledge_base.kb_service.es_kb_service import (
                ESKBService,
            )

            return ESKBService(**params)
        elif SupportedVSType.CHROMADB == vector_store_type:
            from chayuan.server.knowledge_base.kb_service.chromadb_kb_service import (
                ChromaKBService,
            )

            return ChromaKBService(**params)
        elif SupportedVSType.SQLITE_VEC == vector_store_type:
            # Phase 5.x:单机版默认。基于 ``retrieval/vector_store_local.SqliteVecStore``
            # 的 KBService 适配,共享单 sqlite 文件、每 KB 一个 vec0 collection。
            from chayuan.server.knowledge_base.kb_service.sqlite_vec_kb_service import (
                SqliteVecKBService,
            )

            return SqliteVecKBService(**params)

    @staticmethod
    def get_service_by_name(kb_name: str) -> KBService:
        _, vs_type, embed_model = load_kb_from_db(kb_name)
        if _ is None:  # kb not in db, just return None
            return None
        return KBServiceFactory.get_service(kb_name, vs_type, embed_model)

    @staticmethod
    def get_default():
        return KBServiceFactory.get_service("default", SupportedVSType.DEFAULT)


def get_kb_details() -> List[Dict]:
    kbs_in_folder = list_kbs_from_folder()
    kbs_in_db: List[KnowledgeBaseSchema] = KBService.list_kbs()
    result = {}

    for kb in kbs_in_folder:
        result[kb] = {
            "kb_name": kb,
            "vs_type": "",
            "kb_info": "",
            "embed_model": "",
            "file_count": 0,
            "create_time": None,
            "in_folder": True,
            "in_db": False,
        }

    for kb_detail in kbs_in_db:
        kb_detail = kb_detail.model_dump()
        kb_name = kb_detail["kb_name"]
        kb_detail["in_db"] = True
        if kb_name in result:
            result[kb_name].update(kb_detail)
        else:
            kb_detail["in_folder"] = False
            result[kb_name] = kb_detail

    data = []
    for i, v in enumerate(result.values()):
        v["No"] = i + 1
        data.append(v)

    return data


def force_rmtree(path) -> bool:
    """尽力删掉整个目录树,返回最终是否删干净(目录已不存在)。

    比 ``shutil.rmtree(ignore_errors=True)`` 强、且**不静默**:
      - Windows 只读文件:onerror 里 chmod 成可写再删;
      - 个别文件被瞬时占用:重试几次。
    仍然删不掉返回 False —— 调用方据此 error 告警 / 兜底,不再把失败当成功。
    ``ignore_errors=True`` 正是"删知识库后同名重建旧文件复活"的根源:它把删除
    失败静默吞掉,残留 content/ 会被 get_kb_file_details→list_files_from_folder
    重新读出来。
    """
    import stat
    import time as _time

    path = str(path)

    def _onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:  # noqa: BLE001
            pass

    for _ in range(3):
        if not os.path.exists(path):
            return True
        try:
            shutil.rmtree(path, onerror=_onerror)
        except Exception:  # noqa: BLE001
            pass
        if not os.path.exists(path):
            return True
        _time.sleep(0.3)

    # rmtree 删不掉(Windows 上目录里有被占用的文件)→ 把整个目录**改名挪走**。
    # 目录改名一般能成功(里面有打开的文件也不挡父目录 rename),原路径就此
    # 空出 —— "删知识库 → 同名重建" 时 get_kb_path(name) 是干净的,不会再撞见
    # 旧 content/、旧文件不会复活。挪走的 .trash 残骸 best-effort 再删一次,
    # 删不掉就留着(对 list_files_from_folder / list_kbs 都不可见),无害。
    if os.path.isdir(path):
        try:
            trash = f"{path}.trash-{int(_time.time())}"
            os.rename(path, trash)
            logger.warning("force_rmtree 删不掉,已改名挪走: %s → %s", path, trash)
            try:
                shutil.rmtree(trash, onerror=_onerror)
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            logger.error("force_rmtree 连改名挪走都失败 %s: %r", path, e)

    return not os.path.exists(path)


def get_kb_file_details(kb_name: str) -> List[Dict]:
    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        return []

    files_in_folder = list_files_from_folder(kb_name)
    files_in_db = kb.list_files()
    result = {}

    for doc in files_in_folder:
        result[doc] = {
            "kb_name": kb_name,
            "file_name": doc,
            "file_ext": os.path.splitext(doc)[-1],
            "file_version": 0,
            "document_loader": "",
            "docs_count": 0,
            "text_splitter": "",
            "create_time": None,
            "in_folder": True,
            "in_db": False,
        }
    lower_names = {x.lower(): x for x in result}
    for doc in files_in_db:
        doc_detail = get_file_detail(kb_name, doc)
        if doc_detail:
            doc_detail["in_db"] = True
            if doc.lower() in lower_names:
                result[lower_names[doc.lower()]].update(doc_detail)
            else:
                doc_detail["in_folder"] = False
                result[doc] = doc_detail

    data = []
    for i, v in enumerate(result.values()):
        v["No"] = i + 1
        data.append(v)

    return data


def score_threshold_process(score_threshold, k, docs):
    if score_threshold is not None:
        cmp = operator.le
        docs = [
            (doc, similarity)
            for doc, similarity in docs
            if cmp(similarity, score_threshold)
        ]
    return docs[:k]
