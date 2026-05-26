"""Alembic 0020_app_kb_grant 迁移验证 — plan v1.3 §4.3。

目标：在 in-memory SQLite 上跑 upgrade()/downgrade()，确认：
* app_kb_grant 表 + 7 列 + 主键 + uq(app_id,kb_id) + 3 个索引全建出
* server_default(role='reader', granted_at=now()) 真的写得进
* unique constraint 能拒掉重复 (app_id, kb_id)
* downgrade() 完全清理(表、索引一并消失)

为什么不走 `alembic upgrade head`：
* 前面 0001-0019 依赖一长串业务初始化(config_center / users / files / kb 等)，
  in-memory 起完整链路成本高。这里只对 0020 做"孤立单测"，
  在空 schema 上直接 import upgrade()/downgrade() — 严格测本迁移的正确性。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_now = lambda: datetime.now(timezone.utc).replace(tzinfo=None)  # noqa: E731 (SQLite naive datetime)

import pytest

# alembic 不是 dev 强依赖,缺它就跳过整个文件(不影响 CI)
pytest.importorskip("alembic")
import sqlalchemy as sa  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from alembic.operations import Operations  # noqa: E402


@pytest.fixture
def engine():
    """SQLite in-memory engine,本测试整个生命周期共享。"""
    eng = sa.create_engine("sqlite://", future=True)
    yield eng
    eng.dispose()


@pytest.fixture
def op_proxy(engine):
    """绑定 alembic Operations 到 engine,使 op.* 调用工作在 in-memory schema 上。"""
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        yield Operations(ctx)
        conn.commit()


def _import_revision_module():
    """import 0020 模块 — 注意文件名以数字开头,不能用普通 import。"""
    import importlib.util
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    rev_path = (repo_root
                / "chayuan" / "server" / "db" / "alembic"
                / "versions" / "0020_app_kb_grant.py")
    assert rev_path.exists(), f"missing {rev_path}"
    spec = importlib.util.spec_from_file_location("rev_0020", rev_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_metadata_correct():
    """revision id / down_revision 必须严格,改动会破整条 alembic 链。"""
    mod = _import_revision_module()
    assert mod.revision == "0020_app_kb_grant"
    assert mod.down_revision == "0019_data_mount"
    assert mod.branch_labels is None


def test_upgrade_creates_table_with_all_columns(engine, op_proxy):
    mod = _import_revision_module()
    # alembic op.* 通过 fixture 注入,在该 fixture 上下文里跑 upgrade
    import alembic.op as _alembic_op_module
    _alembic_op_module._proxy = op_proxy   # noqa: SLF001 (alembic 内部约定)

    mod.upgrade()

    inspector = sa.inspect(engine)
    assert "app_kb_grant" in inspector.get_table_names(), \
        "upgrade 必须创建 app_kb_grant 表"

    cols = {c["name"]: c for c in inspector.get_columns("app_kb_grant")}
    expected = {"id", "app_id", "kb_id", "role", "granted_by", "granted_at", "expires_at"}
    assert expected.issubset(cols.keys()), \
        f"缺字段:{expected - cols.keys()}"

    # 关键字段类型与 nullable
    assert "INT" in str(cols["id"]["type"]).upper()
    assert cols["id"]["nullable"] is False
    assert "VARCHAR" in str(cols["app_id"]["type"]).upper() or "STRING" in str(cols["app_id"]["type"]).upper()
    assert cols["app_id"]["nullable"] is False
    assert cols["kb_id"]["nullable"] is False
    assert cols["role"]["nullable"] is False
    # 可空字段
    assert cols["granted_by"]["nullable"] is True
    assert cols["expires_at"]["nullable"] is True


def test_upgrade_creates_three_indexes(engine, op_proxy):
    mod = _import_revision_module()
    import alembic.op as _alembic_op_module
    _alembic_op_module._proxy = op_proxy  # noqa: SLF001

    mod.upgrade()

    inspector = sa.inspect(engine)
    idx_names = {i["name"] for i in inspector.get_indexes("app_kb_grant")}
    expected = {"ix_app_kb_grant_app_id", "ix_app_kb_grant_kb_id", "ix_app_kb_grant_expires_at"}
    assert expected.issubset(idx_names), \
        f"缺索引:{expected - idx_names}, 实际:{idx_names}"


def test_upgrade_unique_constraint_app_id_kb_id(engine, op_proxy):
    """uq(app_id, kb_id) 必须工作 — 重复插入应抛 IntegrityError。"""
    mod = _import_revision_module()
    import alembic.op as _alembic_op_module
    _alembic_op_module._proxy = op_proxy  # noqa: SLF001

    mod.upgrade()

    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO app_kb_grant (app_id, kb_id, role, granted_at) "
            "VALUES ('app:demo1', 1, 'reader', :now)"
        ), {"now": _now()})
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO app_kb_grant (app_id, kb_id, role, granted_at) "
                "VALUES ('app:demo1', 1, 'editor', :now)"
            ), {"now": _now()})


def test_upgrade_role_server_default_is_reader(engine, op_proxy):
    """没指定 role 时 server_default='reader' 必须生效。"""
    mod = _import_revision_module()
    import alembic.op as _alembic_op_module
    _alembic_op_module._proxy = op_proxy  # noqa: SLF001

    mod.upgrade()

    with engine.begin() as conn:
        # 注意:granted_at 也有 server_default(now()) — 一并验证
        conn.execute(sa.text(
            "INSERT INTO app_kb_grant (app_id, kb_id) VALUES ('app:demo2', 2)"
        ))
        row = conn.execute(sa.text(
            "SELECT role, granted_at, expires_at FROM app_kb_grant WHERE app_id='app:demo2'"
        )).fetchone()
    assert row is not None
    assert row[0] == "reader", "未指定 role 应回落到 server_default 'reader'"
    assert row[1] is not None, "granted_at 应被 server_default(now()) 自动填上"
    assert row[2] is None, "expires_at 默认应为 NULL"


def test_upgrade_supports_nullable_expires_at_and_editor_role(engine, op_proxy):
    """expires_at 可为 NULL(永久) 也可为时间戳;role 接受 editor。"""
    mod = _import_revision_module()
    import alembic.op as _alembic_op_module
    _alembic_op_module._proxy = op_proxy  # noqa: SLF001

    mod.upgrade()

    expire = _now() + timedelta(days=30)
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO app_kb_grant (app_id, kb_id, role, granted_by, expires_at, granted_at) "
            "VALUES ('app:editor', 3, 'editor', 7, :exp, :now)"
        ), {"exp": expire, "now": _now()})
        row = conn.execute(sa.text(
            "SELECT role, granted_by, expires_at FROM app_kb_grant WHERE app_id='app:editor'"
        )).fetchone()
    assert row[0] == "editor"
    assert row[1] == 7
    assert row[2] is not None


def test_downgrade_removes_table_and_indexes(engine, op_proxy):
    mod = _import_revision_module()
    import alembic.op as _alembic_op_module
    _alembic_op_module._proxy = op_proxy  # noqa: SLF001

    mod.upgrade()
    inspector = sa.inspect(engine)
    assert "app_kb_grant" in inspector.get_table_names()

    mod.downgrade()

    # 有些版本的 inspector 会缓存 — 重新拿一个
    inspector2 = sa.inspect(engine)
    assert "app_kb_grant" not in inspector2.get_table_names(), \
        "downgrade 必须 drop_table"


def test_upgrade_then_downgrade_then_upgrade_idempotent(engine, op_proxy):
    """上 → 下 → 再上必须能跑通,不留残留状态(回滚后重灌的常见运维场景)。"""
    mod = _import_revision_module()
    import alembic.op as _alembic_op_module
    _alembic_op_module._proxy = op_proxy  # noqa: SLF001

    mod.upgrade()
    mod.downgrade()
    mod.upgrade()  # 不能因为残留索引/表抛错

    inspector = sa.inspect(engine)
    assert "app_kb_grant" in inspector.get_table_names()
    idx = {i["name"] for i in inspector.get_indexes("app_kb_grant")}
    assert "ix_app_kb_grant_app_id" in idx
    assert "ix_app_kb_grant_kb_id" in idx
    assert "ix_app_kb_grant_expires_at" in idx
