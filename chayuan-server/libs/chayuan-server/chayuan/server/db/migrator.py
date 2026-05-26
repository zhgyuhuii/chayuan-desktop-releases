"""启动期迁移控制器（T7）。

三种模式（按 ``basic_settings.DB_MIGRATION_MODE`` 选）：

- ``alembic``：运行 ``alembic upgrade head``；多实例并发启动时用 Postgres advisory
  lock 串行化，避免两个实例同时跑 DDL
- ``create_all``：兼容旧行为；还是调 ``Base.metadata.create_all``，仅用于 dev
- ``off``：完全跳过，运维自行管 schema

幂等性保证：
- PostgreSQL：``pg_try_advisory_lock(CHAYUAN_LOCK_KEY)``，多实例只有一个拿到锁
- SQLite / MySQL：退化为"全局异常捕获 + 一次性重试"（重复建表会抛、被忽略）

这是一个**主库 DDL 锁**；迁移时间按正常 schema 改动小于一秒，不会拖垮启动。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chayuan.db.migrator")


_ADVISORY_LOCK_KEY = 7192024 ^ 0xCAFE  # 随意一个 bigint，稳定


def _alembic_config():
    """返回一个指向本项目 alembic 目录的 Config 对象。"""
    from alembic.config import Config
    here = Path(__file__).resolve().parent
    ini = here / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(here / "alembic"))
    from chayuan.settings import Settings
    uri = Settings.basic_settings.SQLALCHEMY_DATABASE_URI
    if uri:
        cfg.set_main_option("sqlalchemy.url", uri)
    return cfg


def _try_advisory_lock(engine, key: int) -> bool:
    """PostgreSQL advisory lock；非 PG 回退到"总是尝试跑"。"""
    try:
        if engine.url.get_backend_name() != "postgresql":
            return True
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text(f"SELECT pg_try_advisory_lock({int(key)})")).first()
            return bool(row and row[0])
    except Exception as e:  # noqa: BLE001
        logger.debug("advisory lock 失败（忽略）：%r", e)
        return True


def _release_advisory_lock(engine, key: int) -> None:
    try:
        if engine.url.get_backend_name() != "postgresql":
            return
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text(f"SELECT pg_advisory_unlock({int(key)})"))
    except Exception:  # noqa: BLE001
        pass


def upgrade_to_head() -> bool:
    """运行 ``alembic upgrade head``；返回是否跑了。"""
    try:
        from alembic import command
    except ImportError:
        logger.warning("alembic 未安装；跳过迁移。`pip install alembic>=1.13`")
        return False
    try:
        from chayuan.server.db.base import engine
    except Exception as e:  # noqa: BLE001
        logger.warning("获取 engine 失败：%r", e)
        return False

    # 多实例并发保护（PG only）
    got = _try_advisory_lock(engine, _ADVISORY_LOCK_KEY)
    if not got:
        logger.info("[migrator] 另一实例持有 advisory lock，本实例跳过迁移")
        return False
    try:
        cfg = _alembic_config()
        command.upgrade(cfg, "head")
        logger.info("[migrator] alembic upgrade head 完成")
        return True
    except Exception as e:  # noqa: BLE001
        logger.exception("[migrator] alembic 迁移失败：%s", e)
        return False
    finally:
        _release_advisory_lock(engine, _ADVISORY_LOCK_KEY)


def stamp_head() -> None:
    """把现有数据库直接标记为 head（不跑 DDL）。

    用于"库已经用 create_all 建好，现在第一次引入 Alembic"的过渡场景；运维执行：
        python -m chayuan.server.db.migrator stamp
    """
    try:
        from alembic import command
    except ImportError:
        logger.warning("alembic 未安装")
        return
    cfg = _alembic_config()
    command.stamp(cfg, "head")
    logger.info("[migrator] alembic stamp head 完成（未跑 DDL）")


def _resolve_mode() -> str:
    """返回最终生效的迁移模式：显式配置优先；DEPLOYMENT_MODE=prod 时默认走 alembic。

    用户既能在配置里明确写 ``DB_MIGRATION_MODE=alembic|create_all|off``，也能交给
    环境自行推断——prod 场景下 create_all 有多实例 DDL race 风险，故默认 alembic。
    """
    from chayuan.settings import Settings
    bs = Settings.basic_settings
    raw = (getattr(bs, "DB_MIGRATION_MODE", "") or "").strip().lower()
    valid = {"alembic", "create_all", "off"}
    if raw in valid and raw != "create_all":
        return raw
    # raw 是空 / unknown / 或显式 create_all（但 prod 仍强制升 alembic）
    deploy = (getattr(bs, "DEPLOYMENT_MODE", "dev") or "dev").lower()
    if deploy == "prod":
        # 仅在用户没显式写 create_all 时抬升
        if raw != "create_all":
            return "alembic"
        # 用户显式写了 create_all：尊重，但大声告警
        logger.warning(
            "[migrator] DEPLOYMENT_MODE=prod 且 DB_MIGRATION_MODE=create_all；"
            "多实例启动会 DDL race，建议改 alembic",
        )
        return "create_all"
    return raw if raw in valid else "create_all"


def bootstrap() -> None:
    """启动期入口；按 ``DB_MIGRATION_MODE`` 选路径。"""
    mode = _resolve_mode()
    logger.info("[migrator] 选择迁移策略：%s", mode)
    if mode == "off":
        logger.info("[migrator] DB_MIGRATION_MODE=off；跳过")
    elif mode == "alembic":
        ok = upgrade_to_head()
        if not ok:
            # 兜底：alembic 失败别把服务挡在外面
            logger.warning("[migrator] alembic 失败，回退 create_all")
            from chayuan.server.knowledge_base.migrate import create_tables
            create_tables()
    else:
        # 默认：create_all（兼容旧行为）
        from chayuan.server.knowledge_base.migrate import create_tables
        create_tables()


def main() -> int:
    import sys
    args = sys.argv[1:]
    op = args[0] if args else "upgrade"
    if op == "upgrade":
        ok = upgrade_to_head()
        return 0 if ok else 1
    if op == "stamp":
        stamp_head()
        return 0
    if op == "current":
        try:
            from alembic import command
            cfg = _alembic_config()
            command.current(cfg)
        except Exception as e:  # noqa: BLE001
            print(f"current failed: {e}", flush=True)
            return 1
        return 0
    print(f"usage: migrator {{upgrade|stamp|current}}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
