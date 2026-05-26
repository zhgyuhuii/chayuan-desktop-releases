"""94-2 + 94-3:kb_collection_routes 端到端测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    """临时内存 SQLite + 全部 ORM 表。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from chayuan.server.db.base import Base
    from chayuan.server.db.models import kb_collection_model  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr("chayuan.server.db.base.SessionLocal", Session)
    monkeypatch.setattr(
        "chayuan.server.db.session.SessionLocal", Session, raising=False,
    )
    return Session


def _admin():
    return {"id": 1, "role": "admin", "is_guest": False}


def _user(uid=2):
    return {"id": uid, "role": "user", "is_guest": False}


# ---------------------------------------------------------------------------
# CRUD 路由
# ---------------------------------------------------------------------------

def test_create_collection_endpoint(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint,
    )
    ret = create_collection_endpoint(
        payload={"name": "proj_a", "display_name": "项目 A"},
        user=_user(uid=10),
    )
    assert ret["code"] == 0
    assert ret["data"]["name"] == "proj_a"
    assert ret["data"]["owner_id"] == 10


def test_create_collection_missing_name_400(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint,
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        create_collection_endpoint(payload={}, user=_user())
    assert exc.value.status_code == 400


def test_create_collection_dup_400(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint,
    )
    from fastapi import HTTPException
    create_collection_endpoint(payload={"name": "x"}, user=_user())
    with pytest.raises(HTTPException) as exc:
        create_collection_endpoint(payload={"name": "x"}, user=_user())
    assert exc.value.status_code == 400


def test_get_collection_404_when_missing(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        get_collection_endpoint,
    )
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        get_collection_endpoint(collection_id=99999, user=_admin())
    assert exc.value.status_code == 404


def test_list_collections_filtered_by_owner(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, list_collections_endpoint,
    )
    create_collection_endpoint(payload={"name": "a"}, user=_user(uid=1))
    create_collection_endpoint(payload={"name": "b"}, user=_user(uid=2))
    ret = list_collections_endpoint(user=_user(uid=1))
    names = {c["name"] for c in ret["data"]["items"]}
    assert names == {"a"}


def test_owner_check_blocks_other_user_get(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, get_collection_endpoint,
    )
    from fastapi import HTTPException
    d = create_collection_endpoint(
        payload={"name": "private_a"}, user=_user(uid=1),
    )["data"]
    with pytest.raises(HTTPException) as exc:
        get_collection_endpoint(collection_id=d["id"], user=_user(uid=999))
    assert exc.value.status_code == 403


def test_admin_can_view_others_collection(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, get_collection_endpoint,
    )
    d = create_collection_endpoint(
        payload={"name": "admin_view"}, user=_user(uid=1),
    )["data"]
    ret = get_collection_endpoint(collection_id=d["id"], user=_admin())
    assert ret["code"] == 0


def test_guest_can_pass_owner_check(tmp_db):
    """AUTH_REQUIRED=false 时 user 是 guest dict,放行。"""
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, get_collection_endpoint,
    )
    d = create_collection_endpoint(
        payload={"name": "guest_pass"}, user=_user(uid=5),
    )["data"]
    guest = {"id": -1, "role": "user", "is_guest": True}
    ret = get_collection_endpoint(collection_id=d["id"], user=guest)
    assert ret["code"] == 0


def test_update_collection(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, update_collection_endpoint,
    )
    d = create_collection_endpoint(
        payload={"name": "u1", "display_name": "old"}, user=_user(uid=1),
    )["data"]
    ret = update_collection_endpoint(
        collection_id=d["id"],
        payload={"display_name": "new", "description": "desc"},
        user=_user(uid=1),
    )
    assert ret["data"]["display_name"] == "new"
    assert ret["data"]["description"] == "desc"


# ---------------------------------------------------------------------------
# 成员关系
# ---------------------------------------------------------------------------

def test_add_member_kind_validation(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint,
    )
    from fastapi import HTTPException
    d = create_collection_endpoint(
        payload={"name": "c"}, user=_user(uid=1),
    )["data"]
    with pytest.raises(HTTPException) as exc:
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "x", "kind": "structured"},
            user=_user(uid=1),
        )
    assert exc.value.status_code == 400


def test_add_member_owner_mismatch_400(tmp_db):
    """子 KB owner != 集合 owner → 400。"""
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint,
    )
    from fastapi import HTTPException
    d = create_collection_endpoint(
        payload={"name": "c"}, user=_user(uid=1),
    )["data"]

    with patch(
        "chayuan.server.api_server.kb_collection_routes._resolve_ku_owner",
        return_value=999,  # 别人的 KB
    ), pytest.raises(HTTPException) as exc:
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "doc_kb_x", "kind": "document"},
            user=_user(uid=1),
        )
    assert exc.value.status_code == 400
    assert "owner" in exc.value.detail.lower()


