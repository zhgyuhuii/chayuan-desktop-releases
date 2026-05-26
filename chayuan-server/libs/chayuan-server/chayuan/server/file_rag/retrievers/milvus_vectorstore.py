import warnings

from langchain_core.vectorstores import VectorStore
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStoreRetriever

from chayuan.server.file_rag.retriever_compat import retriever_get_documents
from chayuan.server.file_rag.retrievers.base import BaseRetrieverService

from langchain_core.documents import Document
from langchain_core.callbacks.manager import (
        AsyncCallbackManagerForRetrieverRun,
        CallbackManagerForRetrieverRun
)

from typing import List, Tuple


def _normalize_relevance(score: float) -> float:
    """Milvus 默认走 L2 距离/IP 相似度,Langchain 的 similarity_score_threshold
    路径期望分数 ∈ [0, 1] 的"相关度"(越大越相关)。

    - score ∈ [0, 1]:已是相关度,原样返回
    - score < 0:负 IP 相似度,夹到 0
    - score > 1:按 L2 距离处理,用 ``1 / (1 + d)`` 反演为相关度,
      d=0 → 1.0,d=1 → 0.5,d→∞ → 0,与 langchain 文档约定一致
    """
    s = float(score)
    if 0.0 <= s <= 1.0:
        return s
    if s < 0.0:
        return 0.0
    return 1.0 / (1.0 + s)


def _filter_and_warn(
    docs_and_scores: List[Tuple[Document, float]],
    score_threshold,
) -> List[Document]:
    """归一化 + 阈值过滤,返回 ``List[Document]`` 与 LangChain VectorStoreRetriever
    契约一致;归一化后的相关度写到 ``doc.metadata['score']``,后续节点(如
    nodes._doc_to_chunk)会从 metadata.score 读出来作为引用排序。

    threshold 双语义自动识别(避免硬切让历史配置全 0 命中):
    - ``threshold > 1.0``:旧语义,raw 是 L2 距离,**小**越相关 → 用 ``raw <= threshold`` 过滤;
       SCORE_THRESHOLD=2.0 在该模式下相当于不筛选(L2 距离很少 > 2)
    - ``threshold ∈ [0, 1]``:新语义,归一化相关度 = ``1 / (1 + raw)`` ∈ (0, 1],**大**越相关
       → 用 ``relevance >= threshold`` 过滤;0.3 ~ 0.5 比较常用
    """
    def _annotate(doc: Document, raw: float) -> Document:
        # 用同一份 Document(原 langchain Milvus 实例化的);只补 metadata.score
        # metadata 可能是 None,统一兜成 dict
        meta = dict(doc.metadata or {})
        meta["score"] = _normalize_relevance(raw)
        meta.setdefault("raw_score", float(raw))
        doc.metadata = meta
        return doc

    if score_threshold is None:
        return [_annotate(d, s) for d, s in docs_and_scores]

    th = float(score_threshold)
    if th > 1.0:
        # 旧 L2 语义:按 raw 比较
        kept = [_annotate(d, s) for d, s in docs_and_scores if s <= th]
        mode_desc = f"L2 raw <= {th}"
    else:
        kept = [_annotate(d, s) for d, s in docs_and_scores
                if _normalize_relevance(s) >= th]
        mode_desc = f"relevance >= {th}"

    if not kept:
        warnings.warn(
            f"No relevant docs were retrieved (mode={mode_desc}, "
            f"原始分数:{[round(s, 3) for _, s in docs_and_scores]})"
        )
    return kept


class MilvusRetriever(VectorStoreRetriever):
    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        if self.search_type == "similarity":
            return self.vectorstore.similarity_search(query, **self.search_kwargs)
        elif self.search_type == "similarity_score_threshold":
            raw = self.vectorstore.similarity_search_with_score(query, **self.search_kwargs)
            return _filter_and_warn(raw, self.search_kwargs.get("score_threshold"))
        elif self.search_type == "mmr":
            return self.vectorstore.max_marginal_relevance_search(
                query, **self.search_kwargs
            )
        else:
            raise ValueError(f"search_type of {self.search_type} not allowed.")

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> List[Document]:
        if self.search_type == "similarity":
            return await self.vectorstore.asimilarity_search(
                query, **self.search_kwargs
            )
        elif self.search_type == "similarity_score_threshold":
            raw = await self.vectorstore.asimilarity_search_with_score(
                query, **self.search_kwargs
            )
            return _filter_and_warn(raw, self.search_kwargs.get("score_threshold"))
        elif self.search_type == "mmr":
            return await self.vectorstore.amax_marginal_relevance_search(
                query, **self.search_kwargs
            )
        else:
            raise ValueError(f"search_type of {self.search_type} not allowed.")


class MilvusVectorstoreRetrieverService(BaseRetrieverService):
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
        score_threshold: int or float,
    ):
        retriever = MilvusRetriever(vectorstore=vectorstore,
                                    search_type="similarity_score_threshold",
                                    search_kwargs={"score_threshold": score_threshold, "k": top_k}
                                    )

        return MilvusVectorstoreRetrieverService(retriever=retriever, top_k=top_k)

    def get_relevant_documents(self, query: str):
        return retriever_get_documents(self.retriever, query)[: self.top_k]
