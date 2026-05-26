"""RAPTOR (Recursive Abstractive Processing for Tree-Organized Retrieval)。

Stanford 2024 提出的层次化检索：对现有 chunks 递归聚类 + LLM 摘要，形成多层树。
查询时"叶子具体片段"和"上级摘要"同时检索，既保细节又保全局。

设计要点（与论文对齐但做了工程简化）：
- 聚类算法：GMM（参数少、接受"模糊成员"，与论文一致）；UMAP 降维可选
- 摘要生成：LLM，prompt 走我们项目统一入口（含 Langfuse / 治理 trace）
- 存储：**复用 kb 的 vector store**，把每级摘要作为额外 Document 存入；
  metadata 打 ``raptor_level`` / ``raptor_cluster_id``，检索时自然出现在结果池
- 失败 fail-soft：某一层构建失败不影响下层；用户看到"部分摘要树"也好过没有
"""

from chayuan.server.file_rag.raptor.builder import build_raptor_for_kb  # noqa: F401
from chayuan.server.file_rag.raptor.retriever import (  # noqa: F401
    raptor_docs_metadata,
)
