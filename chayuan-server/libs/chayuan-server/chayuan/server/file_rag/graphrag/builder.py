"""GraphRAG 构建器（一次性离线任务，成本最高的环节）。

流程：
  1. 取 KB 的所有 level=0 chunks
  2. 对每 chunk 调 extractor.extract_entities_relations（LLM 一次）
  3. 聚合到本地字典：entity 去重（按 kb+name），relation 去重（按 kb+src+dst+type）
  4. 清除本 KB 之前的 entity / relation / community 记录（幂等）
  5. 批量写入 graphrag_entity / graphrag_relation
  6. 用 networkx + python-louvain 做社区检测（python-louvain 未装则回退为
     每个连通分量即一个社区）
  7. 对每个社区做摘要（LLM）并入向量库（metadata.graphrag_type=community）
  8. 把社区信息写 graphrag_community

**成本**：N 个 chunk + C 个社区 = N + C 次 LLM。对 1k chunks 约 1.5 美元；
10k chunks 约 15 美元。建议深夜跑。

**幂等**：重跑会先清空本 KB 的 entity / relation / community 表 + 向量库里
graphrag_type=community 的摘要 Document。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.documents import Document
from sqlalchemy.orm import Session

from chayuan.server.db.models.graphrag_model import (
    GraphCommunityModel, GraphEntityModel, GraphRelationModel,
)
from chayuan.server.db.session import session_scope, with_session
from chayuan.server.file_rag.graphrag.extractor import extract_entities_relations

logger = logging.getLogger("chayuan.graphrag.builder")


COMMUNITY_SUMMARY_SYSTEM = """你是知识图谱社区摘要专家。请对一组相关实体及其关系形成**主题摘要**：
- 150-300 字，围绕"这些实体共同描述了什么"
- 覆盖主要人物 / 组织 / 概念 / 时间；能回答"这组实体是关于什么主题的？"
- 末尾列出 2-5 个关键词（逗号分隔）"""


@dataclass
class GraphRagBuildReport:
    kb_name: str
    chunks_processed: int = 0
    entities: int = 0
    relations: int = 0
    communities: int = 0
    elapsed_sec: float = 0.0
    error: Optional[str] = None


def build_graphrag_for_kb(
    kb_name: str,
    *,
    llm_model: Optional[str] = None,
    max_chunks: int = 10_000,
    community_min_size: int = 2,
    extract_fn: Optional[Callable] = None,
    summarize_fn: Optional[Callable[[str], str]] = None,
) -> GraphRagBuildReport:
    """对指定 KB 跑完整 GraphRAG build。"""
    import time
    t0 = time.time()
    report = GraphRagBuildReport(kb_name=kb_name)

    try:
        from chayuan.server.knowledge_base.kb_service.base import KBServiceFactory
    except Exception as e:  # noqa: BLE001
        report.error = f"KBServiceFactory 不可用：{e}"
        return report
    kb = KBServiceFactory.get_service_by_name(kb_name)
    if kb is None:
        report.error = f"KB {kb_name!r} 不存在"
        return report

    # 1) 拉 chunks
    docs = _load_level0_docs(kb, max_chunks=max_chunks)
    if not docs:
        report.error = "KB 中无 chunks"
        return report
    report.chunks_processed = len(docs)

    extract = extract_fn or (lambda text: extract_entities_relations(text, llm_model=llm_model))

    # 2) 扫 chunks 抽实体关系（按 chunk 并行可加快；这里先顺序，便于失败单点重试）
    entity_bag: Dict[str, Dict[str, Any]] = {}        # name → {type, description, chunks}
    relation_bag: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for d in docs:
        chunk_id = str((d.metadata or {}).get("id") or "")
        try:
            res = extract(d.page_content or "") or {}
        except Exception as e:  # noqa: BLE001
            logger.debug("extract 失败（跳过该 chunk）：%r", e)
            continue
        for e in (res.get("entities") or []):
            name = e.get("name") or ""
            if not name:
                continue
            slot = entity_bag.setdefault(name, {
                "type": e.get("type") or "OTHER",
                "description": e.get("description") or "",
                "first_seen_chunk_id": chunk_id,
                "mention_count": 0,
            })
            slot["mention_count"] += 1
            # description 用最长的那条
            new_desc = e.get("description") or ""
            if len(new_desc) > len(slot["description"]):
                slot["description"] = new_desc[:500]
        for r in (res.get("relations") or []):
            k = (r.get("src") or "", r.get("dst") or "", r.get("type") or "")
            if "" in k:
                continue
            slot = relation_bag.setdefault(k, {
                "description": r.get("description") or "",
                "weight": 0,
                "source_chunk_id": chunk_id,
            })
            slot["weight"] += 1

    report.entities = len(entity_bag)
    report.relations = len(relation_bag)

    # 3) 清旧数据
    _purge_old_graphrag(kb_name, kb)

    # 4) 写 entity + relation 表
    name_to_id = _write_entities(kb_name, entity_bag)
    _write_relations(kb_name, relation_bag, name_to_id)

    # 5) 社区检测
    communities = _detect_communities(relation_bag, name_to_id,
                                        min_size=int(community_min_size))
    logger.info("[graphrag] kb=%s communities=%d", kb_name, len(communities))

    # 6) 摘要 + 写入向量库 + 写 community 表
    summarize = summarize_fn or _default_summarizer(llm_model)
    total_community_written = 0
    for comm_key, member_ids in communities.items():
        if len(member_ids) < int(community_min_size):
            continue
        # 准备摘要输入
        ctx_lines = _community_context(kb_name, member_ids, entity_bag, relation_bag, name_to_id)
        try:
            summary = summarize(ctx_lines)
        except Exception as e:  # noqa: BLE001
            logger.debug("community summary 失败（跳过）：%r", e)
            continue
        if not summary:
            continue
        summary_doc_id = f"graphrag:{kb_name}:C{comm_key}:{uuid.uuid4().hex[:8]}"
        summary_doc = Document(
            page_content=summary,
            metadata={
                "graphrag_type": "community",
                "graphrag_kb": kb_name,
                "graphrag_community_key": str(comm_key),
                "graphrag_member_count": len(member_ids),
                "id": summary_doc_id,
                "source": f"__graphrag__/{kb_name}/C{comm_key}",
            },
        )
        try:
            kb.do_add_doc(docs=[summary_doc])
        except Exception as e:  # noqa: BLE001
            logger.debug("写入社区摘要向量库失败（跳过）：%r", e)
            continue
        _write_community(kb_name, comm_key, member_ids, summary, summary_doc_id)
        total_community_written += 1

    report.communities = total_community_written
    report.elapsed_sec = round(time.time() - t0, 2)
    # 文档集变了 → 失效 BM25
    try:
        from chayuan.server.file_rag.hybrid_service import invalidate_bm25_cache
        invalidate_bm25_cache(kb_name)
    except Exception:  # noqa: BLE001
        pass
    # N-6：entity 表重写了 → 清本 KB 的 entity 向量索引缓存，下次查询重建
    try:
        from chayuan.server.file_rag.graphrag.entity_index import invalidate as _inv_entity
        _inv_entity(kb_name)
    except Exception:  # noqa: BLE001
        pass
    return report


# ---------------------------------------------------------------------------
# 写表 / 清表
# ---------------------------------------------------------------------------

@with_session
def _purge_old_graphrag(session: Session, kb_name: str, kb) -> None:
    session.query(GraphCommunityModel).filter(
        GraphCommunityModel.kb_name == kb_name,
    ).delete(synchronize_session=False)
    session.query(GraphRelationModel).filter(
        GraphRelationModel.kb_name == kb_name,
    ).delete(synchronize_session=False)
    session.query(GraphEntityModel).filter(
        GraphEntityModel.kb_name == kb_name,
    ).delete(synchronize_session=False)
    # 删向量库里的社区摘要 Document
    try:
        vs = _get_vector_store(kb)
        if vs is not None:
            ids: List[str] = []
            ds = getattr(vs, "docstore", None)
            inner = getattr(ds, "_dict", None)
            if isinstance(inner, dict):
                for did, doc in inner.items():
                    if (doc.metadata or {}).get("graphrag_type") == "community":
                        ids.append(str(did))
            if ids and hasattr(kb, "del_doc_by_ids"):
                kb.del_doc_by_ids(ids)
    except Exception as e:  # noqa: BLE001
        logger.debug("清除旧 graphrag 摘要文档失败：%r", e)


def _write_entities(kb_name: str, entity_bag: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    """批量入库并返回 name → entity_id 映射。"""
    name_to_id: Dict[str, int] = {}
    with session_scope() as s:
        for name, data in entity_bag.items():
            row = GraphEntityModel(
                kb_name=kb_name,
                name=name[:200],
                entity_type=(data.get("type") or "OTHER")[:64],
                description=(data.get("description") or "")[:4000],
                first_seen_chunk_id=(data.get("first_seen_chunk_id") or "")[:128],
                mention_count=int(data.get("mention_count") or 1),
            )
            s.add(row)
            s.flush()
            name_to_id[name] = int(row.id)
    return name_to_id


def _write_relations(
    kb_name: str,
    relation_bag: Dict[Tuple[str, str, str], Dict[str, Any]],
    name_to_id: Dict[str, int],
) -> None:
    with session_scope() as s:
        for (src, dst, rtype), data in relation_bag.items():
            sid = name_to_id.get(src)
            did = name_to_id.get(dst)
            if sid is None or did is None:
                continue
            s.add(GraphRelationModel(
                kb_name=kb_name,
                src_entity_id=int(sid), dst_entity_id=int(did),
                relation_type=rtype[:64],
                description=(data.get("description") or "")[:4000],
                weight=int(data.get("weight") or 1),
                source_chunk_id=(data.get("source_chunk_id") or "")[:128],
            ))


def _write_community(
    kb_name: str, comm_key: str, member_ids: List[int],
    summary: str, summary_doc_id: str,
) -> None:
    import json
    with session_scope() as s:
        s.add(GraphCommunityModel(
            kb_name=kb_name, level=0,
            community_key=str(comm_key)[:32],
            members_json=json.dumps(list(member_ids), ensure_ascii=False),
            summary=(summary or "")[:8000],
            summary_doc_id=(summary_doc_id or "")[:128],
        ))


# ---------------------------------------------------------------------------
# 社区检测（Louvain）+ 降级
# ---------------------------------------------------------------------------

def _detect_communities(
    relation_bag: Dict[Tuple[str, str, str], Dict[str, Any]],
    name_to_id: Dict[str, int],
    *, min_size: int = 2,
) -> Dict[str, List[int]]:
    """返回 community_key → [entity_id]。"""
    try:
        import networkx as nx
    except Exception as e:  # noqa: BLE001
        logger.warning("networkx 不可用，所有实体作为一个大社区：%r", e)
        return {"0": list(name_to_id.values())}

    g = nx.Graph()
    for eid in name_to_id.values():
        g.add_node(int(eid))
    for (src, dst, _rtype), data in relation_bag.items():
        s_id, d_id = name_to_id.get(src), name_to_id.get(dst)
        if s_id is None or d_id is None:
            continue
        w = int(data.get("weight") or 1)
        if g.has_edge(s_id, d_id):
            g[s_id][d_id]["weight"] = g[s_id][d_id].get("weight", 1) + w
        else:
            g.add_edge(s_id, d_id, weight=w)

    # Louvain 优先；未装回退连通分量
    try:
        import community as community_louvain  # type: ignore
        partition = community_louvain.best_partition(g, weight="weight")
        out: Dict[str, List[int]] = {}
        for node, comm_id in partition.items():
            out.setdefault(str(comm_id), []).append(int(node))
        return {k: v for k, v in out.items() if len(v) >= int(min_size)}
    except Exception as e:  # noqa: BLE001
        logger.info("python-louvain 不可用，回退连通分量：%r", e)
    try:
        groups: Dict[str, List[int]] = {}
        for i, comp in enumerate(nx.connected_components(g)):
            groups[str(i)] = [int(n) for n in comp]
        return {k: v for k, v in groups.items() if len(v) >= int(min_size)}
    except Exception as e:  # noqa: BLE001
        logger.warning("连通分量计算失败，整个图作为一社区：%r", e)
        return {"0": list(g.nodes())}


# ---------------------------------------------------------------------------
# 社区摘要 prompt 组装
# ---------------------------------------------------------------------------

def _community_context(
    kb_name: str, member_ids: List[int],
    entity_bag: Dict[str, Dict[str, Any]],
    relation_bag: Dict[Tuple[str, str, str], Dict[str, Any]],
    name_to_id: Dict[str, int],
) -> str:
    id_to_name = {v: k for k, v in name_to_id.items()}
    lines = [f"【社区实体（{len(member_ids)} 个）】"]
    member_names = set()
    for eid in member_ids[:80]:
        name = id_to_name.get(int(eid))
        if not name:
            continue
        member_names.add(name)
        data = entity_bag.get(name, {})
        lines.append(f"- {name} ({data.get('type') or 'OTHER'}): "
                     f"{(data.get('description') or '')[:120]}")
    lines.append("\n【关键关系】")
    rel_shown = 0
    for (src, dst, rtype), data in relation_bag.items():
        if src not in member_names or dst not in member_names:
            continue
        lines.append(f"- {src} -[{rtype}]-> {dst}: "
                     f"{(data.get('description') or '')[:120]}")
        rel_shown += 1
        if rel_shown >= 60:
            break
    return "\n".join(lines)


def _default_summarizer(llm_model: Optional[str]) -> Callable[[str], str]:
    def _do(text: str) -> str:
        try:
            from chayuan.server.observability.langfuse_integration import (
                inject_into_callbacks,
            )
            from chayuan.server.utils import get_ChatOpenAI, get_default_llm
            model = (llm_model or get_default_llm()).strip()
            llm = get_ChatOpenAI(
                model_name=model, temperature=0.0, streaming=False,
                callbacks=inject_into_callbacks([]) or None,
            )
            resp = llm.invoke([
                {"role": "system", "content": COMMUNITY_SUMMARY_SYSTEM},
                {"role": "user", "content": text[:6000]},
            ])
            return (getattr(resp, "content", None) or "").strip()
        except Exception as e:  # noqa: BLE001
            logger.debug("community summarizer 失败：%r", e)
            return ""
    return _do


# ---------------------------------------------------------------------------
# 复用：KB docstore 工具
# ---------------------------------------------------------------------------

def _load_level0_docs(kb, max_chunks: int = 10_000) -> List[Document]:
    vs = _get_vector_store(kb)
    if vs is None:
        return []
    try:
        ds = getattr(vs, "docstore", None)
        inner = getattr(ds, "_dict", None)
        if isinstance(inner, dict):
            out = []
            for d in inner.values():
                meta = d.metadata or {}
                if int(meta.get("raptor_level") or 0) != 0:
                    continue
                if meta.get("graphrag_type") == "community":
                    continue
                out.append(d)
                if len(out) >= max_chunks:
                    break
            return out
    except Exception:  # noqa: BLE001
        pass
    try:
        return vs.similarity_search("", k=int(max_chunks))
    except Exception:  # noqa: BLE001
        return []


def _get_vector_store(kb):
    for attr in ("vs", "vector_store", "_vs", "_vectorstore"):
        v = getattr(kb, attr, None)
        if v is not None:
            return v
    for meth in ("load_vector_store", "_load_vector_store"):
        fn = getattr(kb, meth, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                continue
    return None
