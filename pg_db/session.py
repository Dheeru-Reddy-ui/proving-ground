"""Engine and sessions from `DATABASE_URL` (never logged)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

CONNECT_TIMEOUT_S = 10


def sqlalchemy_url(database_url: str) -> str:
    """Use the psycopg 3 driver whatever scheme the URL was written with."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    raise ValueError("DATABASE_URL must be a postgresql:// URL")


def make_engine(database_url: str) -> Engine:
    return create_engine(
        sqlalchemy_url(database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
    )


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """One transaction: committed on success, rolled back on any error."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
