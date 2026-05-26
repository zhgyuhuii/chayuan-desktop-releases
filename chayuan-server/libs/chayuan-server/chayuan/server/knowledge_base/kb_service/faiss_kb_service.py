import os
import shutil
from typing import Dict, List, Tuple

from langchain_core.documents import Document

from chayuan.settings import Settings
from chayuan.server.file_rag.utils import get_Retriever
from chayuan.server.knowledge_base.kb_cache.faiss_cache import (
    ThreadSafeFaiss,
    kb_faiss_pool,
)
from chayuan.server.knowledge_base.kb_service.base import KBService, SupportedVSType
from chayuan.server.knowledge_base.utils import KnowledgeFile, get_kb_path, get_vs_path


class FaissKBService(KBService):
    vs_path: str
    kb_path: str
    vector_name: str = None

    def vs_type(self) -> str:
        return SupportedVSType.FAISS

    def get_vs_path(self):
        return get_vs_path(self.kb_name, self.vector_name)

    def get_kb_path(self):
        return get_kb_path(self.kb_name)

    def load_vector_store(self) -> ThreadSafeFaiss:
        return kb_faiss_pool.load_vector_store(
            kb_name=self.kb_name,
            vector_name=self.vector_name,
            embed_model=self.embed_model,
        )

    def save_vector_store(self):
        self.load_vector_store().save(self.vs_path)

    def get_doc_by_ids(self, ids: List[str]) -> List[Document]:
        with self.load_vector_store().acquire() as vs:
            return [vs.docstore._dict.get(id) for id in ids]

    def del_doc_by_ids(self, ids: List[str]) -> bool:
        with self.load_vector_store().acquire() as vs:
            vs.delete(ids)

    def do_init(self):
        self.vector_name = self.vector_name or self.embed_model.replace(":", "_")
        self.kb_path = self.get_kb_path()
        self.vs_path = self.get_vs_path()

    def do_create_kb(self):
        if not os.path.exists(self.vs_path):
            os.makedirs(self.vs_path)
        self.load_vector_store()

    def do_drop_kb(self):
        self.clear_vs()
        try:
            shutil.rmtree(self.kb_path)
        except Exception:
            pass

    def do_search(
        self,
        query: str,
        top_k: int,
        score_threshold: float = Settings.kb_settings.SCORE_THRESHOLD,
    ) -> List[Tuple[Document, float]]:
        with self.load_vector_store().acquire() as vs:
            retriever = get_Retriever("ensemble").from_vectorstore(
                vs,
                top_k=top_k,
                score_threshold=score_threshold,
            )
            docs = retriever.get_relevant_documents(query)
        return docs

    def do_add_doc(
        self,
        docs: List[Document],
        **kwargs,
    ) -> List[Dict]:
        texts = [x.page_content for x in docs]
        metadatas = [x.metadata for x in docs]
        with self.load_vector_store().acquire() as vs:
            embeddings = vs.embeddings.embed_documents(texts)
            # 防御 + 诊断:embed_documents silent-fail 是反复出现的真凶,
            # 上一版只查 len 不查内容 —— 如果返 [None] 或 [[]] 等长但
            # 是空 / 假向量,guard 漏过去 -> FAISS 静默写 0 真向量。
            # 这版把:
            #   1) Embeddings class 名
            #   2) 文本数 / 返回向量数
            #   3) 第一个向量的 dim + 前 3 个 float
            #   4) 是否全 0(no-op embed 常见特征)
            # 全部 logger.info,且任何形状异常立即 raise。装机版排查时
            # 这条日志直接告诉运维 routing 到了哪个 Embeddings + 它真返
            # 了啥;不再黑盒。
            import logging as _logging
            _embed_log = _logging.getLogger("chayuan.kb_service.faiss.do_add_doc")
            n_txt = len(texts)
            n_emb = len(embeddings) if embeddings is not None else 0
            emb_class = type(vs.embeddings).__name__
            first_dim = "?"
            first_few = "?"
            all_zero = "?"
            if n_emb > 0 and embeddings[0] is not None:
                try:
                    first_dim = len(embeddings[0])
                    first_few = (embeddings[0][:3] if first_dim >= 3 else embeddings[0])
                    all_zero = all(v == 0 for v in embeddings[0]) if first_dim else "n/a"
                except Exception:  # noqa: BLE001
                    first_dim = "ERR"
            _embed_log.info(
                "[embed-probe] class=%s model=%r n_txt=%d n_emb=%d "
                "first_dim=%s first_3=%s all_zero=%s",
                emb_class, self.embed_model, n_txt, n_emb,
                first_dim, first_few, all_zero,
            )
            # 关键断言(长度 / 第一个向量 None / 全 0)
            if n_emb != n_txt:
                raise RuntimeError(
                    f"embed_documents returned {n_emb} vectors for {n_txt} texts "
                    f"(Embeddings class={emb_class}, model={self.embed_model!r}). "
                    f"Silent embedding failure -- likely causes: "
                    f"(a) ONNX local short-circuit hit but model.onnx missing; "
                    f"(b) langchain Embeddings instance swallowed transport error; "
                    f"(c) MODEL_PLATFORMS routing returned a no-op Embeddings."
                )
            if n_emb > 0 and embeddings[0] is None:
                raise RuntimeError(
                    f"embed_documents returned None for first text "
                    f"(Embeddings class={emb_class}, model={self.embed_model!r})"
                )
            if n_emb > 0 and first_dim == 0:
                raise RuntimeError(
                    f"embed_documents returned empty vector (dim=0) for first text "
                    f"(Embeddings class={emb_class}, model={self.embed_model!r})"
                )
            if n_emb > 0 and all_zero is True:
                raise RuntimeError(
                    f"embed_documents returned ALL-ZERO vector "
                    f"(Embeddings class={emb_class}, model={self.embed_model!r}). "
                    f"Embedding model produced no signal -- check model name "
                    f"forwarding to sidecar."
                )
            # 加 ntotal 前后对比 + add_embeddings 返回 IDs,定位"embed 真返 vec
            # 但 FAISS 没收"那一类 silent fail。
            ntotal_before = getattr(getattr(vs, "index", None), "ntotal", "?")
            docstore_before = len(getattr(getattr(vs, "docstore", None), "_dict", {}) or {})
            # ⚠ vs.add_embeddings 入参是 zip 迭代器 —— 注意它内部可能 zip(*)
            # 重新解包,迭代器一次性消耗光,理论是 OK,但记录 zip 物化成 list 防意外。
            text_emb_pairs = list(zip(texts, embeddings))
            ids = vs.add_embeddings(
                text_embeddings=text_emb_pairs, metadatas=metadatas
            )
            ntotal_after = getattr(getattr(vs, "index", None), "ntotal", "?")
            docstore_after = len(getattr(getattr(vs, "docstore", None), "_dict", {}) or {})
            _embed_log.info(
                "[embed-probe] add_embeddings done: ids=%d (%s) "
                "index.ntotal %s -> %s  docstore %d -> %d",
                len(ids) if ids else 0,
                (ids[:3] if ids else "[]"),
                ntotal_before, ntotal_after,
                docstore_before, docstore_after,
            )
            if (isinstance(ntotal_after, int) and isinstance(ntotal_before, int)
                    and ntotal_after == ntotal_before):
                raise RuntimeError(
                    f"FAISS.add_embeddings ran but index.ntotal did not change "
                    f"({ntotal_before} -> {ntotal_after}). Vectors silently "
                    f"dropped. Embeddings class={emb_class}, model={self.embed_model!r}."
                )
            if not kwargs.get("not_refresh_vs_cache"):
                vs.save_local(self.vs_path)
                _embed_log.info(
                    "[embed-probe] do_add_doc save_local done at vs_path=%s "
                    "(ntotal=%s docstore=%d)",
                    self.vs_path, ntotal_after, docstore_after,
                )
        doc_infos = [{"id": id, "metadata": doc.metadata} for id, doc in zip(ids, docs)]
        return doc_infos

    def do_delete_doc(self, kb_file: KnowledgeFile, **kwargs):
        with self.load_vector_store().acquire() as vs:
            ids = [
                k
                for k, v in vs.docstore._dict.items()
                if v.metadata.get("source").lower() == kb_file.filename.lower()
            ]
            if len(ids) > 0:
                vs.delete(ids)
            if not kwargs.get("not_refresh_vs_cache"):
                vs.save_local(self.vs_path)
        return ids

    def do_clear_vs(self):
        with kb_faiss_pool.atomic:
            kb_faiss_pool.pop((self.kb_name, self.vector_name))
        try:
            shutil.rmtree(self.vs_path)
        except Exception:
            ...
        os.makedirs(self.vs_path, exist_ok=True)

    def exist_doc(self, file_name: str):
        if super().exist_doc(file_name):
            return "in_db"

        content_path = os.path.join(self.kb_path, "content")
        if os.path.isfile(os.path.join(content_path, file_name)):
            return "in_folder"
        else:
            return False


if __name__ == "__main__":
    faissService = FaissKBService("test")
    faissService.add_doc(KnowledgeFile("README.md", "test"))
    faissService.delete_doc(KnowledgeFile("README.md", "test"))
    faissService.do_drop_kb()
    print(faissService.search_docs("如何启动api服务"))