def test_add_member_owner_match_ok(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint,
    )
    d = create_collection_endpoint(
        payload={"name": "c"}, user=_user(uid=1),
    )["data"]
    with patch(
        "chayuan.server.api_server.kb_collection_routes._resolve_ku_owner",
        return_value=1,
    ):
        ret = add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "doc_kb_a", "kind": "document"},
            user=_user(uid=1),
        )
    assert ret["code"] == 0


def test_add_member_unknown_owner_passthrough(tmp_db):
    """反查不到 owner(KB 不在我们 schema 里)→ 不强校验,放行。"""
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint,
    )
    d = create_collection_endpoint(
        payload={"name": "c"}, user=_user(uid=1),
    )["data"]
    with patch(
        "chayuan.server.api_server.kb_collection_routes._resolve_ku_owner",
        return_value=None,
    ):
        ret = add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "ku_x", "kind": "image"},
            user=_user(uid=1),
        )
    assert ret["code"] == 0


def test_remove_member(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint, remove_member_endpoint,
    )
    d = create_collection_endpoint(
        payload={"name": "c"}, user=_user(uid=1),
    )["data"]
    with patch(
        "chayuan.server.api_server.kb_collection_routes._resolve_ku_owner",
        return_value=1,
    ):
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "ku_a", "kind": "document"},
            user=_user(uid=1),
        )
    ret = remove_member_endpoint(
        collection_id=d["id"], ku_id="ku_a", user=_user(uid=1),
    )
    assert ret["data"]["removed"] is True


# ---------------------------------------------------------------------------
# 删集合(级联子 KB)
# ---------------------------------------------------------------------------

def test_delete_collection_cascades_member_kbs(tmp_db):
    """删集合时调用 _delete_member_kb 清子 KB。"""
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint,
        delete_collection_endpoint,
    )

    d = create_collection_endpoint(
        payload={"name": "to_del"}, user=_user(uid=1),
    )["data"]
    with patch(
        "chayuan.server.api_server.kb_collection_routes._resolve_ku_owner",
        return_value=1,
    ):
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "doc_a", "kind": "document"},
            user=_user(uid=1),
        )
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "src:5", "kind": "image"},
            user=_user(uid=1),
        )

    delete_calls = []
    def _fake_delete(ku_id, kind, user):
        delete_calls.append((ku_id, kind))

    with patch(
        "chayuan.server.api_server.kb_collection_routes._delete_member_kb",
        _fake_delete,
    ):
        ret = delete_collection_endpoint(
            collection_id=d["id"], user=_user(uid=1),
        )

    assert ret["code"] == 0
    assert len(delete_calls) == 2
    assert ("doc_a", "document") in delete_calls
    assert ("src:5", "image") in delete_calls


def test_delete_collection_continues_on_member_kb_error(tmp_db):
    """单个子 KB 删失败收集到 errors,不阻断其它删除 + 集合删除本身。"""
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint,
        delete_collection_endpoint,
    )

    d = create_collection_endpoint(
        payload={"name": "del_partial"}, user=_user(uid=1),
    )["data"]
    with patch(
        "chayuan.server.api_server.kb_collection_routes._resolve_ku_owner",
        return_value=1,
    ):
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "doc_a", "kind": "document"},
            user=_user(uid=1),
        )
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "src:5", "kind": "image"},
            user=_user(uid=1),
        )

    def _fake_delete(ku_id, kind, user):
        if ku_id == "doc_a":
            raise RuntimeError("milvus connection refused")

    with patch(
        "chayuan.server.api_server.kb_collection_routes._delete_member_kb",
        _fake_delete,
    ):
        ret = delete_collection_endpoint(
            collection_id=d["id"], user=_user(uid=1),
        )

    assert ret["code"] == 0
    assert len(ret["data"]["errors"]) == 1
    assert ret["data"]["errors"][0]["ku_id"] == "doc_a"


# ---------------------------------------------------------------------------
# 搜索(94-3)
# ---------------------------------------------------------------------------

def test_search_collection_no_members_returns_empty(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, search_collection_endpoint,
    )
    d = create_collection_endpoint(
        payload={"name": "empty"}, user=_user(uid=1),
    )["data"]
    ret = search_collection_endpoint(
        collection_id=d["id"],
        payload={"query": "test"},
        user=_user(uid=1),
    )
    assert ret["data"]["items"] == []
    assert ret["data"]["total"] == 0


def test_search_collection_query_required(tmp_db):
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, search_collection_endpoint,
    )
    from fastapi import HTTPException
    d = create_collection_endpoint(
        payload={"name": "x"}, user=_user(uid=1),
    )["data"]
    with pytest.raises(HTTPException) as exc:
        search_collection_endpoint(
            collection_id=d["id"], payload={}, user=_user(uid=1),
        )
    assert exc.value.status_code == 400


