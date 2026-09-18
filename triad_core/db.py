"""Database engine and session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from triad_core.config import settings


@lru_cache(maxsize=1)
def engine() -> Engine:
    return create_engine(
        settings().db_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        future=True,
    )


@lru_cache(maxsize=1)
def session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    s = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s


def ping() -> bool:
    with engine().connect() as conn:
        return conn.execute(text("SELECT 1")).scalar_one() == 1
