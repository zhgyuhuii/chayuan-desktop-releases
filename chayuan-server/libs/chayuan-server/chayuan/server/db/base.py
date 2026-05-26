"""SQLAlchemy engine / session 初始化。

按 `basic_settings.yaml` 中的 `SQLALCHEMY_DATABASE_URI` 建立全局 engine；
同时按 `DB_POOL_*` 字段配置连接池，满足多 worker 生产场景。

SQLite 特殊处理：
- SQLite 默认 `NullPool`，而且多线程需要 `check_same_thread=False`；
- 5000 并发下 SQLite 会锁表，这里只做 best-effort 保证单机 dev 能跑通，
  并在启动时 stderr 输出告警；真实上线请按 docs/scalability.md 切 Postgres。
"""
from __future__ import annotations

import json
import sys

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import DeclarativeMeta, declarative_base
from sqlalchemy.orm import sessionmaker

from chayuan.settings import Settings


def _create_sqlite_engine(uri: str, bs):
    """SQLite engine — 跨线程安全 + 不走连接池(避免 writer 锁/超时)。"""
    common = dict(json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False))
    if getattr(bs, "DEPLOYMENT_MODE", "dev") == "prod":
        sys.stderr.write(
            "[chayuan][db] ⚠️  DEPLOYMENT_MODE=prod 但 SQLALCHEMY_DATABASE_URI 仍为 sqlite:"
            "——SQLite 单写者锁在高并发下会导致请求失败。"
            "请参考 docs/scalability.md 切 PostgreSQL。\n"
        )
    else:
        sys.stderr.write(
            "[chayuan][db] 使用 SQLite(单机/桌面默认)。上生产前请切 Postgres,详见 docs/scalability.md。\n"
        )
    sys.stderr.flush()
    return create_engine(
        uri,
        connect_args={"check_same_thread": False},
        pool_pre_ping=bool(getattr(bs, "DB_POOL_PRE_PING", True)),
        **common,
    )


def _create_pooled_engine(uri: str, bs):
    """非 SQLite engine — 按 DB_POOL_* 配建池(PG/MySQL/...)。"""
    common = dict(json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False))
    pool_size = int(getattr(bs, "DB_POOL_SIZE", 10) or 10)
    max_overflow = int(getattr(bs, "DB_MAX_OVERFLOW", 20) or 20)
    pool_recycle = int(getattr(bs, "DB_POOL_RECYCLE", 3600) or 3600)
    pool_timeout = int(getattr(bs, "DB_POOL_TIMEOUT", 30) or 30)
    pool_pre_ping = bool(getattr(bs, "DB_POOL_PRE_PING", True))
    return create_engine(
        uri,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
        pool_timeout=pool_timeout,
        pool_pre_ping=pool_pre_ping,
        **common,
    )


