"""The job queue in SQL (ADR-0006). The rules come from `pg_core.jobs`; this module only reads
and writes rows. Every function runs inside the caller's transaction and takes `now` from the
caller (the API or the worker, never the agent)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pg_core.jobs import (
    DEFAULT_LEASE_TTL_S,
    CompletionAction,
    JobSpec,
    JobStatus,
    JobType,
    LeaseCheck,
    Transition,
    after_failure,
    after_lease_expired,
    can_claim_more,
    canonical_sha,
    check_lease,
    completion_action,
    lease_expiry,
)
from pg_db.models import Job

MAX_ERROR_CHARS = 4000


def enqueue(
    session: Session, specs: Iterable[JobSpec], *, validation_id: int | None, now: datetime
) -> list[int]:
    """Insert jobs; a job whose idempotency key exists is skipped. Returns the new job ids."""
    created: list[int] = []
    for spec in specs:
        statement = (
            insert(Job)
            .values(
                type=spec.type.value,
                payload=spec.payload,
                requires=spec.requires,
                status=JobStatus.QUEUED.value,
                priority=spec.priority,
                attempts=0,
                max_attempts=spec.max_attempts,
                run_after=now,
                idempotency_key=spec.idempotency_key,
                validation_id=validation_id,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Job.id)
        )
        new_id = session.execute(statement).scalar_one_or_none()
        if new_id is not None:
            created.append(new_id)
    return created


def claim(
    session: Session,
    *,
    owner: str,
    types: Sequence[JobType],
    capabilities: Mapping[str, Any],
    now: datetime,
    max_concurrency: int = 1,
    ttl_s: float = DEFAULT_LEASE_TTL_S,
    prefer_build_sha: str | None = None,
) -> Job | None:
    """Lease the next queued job this claimer can run, or None.

    `FOR UPDATE SKIP LOCKED` means two claimers never get the same job and never wait for each
    other. A transaction-scoped advisory lock per owner makes the `max_concurrency` check and
    the lease one step, so an owner never holds more leases than it advertised.
    `prefer_build_sha` puts jobs for the build already on the device first, so two validations
    of different builds do not reinstall the APK between every test."""
    session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"claim:{owner}"))))
    held = session.execute(
        select(func.count())
        .select_from(Job)
        .where(Job.status == JobStatus.LEASED.value, Job.lease_owner == owner)
    ).scalar_one()
    if not can_claim_more(held, max_concurrency):
        return None
    order: list[Any] = []
    if prefer_build_sha is not None:
        same_build = Job.payload["build_sha"].astext == prefer_build_sha
        order.append(func.coalesce(same_build, False).desc())
    order += [Job.priority.desc(), Job.id]
    picked = session.execute(
        select(Job)
        .where(
            Job.status == JobStatus.QUEUED.value,
            Job.type.in_([t.value for t in types]),
            Job.requires.contained_by(dict(capabilities)),
            Job.run_after <= now,
        )
        .order_by(*order)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if picked is None:
        return None
    picked.status = JobStatus.LEASED.value
    picked.attempts += 1
    picked.lease_owner = owner
    picked.lease_token = uuid.uuid4().hex
    picked.lease_expires_at = lease_expiry(now, ttl_s)
    picked.updated_at = now
    session.flush()
    return picked


def locked(session: Session, job_id: int) -> Job | None:
    """The job row, locked until the end of the transaction."""
    return session.get(Job, job_id, with_for_update=True)


def heartbeat(
    session: Session, job_id: int, token: str, now: datetime, ttl_s: float = DEFAULT_LEASE_TTL_S
) -> LeaseCheck:
    job = locked(session, job_id)
    if job is None:
        return LeaseCheck.STALE
    check = check_lease(JobStatus(job.status), job.lease_token, token)
    if check is LeaseCheck.OK:
        job.lease_expires_at = lease_expiry(now, ttl_s)
        job.updated_at = now
    return check


def record_completion(
    job: Job, token: str, result: Mapping[str, Any], now: datetime
) -> CompletionAction:
    """Store a result under the current lease, once. The caller holds the row lock (`locked`)
    and writes the result's domain rows in the same transaction when the action is STORE."""
    sha = canonical_sha(result)
    action = completion_action(
        status=JobStatus(job.status),
        current_token=job.lease_token,
        current_result_sha=job.result_sha,
        token=token,
        result_sha=sha,
    )
    if action is CompletionAction.STORE:
        job.status = JobStatus.SUCCEEDED.value
        job.result = dict(result)
        job.result_sha = sha
        job.last_error = None
        _end_lease(job, now)
        job.finished_at = now
    return action


def apply_transition(job: Job, transition: Transition, error: str | None, now: datetime) -> None:
    job.status = transition.status.value
    if transition.run_after is not None:
        job.run_after = transition.run_after
    if error is not None:
        job.last_error = error[-MAX_ERROR_CHARS:]
    _end_lease(job, now)
    if transition.status.is_final:
        job.finished_at = now


def record_failure(
    job: Job,
    token: str,
    *,
    error: str,
    retryable: bool,
    now: datetime,
    jitter: float,
) -> Transition | None:
    """A claimer reports that the job failed. None when the lease is not current (stale)."""
    if check_lease(JobStatus(job.status), job.lease_token, token) is not LeaseCheck.OK:
        return None
    transition = after_failure(
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        retryable=retryable,
        now=now,
        jitter=jitter,
    )
    apply_transition(job, transition, error, now)
    return transition


def release(job: Job, token: str, now: datetime) -> bool:
    """Give a job back before working on it (agent shutdown): the attempt does not count."""
    if check_lease(JobStatus(job.status), job.lease_token, token) is not LeaseCheck.OK:
        return False
    job.status = JobStatus.QUEUED.value
    job.attempts = max(job.attempts - 1, 0)
    job.run_after = now
    _end_lease(job, now)
    return True


def expire_leases(session: Session, now: datetime, limit: int = 100) -> list[Job]:
    """Return expired leases to the queue (or make them dead). Returns the jobs it changed."""
    expired = list(
        session.execute(
            select(Job)
            .where(Job.status == JobStatus.LEASED.value, Job.lease_expires_at < now)
            .order_by(Job.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    for job in expired:
        owner = job.lease_owner
        transition = after_lease_expired(
            attempts=job.attempts, max_attempts=job.max_attempts, now=now
        )
        apply_transition(job, transition, f"lease expired (owner {owner})", now)
    session.flush()
    return expired


def retry_dead(session: Session, job_id: int, now: datetime) -> bool:
    """An admin puts a dead job back in the queue with fresh attempts."""
    job = locked(session, job_id)
    if job is None or job.status != JobStatus.DEAD.value:
        return False
    job.status = JobStatus.QUEUED.value
    job.attempts = 0
    job.run_after = now
    job.finished_at = None
    job.updated_at = now
    return True


def _end_lease(job: Job, now: datetime) -> None:
    job.lease_token = None
    job.lease_expires_at = None
    job.updated_at = now


def by_key(session: Session, key: str) -> Job | None:
    return session.execute(select(Job).where(Job.idempotency_key == key)).scalar_one_or_none()


def of_validation(session: Session, validation_id: int) -> list[Job]:
    return list(
        session.execute(
            select(Job).where(Job.validation_id == validation_id).order_by(Job.id)
        ).scalars()
    )


def counts_by_status(session: Session) -> dict[str, int]:
    rows = session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    return {str(status): int(count) for status, count in rows}
