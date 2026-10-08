"""Postgres schema (SQLAlchemy 2.0). Every change is an Alembic migration in `migrations/`."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Build(Base):
    __tablename__ = "builds"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    apk_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(Text)
    locator_tag: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    source: Mapped[str] = mapped_column(String(16), server_default="cli")
    patch_notes: Mapped[str] = mapped_column(Text, server_default="")
    apk: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # pg_core.builds.ApkManifest

    __table_args__ = (CheckConstraint("source IN ('cli', 'webhook')", name="builds_source"),)


class Spec(Base):
    __tablename__ = "specs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    spec_id: Mapped[str] = mapped_column(String(32), unique=True)
    feature: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    file_sha: Mapped[str] = mapped_column(String(64))


class Bug(Base):
    __tablename__ = "bugs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    flag: Mapped[str] = mapped_column(String(64), unique=True)
    category: Mapped[str] = mapped_column(String(64))
    feature: Mapped[str] = mapped_column(String(64))
    split: Mapped[str] = mapped_column(String(16))
    pages: Mapped[list[str]] = mapped_column(ARRAY(Text))

    __table_args__ = (CheckConstraint("split IN ('dev', 'holdout')", name="bugs_split"),)


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("builds.id"))
    feature: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    temperature: Mapped[float | None]
    seed: Mapped[int | None] = mapped_column(BigInteger)
    seed_supported: Mapped[bool | None]
    n_requested: Mapped[int] = mapped_column(Integer)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")
    error: Mapped[str | None] = mapped_column(Text)
    validation_id: Mapped[int | None] = mapped_column(ForeignKey("validations.id"))
    job_key: Mapped[str | None] = mapped_column(String(200), unique=True)  # the GENERATE job


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    generation_run_id: Mapped[int] = mapped_column(ForeignKey("generation_runs.id"))
    name: Mapped[str] = mapped_column(String(200))
    intent: Mapped[str] = mapped_column(Text, default="")
    code: Mapped[str] = mapped_column(Text)
    code_sha: Mapped[str] = mapped_column(String(64))
    spec_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    pages_used: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    decision: Mapped[str] = mapped_column(String(16), default="pending")
    trust_score: Mapped[int | None]
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    gates: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    human_decision: Mapped[str | None] = mapped_column(String(16))
    human_reason: Mapped[str | None] = mapped_column(Text)
    human_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idx: Mapped[int | None] = mapped_column(Integer)  # position in the model's answer
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("generation_run_id", "code_sha", name="candidates_run_code"),
        UniqueConstraint("generation_run_id", "idx", name="candidates_run_idx"),
        CheckConstraint(
            "decision IN ('pending', 'accept', 'review', 'reject')", name="candidates_decision"
        ),
    )


class SuiteTest(Base):
    __tablename__ = "suite_tests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    origin: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(String(200))
    code_sha: Mapped[str] = mapped_column(String(64))
    spec_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    accepted_from_candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint("origin IN ('human', 'generated', 'repaired')", name="suite_tests_origin"),
        CheckConstraint("status IN ('active', 'quarantined')", name="suite_tests_status"),
    )


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"))
    suite_test_id: Mapped[int | None] = mapped_column(ForeignKey("suite_tests.id"))
    build_id: Mapped[int] = mapped_column(ForeignKey("builds.id"))
    run_group: Mapped[str] = mapped_column(String(64))  # the prove/baseline run it belongs to
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    purpose: Mapped[str] = mapped_column(String(16))
    repeat: Mapped[int] = mapped_column(Integer)  # 1..3 clean, 1..2 per bug
    attempt: Mapped[int] = mapped_column(Integer)  # infra retries of that repeat
    outcome: Mapped[str] = mapped_column(String(16))
    failure_kind: Mapped[str | None] = mapped_column(String(32))  # the classification rule
    detail: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    wall_ms: Mapped[int] = mapped_column(Integer)
    artifacts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))  # the RUN_TEST job

    __table_args__ = (
        CheckConstraint("purpose IN ('clean', 'bug', 'benchmark')", name="executions_purpose"),
        CheckConstraint(
            "(candidate_id IS NULL) <> (suite_test_id IS NULL)", name="executions_one_subject"
        ),
        UniqueConstraint(
            "run_group",
            "candidate_id",
            "suite_test_id",
            "flags",
            "repeat",
            "attempt",
            name="executions_idempotent",
            postgresql_nulls_not_distinct=True,
        ),
    )


class Kill(Base):
    __tablename__ = "kills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_group: Mapped[str] = mapped_column(String(64))
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"))
    suite_test_id: Mapped[int | None] = mapped_column(ForeignKey("suite_tests.id"))
    bug_id: Mapped[int] = mapped_column(ForeignKey("bugs.id"))
    killed: Mapped[bool]
    unstable: Mapped[bool]
    evidence_execution_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default=list)

    __table_args__ = (
        CheckConstraint(
            "(candidate_id IS NULL) <> (suite_test_id IS NULL)", name="kills_one_subject"
        ),
        UniqueConstraint(
            "run_group",
            "candidate_id",
            "suite_test_id",
            "bug_id",
            name="kills_idempotent",
            postgresql_nulls_not_distinct=True,
        ),
    )


class Validation(Base):
    """One build validation: the job DAG for some features of one build (ADR-0006)."""

    __tablename__ = "validations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("builds.id"))
    features: Mapped[list[str]] = mapped_column(ARRAY(Text))
    n: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int | None] = mapped_column(BigInteger)
    manifest_sha: Mapped[str] = mapped_column(String(64))  # the SDK manifest it was planned with
    run_timeout_s: Mapped[float] = mapped_column(Double)
    requested_by: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    problems: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="validations_status"),
    )


class Job(Base):
    """A unit of work for the worker or the device agent, leased with SKIP LOCKED (ADR-0006)."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    requires: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    validation_id: Mapped[int | None] = mapped_column(ForeignKey("validations.id"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_sha: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "type IN ('INSTALL_BUILD', 'RUN_TEST', 'GENERATE', 'SCORE', 'CRAWL')", name="jobs_type"
        ),
        CheckConstraint(
            "status IN ('queued', 'leased', 'succeeded', 'failed', 'dead')", name="jobs_status"
        ),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="jobs_attempts"),
        Index(
            "jobs_claimable",
            "priority",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("jobs_leases", "lease_expires_at", postgresql_where=text("status = 'leased'")),
        Index("jobs_validation", "validation_id"),
    )