def test_search_collection_merges_concurrent_results(tmp_db):
    """两个 member 各返 2 条命中,合并按 score 倒排。"""
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint,
        search_collection_endpoint,
    )

    d = create_collection_endpoint(
        payload={"name": "search_test"}, user=_user(uid=1),
    )["data"]
    with patch(
        "chayuan.server.api_server.kb_collection_routes._resolve_ku_owner",
        return_value=1,
    ):
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "doc_a", "kind": "document"},
            user=_user(uid=1),
        )
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "src:5", "kind": "image"},
            user=_user(uid=1),
        )

    def _fake_search(ku_id, kind, query, top_k, user):
        if kind == "document":
            return [
                {"id": "d1", "content": "doc-1", "score": 0.9, "metadata": {}},
                {"id": "d2", "content": "doc-2", "score": 0.6, "metadata": {}},
            ]
        return [
            {"id": "i1", "content": "img-1", "score": 0.95, "metadata": {}},
            {"id": "i2", "content": "img-2", "score": 0.5, "metadata": {}},
        ]

    with patch(
        "chayuan.server.api_server.kb_collection_routes._search_single_member",
        _fake_search,
    ):
        ret = search_collection_endpoint(
            collection_id=d["id"],
            payload={"query": "hello", "top_k": 5},
            user=_user(uid=1),
        )

    items = ret["data"]["items"]
    assert len(items) == 4
    # 按 score 倒序
    scores = [it["score"] for it in items]
    assert scores == sorted(scores, reverse=True)
    # 来源标记
    kinds = {it["source_kind"] for it in items}
    assert kinds == {"document", "image"}
    # 诊断
    assert len(ret["data"]["diagnostics"]) == 2
    assert all(d["ok"] for d in ret["data"]["diagnostics"])


def test_search_collection_single_member_failure_isolated(tmp_db):
    """单个 member 抛异常 → 其它正常返,失败的标到 diagnostics。"""
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint,
        search_collection_endpoint,
    )

    d = create_collection_endpoint(
        payload={"name": "isolated"}, user=_user(uid=1),
    )["data"]
    with patch(
        "chayuan.server.api_server.kb_collection_routes._resolve_ku_owner",
        return_value=1,
    ):
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "doc_a", "kind": "document"},
            user=_user(uid=1),
        )
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "src:5", "kind": "image"},
            user=_user(uid=1),
        )

    def _fake_search(ku_id, kind, query, top_k, user):
        if kind == "document":
            raise RuntimeError("milvus offline")
        return [{"id": "i1", "content": "img", "score": 0.5, "metadata": {}}]

    with patch(
        "chayuan.server.api_server.kb_collection_routes._search_single_member",
        _fake_search,
    ):
        ret = search_collection_endpoint(
            collection_id=d["id"],
            payload={"query": "hello"},
            user=_user(uid=1),
        )

    # image 仍返了一条
    assert len(ret["data"]["items"]) == 1
    diags = {d["kind"]: d for d in ret["data"]["diagnostics"]}
    assert diags["document"]["ok"] is False
    assert "milvus offline" in diags["document"]["error"]
    assert diags["image"]["ok"] is True


def test_search_collection_member_timeout_skipped(tmp_db):
    """单个 member 超时 → 标到 diagnostics,不阻断其它结果返回。"""
    import time as _time
    from chayuan.server.api_server.kb_collection_routes import (
        create_collection_endpoint, add_member_endpoint,
        search_collection_endpoint,
    )

    d = create_collection_endpoint(
        payload={"name": "timeout_test"}, user=_user(uid=1),
    )["data"]
    with patch(
        "chayuan.server.api_server.kb_collection_routes._resolve_ku_owner",
        return_value=1,
    ):
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "slow_doc", "kind": "document"},
            user=_user(uid=1),
        )
        add_member_endpoint(
            collection_id=d["id"],
            payload={"ku_id": "src:1", "kind": "image"},
            user=_user(uid=1),
        )

    def _fake_search(ku_id, kind, query, top_k, user):
        if kind == "document":
            _time.sleep(2.0)  # 超时阈值 0.5
        return [{"id": kind, "content": "x", "score": 0.5, "metadata": {}}]

    t0 = _time.time()
    with patch(
        "chayuan.server.api_server.kb_collection_routes._search_single_member",
        _fake_search,
    ):
        ret = search_collection_endpoint(
            collection_id=d["id"],
            payload={"query": "hi", "per_member_timeout_s": 0.5},
            user=_user(uid=1),
        )
    elapsed = _time.time() - t0
    # 总耗时不应等满 2 秒
    assert elapsed < 1.5, f"超时未生效,耗时 {elapsed:.2f}s"

    diags = {d["kind"]: d for d in ret["data"]["diagnostics"]}
    assert diags["document"]["ok"] is False
    assert "超时" in diags["document"]["error"]
