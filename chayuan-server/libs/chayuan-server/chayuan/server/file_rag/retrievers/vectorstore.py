from __future__ import annotations


from langchain_core.vectorstores import VectorStore
from langchain_core.retrievers import BaseRetriever

from chayuan.server.file_rag.retriever_compat import retriever_get_documents
from chayuan.server.file_rag.retrievers.base import BaseRetrieverService


class VectorstoreRetrieverService(BaseRetrieverService):
    def do_init(
        self,
        retriever: BaseRetriever = None,
        top_k: int = 5,
    ):
        self.vs = None
        self.top_k = top_k
        self.retriever = retriever

    @staticmethod
    def from_vectorstore(
        vectorstore: VectorStore,
        top_k: int,
        score_threshold: int | float,
    ):
        retriever = vectorstore.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"score_threshold": score_threshold, "k": top_k},
        )
        return VectorstoreRetrieverService(retriever=retriever, top_k=top_k)

    def get_relevant_documents(self, query: str):
        return retriever_get_documents(self.retriever, query)[: self.top_k]
