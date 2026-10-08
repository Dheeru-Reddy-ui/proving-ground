"""Persistence against a real Postgres: migrations from empty, idempotent writes, constraints.

Uses a throwaway database from `conftest.py`, so the real data is untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from pg_core.catalog import parse_catalog
from pg_db import repo
from pg_db.session import session_scope

ROOT = Path(__file__).resolve().parents[2]


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
        "validations",
        "jobs",
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
