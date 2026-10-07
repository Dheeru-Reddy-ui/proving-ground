"""Persistence against a real Postgres: migrations from empty, idempotent writes, constraints.

Uses DATABASE_URL's server with a separate throwaway database, so the real data is untouched.
Skipped when DATABASE_URL is not set.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from pg_cli.settings import Settings
from pg_core.catalog import parse_catalog
from pg_db import repo
from pg_db.session import make_engine, session_scope, sqlalchemy_url

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


def test_migrations_create_every_table(engine: Engine) -> None:
    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            ).scalars()
        )
    assert {
        "builds",
        "specs",
        "bugs",
        "generation_runs",
        "candidates",
        "executions",
        "kills",
        "suite_tests",
    } <= tables


def test_build_registration_is_idempotent(engine: Engine) -> None:
    with session_scope(engine) as s:
        first, created = repo.register_build(s, "a" * 64, "first", "aaaaaaaaaaaa")
        again, created_again = repo.register_build(s, "a" * 64, "renamed", "aaaaaaaaaaaa")
    assert created
    assert not created_again
    assert first.id == again.id
    assert again.label == "first"


def test_bug_sync_is_an_upsert(engine: Engine) -> None:
    catalog = parse_catalog((ROOT / "benchmark/bugs.yaml").read_text(encoding="utf-8"))
    with session_scope(engine) as s:
        repo.sync_bugs(s, catalog)
        repo.sync_bugs(s, catalog)
        bugs = repo.bugs_by_code(s)
    assert len(bugs) == 16
    assert bugs["SB16"].split == "dev"


def test_candidates_and_executions_are_idempotent(engine: Engine) -> None:
    now = datetime.now(UTC)
    with session_scope(engine) as s:
        build, _ = repo.register_build(s, "b" * 64, "b", "bbbbbbbbbbbb")
        run = repo.create_generation_run(
            s,
            build_id=build.id,
            feature="store",
            provider="fake",
            model="m",
            prompt_version="v1",
            prompt_hash="h",
            n_requested=2,
        )
        cand, created = repo.add_candidate(s, run.id, "t", "intent", "code", ["STORE-1"])
        same, created_again = repo.add_candidate(
            s, run.id, "t2", "other intent", "code", ["STORE-1"]
        )
        assert created
        assert not created_again
        assert cand.id == same.id
        fields = {
            "candidate_id": cand.id,
            "build_id": build.id,
            "run_group": "prove-1",
            "flags": [],
            "purpose": "clean",
            "repeat": 1,
            "attempt": 1,
            "outcome": "passed",
            "failure_kind": "plugin_passed",
            "duration_ms": 5000,
            "wall_ms": 17000,
            "artifacts": {},
            "started_at": now,
            "finished_at": now,
        }
        one = repo.record_execution(s, **fields)
        two = repo.record_execution(s, **fields)
        assert one.id == two.id
        assert len(repo.executions_for(s, "prove-1", candidate_id=cand.id)) == 1


def test_an_execution_needs_exactly_one_subject(engine: Engine) -> None:
    now = datetime.now(UTC)
    with session_scope(engine) as s:
        build, _ = repo.register_build(s, "c" * 64, "c", "cccccccccccc")
    with pytest.raises(IntegrityError), session_scope(engine) as s:
        repo.record_execution(
            s,
            build_id=build.id,
            run_group="prove-x",
            flags=[],
            purpose="clean",
            repeat=1,
            attempt=1,
            outcome="passed",
            duration_ms=1,
            wall_ms=1,
            artifacts={},
            started_at=now,
            finished_at=now,
            suite_test_id=None,
            candidate_id=None,
        )
