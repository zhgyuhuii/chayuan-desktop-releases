from contextlib import asynccontextmanager, contextmanager
from functools import wraps

from sqlalchemy.orm import Session

from chayuan.server.db.base import SessionLocal, get_async_session_maker


@contextmanager
def session_scope() -> Session:
    """上下文管理器用于自动获取 Session, 避免错误"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


def with_session(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        with session_scope() as session:
            try:
                result = f(session, *args, **kwargs)
                session.commit()
                return result
            except:
                session.rollback()
                raise

    return wrapper


def get_db() -> SessionLocal:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db0() -> SessionLocal:
    db = SessionLocal()
    return db


# ---------------------------------------------------------------------------
# Async session（T7）：asyncpg / asyncmy / aiosqlite 下可用；其它情况返回 None
# ---------------------------------------------------------------------------


@asynccontextmanager
async def async_session_scope():
    """Async session 上下文管理器；未配置 async 引擎时 yield None。

    调用方模式：
        async with async_session_scope() as s:
            if s is None:
                # 回退 sync
                with session_scope() as ss:
                    ...
                return
            rows = (await s.execute(text("SELECT 1"))).all()
    """
    maker = get_async_session_maker()
    if maker is None:
        yield None
        return
    s = maker()
    try:
        yield s
        try:
            await s.commit()
        except Exception:
            await s.rollback()
            raise
    finally:
        await s.close()


def with_async_session(f):
    """等价于 ``@with_session`` 的 async 版本；async engine 不可用时传入 None。"""
    @wraps(f)
    async def wrapper(*args, **kwargs):
        async with async_session_scope() as session:
            return await f(session, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Tenant-scoped session（T9-b）：在 session begin 后立刻 SET LOCAL app.tenant_id
# ---------------------------------------------------------------------------


@asynccontextmanager
async def async_tenant_session_scope():
    """带 tenant 标记的 async session。

    行为：
    - 从 ``shared.tenant_context.current_tenant_id()`` 读 tenant；空串时退化为普通 async session
    - Postgres 后端：发 ``SET LOCAL app.tenant_id = :tid``，配合 0003_enable_rls 的 RLS 策略
    - 其它后端：no-op 附加指令；行为与 ``async_session_scope`` 一致
    - ``RLS_ENABLED=False`` 时整体退化为普通 async session（不碰 tenant）
    """
    maker = get_async_session_maker()
    if maker is None:
        yield None
        return

    try:
        from chayuan.settings import Settings
        rls_on = bool(getattr(Settings.basic_settings, "RLS_ENABLED", False))
    except Exception:  # noqa: BLE001
        rls_on = False

    tenant = ""
    if rls_on:
        try:
            from chayuan.server.shared.tenant_context import current_tenant_id
            tenant = str(current_tenant_id() or "")
        except Exception:  # noqa: BLE001
            tenant = ""

    s = maker()
    try:
        if tenant:
            backend = ""
            try:
                backend = s.get_bind().dialect.name  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                backend = ""
            if backend == "postgresql":
                try:
                    from sqlalchemy import text
                    safe = tenant.replace("'", "''")
                    await s.execute(text(f"SET LOCAL app.tenant_id = '{safe}'"))
                except Exception:  # noqa: BLE001
                    pass
        yield s
        try:
            await s.commit()
        except Exception:
            await s.rollback()
            raise
    finally:
        await s.close()
