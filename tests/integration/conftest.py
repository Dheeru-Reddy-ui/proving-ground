"""Integration fixtures: a throwaway database on DATABASE_URL's server, migrated from empty.

Real data is never touched. Every test module gets its own database; tests are skipped when
DATABASE_URL is not set.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

from pg_cli.settings import Settings
from pg_db.session import make_engine, sqlalchemy_url

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    url = Settings().database_url
    if url is None:
        pytest.skip("DATABASE_URL is not set")
    base = url.get_secret_value()
    name = f"pg_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(sqlalchemy_url(base), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    test_url = base.rsplit("/", 1)[0] + "/" + name
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url
    try:
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        test_engine = make_engine(test_url)
        yield test_engine
        test_engine.dispose()
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
