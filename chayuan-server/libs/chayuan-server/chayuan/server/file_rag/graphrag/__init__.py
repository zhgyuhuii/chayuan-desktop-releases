"""GraphRAG 子系统（Microsoft GraphRAG 思路的工程化实现）。

build 阶段（离线）：
  1. 对 KB 每个 chunk 调 LLM 抽实体 + 关系
  2. 聚合成 networkx 图，Louvain 社区检测
  3. 每个社区的文本并合后 LLM 生成摘要
  4. 摘要以 Document 形式入向量库（metadata.graphrag_type=community）
  5. entity / relation / community 元数据进 SQL 表

query 阶段（在线，零 LLM 额外成本）：
  - Local search：向量/规则找种子实体 → 遍历 N 跳邻居 → 拼邻居关联原始 chunk
  - Global search：向量库召回社区摘要（天然走 hybrid，这里只需补一次元数据追加）
"""

from chayuan.server.file_rag.graphrag.builder import build_graphrag_for_kb  # noqa: F401
