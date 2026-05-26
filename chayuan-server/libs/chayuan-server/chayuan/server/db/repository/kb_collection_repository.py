"""94-1:kb_collections 仓储层。

API 风格沿用其它 repo:``with_session`` 装饰器自动开 session,函数返 dict 给上层。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from chayuan.server.db.models.kb_collection_model import (
    KBCollectionMemberModel, KBCollectionModel,
)
from chayuan.server.db.session import with_session


# ---------------------------------------------------------------------------
# 集合 CRUD
# ---------------------------------------------------------------------------

@with_session
def create_collection(
    session,
    name: str,
    owner_id: int,
    *,
    display_name: str = "",
    description: str = "",
    visibility: str = "private",
) -> Dict[str, Any]:
    """新建集合。返 dict;name 重复抛 ValueError。"""
    if not name or not name.strip():
        raise ValueError("name required")
    obj = KBCollectionModel(
        name=name.strip(),
        display_name=display_name.strip() or name.strip(),
        description=(description or "").strip(),
        owner_id=int(owner_id),
        visibility=(visibility or "private").strip(),
    )
    session.add(obj)
    try:
        session.flush()
    except IntegrityError as e:
        raise ValueError(f"集合名 '{name}' 已存在") from e
    return obj.to_dict()


@with_session
def get_collection(session, collection_id: int) -> Optional[Dict[str, Any]]:
    obj = session.get(KBCollectionModel, int(collection_id))
    if obj is None:
        return None
    d = obj.to_dict()
    d["members"] = [
        m.to_dict() for m in
        session.query(KBCollectionMemberModel)
        .filter(KBCollectionMemberModel.collection_id == obj.id)
        .order_by(KBCollectionMemberModel.sort_order.asc(),
                  KBCollectionMemberModel.id.asc())
        .all()
    ]
    return d


@with_session
def get_collection_by_name(session, name: str) -> Optional[Dict[str, Any]]:
    obj = (
        session.query(KBCollectionModel)
        .filter(KBCollectionModel.name == (name or "").strip())
        .first()
    )
    return obj.to_dict() if obj else None


@with_session
def list_collections(
    session, *, owner_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """列出集合;owner_id 过滤(None 表示不限)。"""
    q = session.query(KBCollectionModel)
    if owner_id is not None:
        q = q.filter(KBCollectionModel.owner_id == int(owner_id))
    out: List[Dict[str, Any]] = []
    for obj in q.order_by(KBCollectionModel.create_time.desc()).all():
        d = obj.to_dict()
        # member 计数(不展开)
        d["member_count"] = (
            session.query(KBCollectionMemberModel)
            .filter(KBCollectionMemberModel.collection_id == obj.id)
            .count()
        )
        out.append(d)
    return out


@with_session
def update_collection(
    session, collection_id: int, *,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    obj = session.get(KBCollectionModel, int(collection_id))
    if obj is None:
        return None
    if display_name is not None:
        obj.display_name = display_name.strip() or obj.display_name
    if description is not None:
        obj.description = description.strip()
    session.flush()
    return obj.to_dict()


@with_session
def delete_collection(session, collection_id: int) -> bool:
    """删集合 + 所有 member 关系。**不**真删子 KB(级联删子 KB 由上层
    service 在事务外做,因为子 KB 删除是跨 schema 操作:文档库要清 milvus 索引,
    图像库要清向量 + 文件)。

    返 True 表示集合存在并已删;False 表示原本就不存在。
    """
    obj = session.get(KBCollectionModel, int(collection_id))
    if obj is None:
        return False
    session.query(KBCollectionMemberModel).filter(
        KBCollectionMemberModel.collection_id == obj.id,
    ).delete(synchronize_session=False)
    session.delete(obj)
    return True


# ---------------------------------------------------------------------------
# 成员关系
# ---------------------------------------------------------------------------

@with_session
def add_member(
    session, collection_id: int, ku_id: str, kind: str,
    *, sort_order: int = 0,
) -> Dict[str, Any]:
    """加成员;ku_id 唯一(已在别的集合 → 抛 ValueError)。

    kind 只能是 ``document`` 或 ``image``(94 范围);sort_order 0 = 跟随 id 顺序。
    """
    if kind not in ("document", "image"):
        raise ValueError(f"kind 必须是 document / image,got {kind!r}")
    if not ku_id or not str(ku_id).strip():
        raise ValueError("ku_id required")
    coll = session.get(KBCollectionModel, int(collection_id))
    if coll is None:
        raise ValueError(f"集合 {collection_id} 不存在")
    member = KBCollectionMemberModel(
        collection_id=coll.id, ku_id=str(ku_id).strip(),
        kind=kind, sort_order=int(sort_order or 0),
    )
    session.add(member)
    try:
        session.flush()
    except IntegrityError as e:
        raise ValueError(f"ku_id {ku_id!r} 已在某个集合中") from e
    return member.to_dict()


@with_session
def list_members(session, collection_id: int) -> List[Dict[str, Any]]:
    return [
        m.to_dict() for m in
        session.query(KBCollectionMemberModel)
        .filter(KBCollectionMemberModel.collection_id == int(collection_id))
        .order_by(KBCollectionMemberModel.sort_order.asc(),
                  KBCollectionMemberModel.id.asc())
        .all()
    ]


@with_session
def remove_member(session, collection_id: int, ku_id: str) -> bool:
    """从集合移除某 ku_id;返 True/False 表示是否真删了一行。"""
    n = (
        session.query(KBCollectionMemberModel)
        .filter(
            KBCollectionMemberModel.collection_id == int(collection_id),
            KBCollectionMemberModel.ku_id == str(ku_id).strip(),
        )
        .delete(synchronize_session=False)
    )
    return n > 0


@with_session
def get_collection_for_ku(session, ku_id: str) -> Optional[Dict[str, Any]]:
    """反查:某 ku_id 属于哪个集合(若有)。"""
    m = (
        session.query(KBCollectionMemberModel)
        .filter(KBCollectionMemberModel.ku_id == str(ku_id).strip())
        .first()
    )
    if m is None:
        return None
    obj = session.get(KBCollectionModel, m.collection_id)
    return obj.to_dict() if obj else None
