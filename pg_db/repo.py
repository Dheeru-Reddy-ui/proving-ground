"""Reads and writes used by the CLI. Writes are idempotent: repeating one with the same key
returns the existing row instead of adding a second (CLAUDE.md reliability rule)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pg_core.catalog import Catalog
from pg_db.models import Bug, Build, Candidate, Execution, GenerationRun, Kill, Spec, SuiteTest


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- builds, specs, bugs ------------------------------------------------------------------


def register_build(
    session: Session, apk_sha256: str, label: str, locator_tag: str
) -> tuple[Build, bool]:
    """The build with this APK hash; created (True) or already registered (False)."""
    statement = (
        insert(Build)
        .values(apk_sha256=apk_sha256, label=label, locator_tag=locator_tag)
        .on_conflict_do_nothing(index_elements=["apk_sha256"])
        .returning(Build.id)
    )
    created = session.execute(statement).scalar_one_or_none() is not None
    build = session.execute(select(Build).where(Build.apk_sha256 == apk_sha256)).scalar_one()
    return build, created


def build_by_sha(session: Session, apk_sha256: str) -> Build | None:
    return session.execute(select(Build).where(Build.apk_sha256 == apk_sha256)).scalar_one_or_none()


def latest_build(session: Session) -> Build | None:
    return session.execute(select(Build).order_by(Build.id.desc()).limit(1)).scalar_one_or_none()


def sync_specs(session: Session, specs: Iterable[tuple[str, str, str, str]]) -> int:
    """Upsert (spec_id, feature, text, file_sha) rows; returns how many were written."""
    count = 0
    for spec_id, feature, text, file_sha in specs:
        statement = insert(Spec).values(
            spec_id=spec_id, feature=feature, text=text, file_sha=file_sha
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=["spec_id"],
                set_={"feature": feature, "text": text, "file_sha": file_sha},
            )
        )
        count += 1
    return count


def sync_bugs(session: Session, catalog: Catalog) -> int:
    for bug in catalog.bugs:
        values = {
            "code": bug.id,
            "flag": bug.flag,
            "category": bug.category,
            "feature": bug.feature,
            "split": bug.split.value,
            "pages": list(bug.pages),
        }
        session.execute(
            insert(Bug).values(**values).on_conflict_do_update(index_elements=["code"], set_=values)
        )
    return len(catalog.bugs)


def bugs_by_code(session: Session) -> dict[str, Bug]:
    return {b.code: b for b in session.execute(select(Bug)).scalars()}


# --- generation ---------------------------------------------------------------------------


def create_generation_run(session: Session, **fields: Any) -> GenerationRun:
    run = GenerationRun(**fields)
    session.add(run)
    session.flush()
    return run


def finish_generation_run(
    session: Session,
    run_id: int,
    *,
    status: str,
    finished_at: datetime,
    tokens_in: int,
    tokens_out: int,
    cost_usd: Decimal,
    latency_ms: int,
    llm_calls: list[dict[str, Any]],
    error: str | None = None,
) -> None:
    run = session.get_one(GenerationRun, run_id)
    run.status = status
    run.finished_at = finished_at
    run.tokens_in = tokens_in
    run.tokens_out = tokens_out
    run.cost_usd = cost_usd
    run.latency_ms = latency_ms
    run.llm_calls = llm_calls
    run.error = error


def add_candidate(
    session: Session,
    generation_run_id: int,
    name: str,
    intent: str,
    code: str,
    spec_ids: Sequence[str],
) -> tuple[Candidate, bool]:
    code_sha = sha256_text(code)
    statement = (
        insert(Candidate)
        .values(
            generation_run_id=generation_run_id,
            name=name,
            intent=intent,
            code=code,
            code_sha=code_sha,
            spec_ids=list(spec_ids),
            pages_used=[],
            decision="pending",
            reasons=[],
            gates={},
        )
        .on_conflict_do_nothing(constraint="candidates_run_code")
        .returning(Candidate.id)
    )
    created = session.execute(statement).scalar_one_or_none() is not None
    candidate = session.execute(
        select(Candidate).where(
            Candidate.generation_run_id == generation_run_id, Candidate.code_sha == code_sha
        )
    ).scalar_one()
    return candidate, created


def candidates_of_run(session: Session, generation_run_id: int) -> list[Candidate]:
    return list(
        session.execute(
            select(Candidate)
            .where(Candidate.generation_run_id == generation_run_id)
            .order_by(Candidate.id)
        ).scalars()
    )


# --- executions and kills -----------------------------------------------------------------


def record_execution(session: Session, **fields: Any) -> Execution:
    """Insert one attempt; the same (run_group, subject, flags, repeat, attempt) is stored once."""
    statement = (
        insert(Execution)
        .values(**fields)
        .on_conflict_do_nothing(constraint="executions_idempotent")
        .returning(Execution.id)
    )
    session.execute(statement)
    query = select(Execution).where(
        Execution.run_group == fields["run_group"],
        Execution.flags == list(fields.get("flags", [])),
        Execution.repeat == fields["repeat"],
        Execution.attempt == fields["attempt"],
    )
    if fields.get("candidate_id") is not None:
        query = query.where(Execution.candidate_id == fields["candidate_id"])
    else:
        query = query.where(Execution.suite_test_id == fields["suite_test_id"])
    return session.execute(query).scalar_one()


def executions_for(
    session: Session,
    run_group: str,
    *,
    candidate_id: int | None = None,
    suite_test_id: int | None = None,
) -> list[Execution]:
    query = select(Execution).where(Execution.run_group == run_group).order_by(Execution.id)
    if candidate_id is not None:
        query = query.where(Execution.candidate_id == candidate_id)
    if suite_test_id is not None:
        query = query.where(Execution.suite_test_id == suite_test_id)
    return list(session.execute(query).scalars())


def record_kill(session: Session, **fields: Any) -> None:
    values = dict(fields)
    session.execute(
        insert(Kill)
        .values(**values)
        .on_conflict_do_update(
            constraint="kills_idempotent",
            set_={
                "killed": values["killed"],
                "unstable": values["unstable"],
                "evidence_execution_ids": values["evidence_execution_ids"],
            },
        )
    )


def kills_for(session: Session, run_group: str) -> list[Kill]:
    return list(session.execute(select(Kill).where(Kill.run_group == run_group)).scalars())


# --- suite --------------------------------------------------------------------------------


def upsert_suite_test(
    session: Session,
    *,
    origin: str,
    path: str,
    name: str,
    code: str,
    spec_ids: Sequence[str],
    accepted_from_candidate_id: int | None = None,
) -> SuiteTest:
    values: dict[str, Any] = {
        "origin": origin,
        "path": path,
        "name": name,
        "code_sha": sha256_text(code),
        "spec_ids": list(spec_ids),
        "accepted_from_candidate_id": accepted_from_candidate_id,
        "status": "active",
    }
    session.execute(
        insert(SuiteTest)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["path"],
            set_={k: v for k, v in values.items() if k != "path"},
        )
    )
    return session.execute(select(SuiteTest).where(SuiteTest.path == path)).scalar_one()


def active_suite_tests(session: Session) -> list[SuiteTest]:
    return list(
        session.execute(
            select(SuiteTest).where(SuiteTest.status == "active").order_by(SuiteTest.id)
        ).scalars()
    )
