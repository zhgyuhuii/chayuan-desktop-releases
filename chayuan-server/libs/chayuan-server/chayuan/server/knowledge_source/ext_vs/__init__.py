"""外部向量库（Bring-Your-Own Vector Store）知识源子包。

用户自己已经把文档切分 + embed 好并存到 Milvus / pgvector / ES / ChromaDB /
Zilliz / Relyt 里，本模块把这些"外部 collection"登记为 ``kind="vs"`` 的知识源，
和已有的 SQL / Mongo / ES / 受管 Vector KB 一视同仁地挂到 orchestrator 的
并行检索管线上。

公共入口：
- ``ExternalVsConnector``  —— BaseConnector 子类，orchestrator 直接用
- ``SUPPORTED_DIALECTS``   —— UI 下拉选项的权威源
- ``build_vectorstore(spec)`` / ``introspect(spec)`` —— backends 的分发点
"""
from __future__ import annotations

from chayuan.server.knowledge_source.ext_vs.backends import (
    SUPPORTED_DIALECTS,
    build_vectorstore,
    introspect_backend,
)
from chayuan.server.knowledge_source.ext_vs.connector import ExternalVsConnector

__all__ = [
    "ExternalVsConnector",
    "SUPPORTED_DIALECTS",
    "build_vectorstore",
    "introspect_backend",
]
