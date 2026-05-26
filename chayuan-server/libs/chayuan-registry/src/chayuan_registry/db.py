"""Database engine + session bootstrap.

Default URL: sqlite:///<CHAYUAN_HOME>/data/registry.sqlite
Override with config.registry.url or CHAYUAN_REGISTRY_URL.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from chayuan_core import ensure_dirs, get_paths, load_config
from chayuan_registry.models import Base

_LOCK = threading.RLock()
_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None


def _default_url() -> str:
    if env := os.environ.get("CHAYUAN_REGISTRY_URL"):
        return env
    p = ensure_dirs().data / "registry.sqlite"
    return f"sqlite:///{p.as_posix()}"


def create_engine_from_url(url: str | None = None, echo: bool = False) -> Engine:
    u = url or _default_url()
    if u.startswith("sqlite"):
        kw: dict = {
            "future": True,
            "echo": echo,
            "connect_args": {"check_same_thread": False, "timeout": 30},
        }
        # In-memory databases are connection-scoped; force a single shared
        # connection so all sessions/threads see the same schema + data.
        if ":memory:" in u or "mode=memory" in u:
            kw["poolclass"] = StaticPool
        return create_engine(u, **kw)
    return create_engine(u, future=True, echo=echo, pool_pre_ping=True)


def init_engine(url: str | None = None, echo: bool = False) -> Engine:
    """Idempotent engine bootstrap; (re)creates schema if missing."""
    global _ENGINE, _SESSION_FACTORY
    with _LOCK:
        if _ENGINE is None:
            try:
                cfg = load_config()
                u = url or cfg.registry.url or _default_url()
                e = echo or cfg.registry.echo
            except Exception:
                u = url or _default_url()
                e = echo
            ensure_dirs()
            _ENGINE = create_engine_from_url(u, echo=e)
            Base.metadata.create_all(_ENGINE)
            _SESSION_FACTORY = sessionmaker(bind=_ENGINE, autoflush=False, expire_on_commit=False)
    return _ENGINE


def get_session() -> Session:
    if _SESSION_FACTORY is None:
        init_engine()
    assert _SESSION_FACTORY is not None
    return _SESSION_FACTORY()


@contextmanager
def session_scope() -> Iterator[Session]:
    s = get_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def reset_for_tests(url: str = "sqlite:///:memory:") -> Engine:
    """Test helper: tear down + reinit."""
    global _ENGINE, _SESSION_FACTORY
    with _LOCK:
        if _ENGINE is not None:
            _ENGINE.dispose()
        _ENGINE = create_engine_from_url(url)
        Base.metadata.create_all(_ENGINE)
        _SESSION_FACTORY = sessionmaker(bind=_ENGINE, autoflush=False, expire_on_commit=False)
    # silence unused-binding warnings
    _ = get_paths()
    return _ENGINE