def _build_engine():
    """构造全局 engine。

    桌面单机版只 bundle sqlite3 驱动;若 ``SQLALCHEMY_DATABASE_URI`` 指向
    PG/MySQL 但运行时缺对应驱动(``psycopg2`` / ``pymysql`` 等),会在 import
    阶段抛 ``ModuleNotFoundError``,把整条 ``db/models/__init__.py`` 副作用
    导入链条拉爆,后端 sidecar 进程根本起不来。

    单机产品默认应该 sqlite,这里加一道 fallback:
      - 用户选了 sqlite → 正常
      - 用户填了 PG/MySQL URI 但驱动可用 → 正常
      - 用户填了 PG/MySQL URI 但驱动缺失 → stderr 大声告警 + 回退到默认 sqlite,
        让单机用户至少能起服务,自己再去配置面板修 URI

    生产部署有真 PG 的场景下 psycopg2 必然装好,不会触发 fallback。
    """
    bs = Settings.basic_settings
    uri = bs.SQLALCHEMY_DATABASE_URI

    if uri.startswith("sqlite:"):
        return _create_sqlite_engine(uri, bs)

    try:
        return _create_pooled_engine(uri, bs)
    except ModuleNotFoundError as e:
        # 桌面 sidecar 没打包 PG/MySQL 驱动;回退到默认 sqlite。
        from chayuan.settings import _default_sqlalchemy_uri
        fallback_uri = _default_sqlalchemy_uri()
        sys.stderr.write(
            f"[chayuan][db] ⚠️  SQLALCHEMY_DATABASE_URI={uri!r} 加载失败:{e};"
            f"该驱动未安装(单机版默认仅 bundle sqlite3),回退到 {fallback_uri!r}。"
            f"如需 PG/MySQL,请安装对应驱动包(psycopg2 / pymysql 等)后重启。\n"
        )
        sys.stderr.flush()
        # 同步把 URI 写回 settings 内存里,后续路径(create_tables / ensure_database_from_uri)读到的也是 sqlite,
        # 不然 startup 用的是新 engine、其它代码用 Settings 直读 URI 会两边不一致。
        try:
            bs.SQLALCHEMY_DATABASE_URI = fallback_uri  # type: ignore[misc]
        except Exception:  # noqa: BLE001
            pass
        return _create_sqlite_engine(fallback_uri, bs)


engine = _build_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base: DeclarativeMeta = declarative_base()


# ---------------------------------------------------------------------------
# Async engine（T7）：懒建；未装 asyncpg/asyncmy/aiosqlite 时返回 None
# ---------------------------------------------------------------------------

_async_engine = None
_async_session_maker = None
_async_tried = False


def _infer_async_uri(sync_uri: str) -> str:
    """sync URI → async URI 简单映射；用户已显式设置 ASYNC_DATABASE_URI 时走那个。"""
    if not sync_uri:
        return ""
    # 常见前缀替换
    mapping = [
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql+psycopg://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("postgres://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+asyncmy://"),
        ("mysql://", "mysql+asyncmy://"),
        ("sqlite:///", "sqlite+aiosqlite:///"),
        ("sqlite:", "sqlite+aiosqlite:"),
    ]
    for a, b in mapping:
        if sync_uri.startswith(a):
            return b + sync_uri[len(a):]
    return sync_uri  # 已经是 async 前缀


def get_async_engine():
    """首次调用时创建 async engine；失败或驱动缺失返回 None（调用方应回退 sync）。"""
    global _async_engine, _async_session_maker, _async_tried
    if _async_engine is not None:
        return _async_engine
    if _async_tried:
        return None
    _async_tried = True
    try:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker as _sm
        bs = Settings.basic_settings
        uri = (getattr(bs, "ASYNC_DATABASE_URI", "") or "").strip() \
              or _infer_async_uri(bs.SQLALCHEMY_DATABASE_URI or "")
        if not uri:
            return None
        pool_size = int(getattr(bs, "DB_POOL_SIZE", 10) or 10)
        max_overflow = int(getattr(bs, "DB_MAX_OVERFLOW", 20) or 20)
        pool_recycle = int(getattr(bs, "DB_POOL_RECYCLE", 3600) or 3600)
        pool_pre_ping = bool(getattr(bs, "DB_POOL_PRE_PING", True))
        kwargs = {}
        if uri.startswith("sqlite+aiosqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update({
                "pool_size": pool_size, "max_overflow": max_overflow,
                "pool_recycle": pool_recycle, "pool_pre_ping": pool_pre_ping,
            })
        _async_engine = create_async_engine(
            uri, json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False),
            **kwargs,
        )
        _async_session_maker = _sm(
            _async_engine, class_=AsyncSession, expire_on_commit=False,
        )
        return _async_engine
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[chayuan][db] async engine 初始化失败（回退 sync）：{e!r}\n")
        _async_engine = None
        _async_session_maker = None
        return None


def get_async_session_maker():
    """返回 async sessionmaker 或 None。"""
    if _async_session_maker is not None:
        return _async_session_maker
    if get_async_engine() is None:
        return None
    return _async_session_maker
