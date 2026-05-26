"""GraphRAG 检索器。

提供两条增强路径（都**不调 LLM**，毫秒级）：

1. **Global search（社区摘要召回）**：由于社区摘要以普通 Document 形式已在向量库里，
   走 hybrid_service 原路径天然召回；本模块额外做：确保结果里**至少保留** K 条
   社区摘要，避免它们被大量 chunk-level 命中"挤走"

2. **Local search（实体邻居扩展）**：
   - 把 query 里的文本跟 kb 的 entity 表做简单字符串 / 模糊匹配找种子
   - 从种子沿关系遍历 N 跳，拿到邻居 + 关系描述
   - 组装成一个 Document 返回，与原 chunks 一起进 rerank

两路径都通过 ``graphrag_augment(kb_service, query)`` 统一暴露给 hybrid_service。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.documents import Document
from sqlalchemy.orm import Session

from chayuan.server.db.models.graphrag_model import (
    GraphCommunityModel, GraphEntityModel, GraphRelationModel,
)
from chayuan.server.db.session import session_scope, with_session

logger = logging.getLogger("chayuan.graphrag.retriever")


@with_session
def _find_seed_entities(
    session: Session, kb_name: str, query: str, *, top_n: int = 6,
) -> List[Dict[str, Any]]:
    """简化种子发现：按字符串子串 / LIKE 匹配。

    后续可升级为 entity 名称向量化 + 近邻检索，这里先零额外存储。
    """
    if not query:
        return []
    # 取所有实体（若 kb 里实体数 > 5000 可考虑加 ts_vector 索引）
    rows = session.query(
        GraphEntityModel.id, GraphEntityModel.name,
        GraphEntityModel.entity_type, GraphEntityModel.description,
        GraphEntityModel.mention_count,
    ).filter(GraphEntityModel.kb_name == kb_name).all()
    if not rows:
        return []
    # 简易相关性打分：子串优先、词级 overlap 次之
    words = [w for w in re.split(r"[\s,，。！？:：;；()（）]+", query) if len(w) >= 2]
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for eid, name, etype, desc, mc in rows:
        score = 0.0
        low_name = (name or "").lower()
        if low_name and low_name in query.lower():
            score += 3.0
        for w in words:
            if w.lower() in low_name:
                score += 1.5
            if desc and w.lower() in desc.lower():
                score += 0.5
        if score > 0:
            scored.append((
                score + min(float(mc or 1) / 20.0, 1.0),  # 出现频次微加权
                {"id": int(eid), "name": name, "type": etype,
                 "description": desc or "", "mention_count": int(mc or 1)},
            ))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _sc, item in scored[: int(top_n)]]


@with_session
def _expand_neighbors(
    session: Session, kb_name: str, seed_ids: List[int],
    *, hops: int = 1, max_neighbors: int = 40,
) -> Tuple[Dict[int, Dict[str, Any]], List[Dict[str, Any]]]:
    """从 seed_ids 出发扩展 N 跳，返回 (entity_id→entity, [relation])。

    简化实现：BFS，用 GraphRelationModel 的无向邻接；保留边描述。
    """
    if not seed_ids:
        return {}, []
    frontier: Set[int] = set(int(x) for x in seed_ids)
    visited: Set[int] = set()
    all_entities: Dict[int, Dict[str, Any]] = {}
    all_relations: List[Dict[str, Any]] = []

    for _ in range(max(1, int(hops))):
        current = list(frontier - visited)
        visited.update(current)
        if not current:
            break
        rows = session.query(
            GraphRelationModel.id, GraphRelationModel.src_entity_id,
            GraphRelationModel.dst_entity_id, GraphRelationModel.relation_type,
            GraphRelationModel.description, GraphRelationModel.weight,
        ).filter(
            GraphRelationModel.kb_name == kb_name,
            (GraphRelationModel.src_entity_id.in_(current))
            | (GraphRelationModel.dst_entity_id.in_(current)),
        ).limit(int(max_neighbors) * 4).all()
        next_nodes: Set[int] = set()
        for rid, src, dst, rt, desc, w in rows:
            all_relations.append({
                "id": int(rid), "src": int(src), "dst": int(dst),
                "type": rt or "", "description": desc or "", "weight": int(w or 1),
            })
            next_nodes.add(int(src))
            next_nodes.add(int(dst))
        frontier = next_nodes
        if len(all_relations) >= int(max_neighbors):
            break

    all_ids = {r["src"] for r in all_relations} | {r["dst"] for r in all_relations} | set(visited)
    if all_ids:
        erows = session.query(
            GraphEntityModel.id, GraphEntityModel.name,
            GraphEntityModel.entity_type, GraphEntityModel.description,
        ).filter(
            GraphEntityModel.kb_name == kb_name,
            GraphEntityModel.id.in_(list(all_ids)),
        ).all()
        for eid, name, etype, desc in erows:
            all_entities[int(eid)] = {
                "id": int(eid), "name": name, "type": etype or "",
                "description": desc or "",
            }
    return all_entities, all_relations[: int(max_neighbors)]


def _build_local_context_doc(
    kb_name: str, query: str, hops: int = 1,
) -> Optional[Document]:
    """Local search：生成一条"实体邻居图谱摘要" Document。

    N-6：优先走 entity 向量索引（毫秒级 cosine）；失败才回退到 DB LIKE 扫。
    """
    from chayuan.server.file_rag.graphrag.entity_index import find_seed_entities
    seeds = find_seed_entities(kb_name, query, top_k=6)
    if not seeds:
        return None
    seed_ids = [s["id"] for s in seeds]
    entities, relations = _expand_neighbors(
        kb_name, seed_ids, hops=hops, max_neighbors=50,
    )
    if not entities:
        return None

    lines: List[str] = [f"【知识图谱 Local 上下文（围绕：{', '.join(s['name'] for s in seeds[:3])}）】"]
    # 实体列表（按 mention_count 大的 / 种子 优先）
    seed_id_set = set(seed_ids)
    listed = 0
    for ent in sorted(entities.values(),
                       key=lambda x: (0 if x["id"] in seed_id_set else 1, -len(x.get("description") or ""))):
        if listed >= 20:
            break
        marker = "★" if ent["id"] in seed_id_set else "·"
        lines.append(f"{marker} {ent['name']} ({ent['type'] or 'OTHER'}): "
                      f"{(ent['description'] or '')[:100]}")
        listed += 1
    # 关系列表
    if relations:
        lines.append("\n【关系】")
        for r in relations[:30]:
            sname = (entities.get(r["src"], {}) or {}).get("name") or f"#{r['src']}"
            dname = (entities.get(r["dst"], {}) or {}).get("name") or f"#{r['dst']}"
            lines.append(f"- {sname} -[{r['type'] or ''}]-> {dname}: "
                         f"{(r['description'] or '')[:100]}")

    return Document(
        page_content="\n".join(lines),
        metadata={
            "graphrag_type": "local_context",
            "graphrag_kb": kb_name,
            "seed_entity_names": [s["name"] for s in seeds],
            "source": f"__graphrag__/{kb_name}/local",
            "id": f"graphrag_local:{kb_name}:{hash(query) & 0xffffffff:x}",
        },
    )


@with_session
def _fetch_top_communities(
    session: Session, kb_name: str, limit: int = 3,
) -> List[Dict[str, Any]]:
    """拉前 N 个社区摘要（按 members 数降序）作为 global context 备选。"""
    rows = session.query(GraphCommunityModel).filter(
        GraphCommunityModel.kb_name == kb_name,
    ).all()
    if not rows:
        return []
    import json

    def _members_len(r):
        try:
            return len(json.loads(r.members_json or "[]"))
        except Exception:
            return 0
    rows.sort(key=_members_len, reverse=True)
    out = []
    for r in rows[: int(limit)]:
        out.append({
            "community_key": r.community_key,
            "summary": r.summary or "",
            "summary_doc_id": r.summary_doc_id or "",
            "members_count": _members_len(r),
        })
    return out


# ---------------------------------------------------------------------------
# 对外：graphrag_augment — hybrid_service 调用入口
# ---------------------------------------------------------------------------

def graphrag_augment(
    *,
    kb_service,
    query: str,
    top_k: int = 5,
) -> List[Document]:
    """返回若干 Document 供 hybrid_service 合入候选池。

    - 1 条 Local Context doc（如果命中种子）
    - 最多 2 条 Community Summary doc（做 Global search 兜底）
    """
    try:
        from chayuan.settings import Settings
        hops = int(getattr(Settings.kb_settings, "GRAPHRAG_LOCAL_NEIGHBOR_HOPS", 1) or 1)
    except Exception:  # noqa: BLE001
        hops = 1
    kb_name = getattr(kb_service, "kb_name", "")
    if not kb_name:
        return []
    out: List[Document] = []
    try:
        local_doc = _build_local_context_doc(kb_name, query, hops=hops)
        if local_doc is not None:
            out.append(local_doc)
    except Exception as e:  # noqa: BLE001
        logger.debug("graphrag local 构建失败（忽略）：%r", e)
    try:
        communities = _fetch_top_communities(kb_name, limit=2)
        for c in communities:
            if not c.get("summary"):
                continue
            out.append(Document(
                page_content=c["summary"],
                metadata={
                    "graphrag_type": "community_cached",
                    "graphrag_kb": kb_name,
                    "graphrag_community_key": c["community_key"],
                    "members_count": c["members_count"],
                    "source": f"__graphrag__/{kb_name}/community/{c['community_key']}",
                    "id": c["summary_doc_id"] or f"graphrag_comm:{kb_name}:{c['community_key']}",
                },
            ))
    except Exception as e:  # noqa: BLE001
        logger.debug("graphrag community 拉取失败（忽略）：%r", e)
    return out