class Agent(Base):
    """A device agent (ADR-0005). Its bearer tokens live in `api_tokens`, hashed."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    health: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # last self-check
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiToken(Base):
    """A bearer token, stored only as its sha256. `agent` tokens reach agent endpoints only;
    `admin` tokens (the CLI) reach admin endpoints."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("kind IN ('agent', 'admin')", name="api_tokens_kind"),
        CheckConstraint("(kind = 'agent') = (agent_id IS NOT NULL)", name="api_tokens_agent"),
    )


class EnrolmentToken(Base):
    """A one-time token that lets `pg agent enroll` register one agent before it expires."""

    __tablename__ = "enrolment_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))


class WebhookDelivery(Base):
    """Each accepted webhook delivery once: a replay of the same delivery id changes nothing."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_id: Mapped[str] = mapped_column(String(64), unique=True)
    body_sha256: Mapped[str] = mapped_column(String(64))
    build_id: Mapped[int | None] = mapped_column(ForeignKey("builds.id"))
    received_at: Mapped[datetime] = _now()


class LlmCall(Base):
    """One model call (or a final failure after retries), for tracing and budgets. Holds no
    prompt text and no secrets: the prompt is identified by version and hash."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    generation_run_id: Mapped[int | None] = mapped_column(ForeignKey("generation_runs.id"))
    build_id: Mapped[int | None] = mapped_column(ForeignKey("builds.id"))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    prompt_hash: Mapped[str] = mapped_column(String(64))
    call_index: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    finish_reason: Mapped[str | None] = mapped_column(String(64))
    returned_tests: Mapped[int] = mapped_column(Integer, default=0)
    candidate_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('ok', 'error')", name="llm_calls_status"),
        Index("llm_calls_created", "created_at"),
        Index("llm_calls_build", "build_id"),
    )


class Worker(Base):
    """A pg_worker process: its last loop and whether generation is switched on (for the
    dashboard's System page)."""

    __tablename__ = "workers"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generation_enabled: Mapped[bool]
    info: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
