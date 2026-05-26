"""回归:knowledge_base.file_count 统计漂移 → "幽灵知识库"。

复现的线上故障
==============
用户上传文档时 embedding 服务对超长块返 500,上传失败。``add_doc`` 是
"先 delete_doc 再 do_add_doc 再 add_file_to_db(+1)";do_add_doc 失败后
那个 +1 不执行,只剩 delete_doc 里的 -1 → ``file_count`` 净漂移变负。

一旦 file_count 变负:
* ``list_kbs_from_db()`` 旧实现 ``filter(file_count > -1)`` 把它过滤掉 →
  知识库从列表 / UI 消失,用户以为已删除;
* 但 ``load_kb_from_db()`` 没有该过滤,仍查得到 → 建同名库报
  "已存在同名知识库" → 成了既看不见又建不了的死结。

本测试锁死三处修复:
1. ``delete_file_from_db`` 把 file_count 下限钳在 0,绝不为负;
2. ``list_kbs_from_db()``("列出全部"语义)不再按 file_count 过滤,
   负数 / NULL 的 KB 一样列出;
3. 对外暴露的 file_count 恒被钳成 >= 0。
"""
from __future__ import annotations

import types

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    """临时内存 SQLite + 全部 ORM 表。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from chayuan.server.db.base import Base
    # import 模型 module 让 ORM 注册到 Base.metadata
    from chayuan.server.db.models import knowledge_base_model  # noqa: F401
    from chayuan.server.db.models import knowledge_file_model  # noqa: F401
    from chayuan.server.db.models import user_model  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    monkeypatch.setattr("chayuan.server.db.base.SessionLocal", Session)
    monkeypatch.setattr(
        "chayuan.server.db.session.SessionLocal", Session, raising=False,
    )
    return Session


def _add_kb(Session, name, file_count):
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    s = Session()
    s.add(KnowledgeBaseModel(
        kb_name=name, kb_info="", vs_type="faiss",
        embed_model="bge-m3", file_count=file_count,
    ))
    s.commit()
    s.close()


def _kb_file_count(Session, name):
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    s = Session()
    row = s.query(KnowledgeBaseModel).filter(
        KnowledgeBaseModel.kb_name == name).first()
    fc = row.file_count if row else None
    s.close()
    return fc


def test_delete_file_from_db_floors_count_at_zero(tmp_db):
    """file_count=0 的 KB 再删一个文件,count 不能变成 -1。"""
    from chayuan.server.db.models.knowledge_file_model import KnowledgeFileModel
    from chayuan.server.db.repository.knowledge_file_repository import (
        delete_file_from_db,
    )

    _add_kb(tmp_db, "test", file_count=0)
    s = tmp_db()
    s.add(KnowledgeFileModel(file_name="doc.md", kb_name="test"))
    s.commit()
    s.close()

    # delete_doc 只读 .filename / .kb_name,用最小 stub 即可
    kb_file = types.SimpleNamespace(filename="doc.md", kb_name="test")
    delete_file_from_db(kb_file)

    # 旧实现这里会是 -1(幽灵库根因);修复后钳在 0
    assert _kb_file_count(tmp_db, "test") == 0


def test_list_kbs_does_not_hide_negative_count(tmp_db):
    """file_count 为负的 KB 必须仍出现在 list_kbs_from_db() 里。"""
    from chayuan.server.db.repository.knowledge_base_repository import (
        list_kbs_from_db,
    )

    _add_kb(tmp_db, "healthy", file_count=3)
    _add_kb(tmp_db, "ghost", file_count=-1)

    names = {kb.kb_name for kb in list_kbs_from_db()}
    # 幽灵库必须可见 —— 否则用户看不到、删不掉、又建不了同名
    assert names == {"healthy", "ghost"}


def test_list_kbs_clamps_negative_and_null_count(tmp_db):
    """对外暴露的 file_count 恒 >= 0(负数 / NULL 都钳成 0)。"""
    from chayuan.server.db.repository.knowledge_base_repository import (
        list_kbs_from_db,
    )

    _add_kb(tmp_db, "neg", file_count=-5)
    _add_kb(tmp_db, "nul", file_count=None)
    _add_kb(tmp_db, "ok", file_count=7)

    by_name = {kb.kb_name: kb.file_count for kb in list_kbs_from_db()}
    assert by_name == {"neg": 0, "nul": 0, "ok": 7}
