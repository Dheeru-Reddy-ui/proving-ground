"""The job queue against real Postgres (ADR-0006): SKIP LOCKED claims, leases, idempotency."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, delete, text
from sqlalchemy.orm import Session

from pg_core.jobs import CompletionAction, JobSpec, JobStatus, JobType, LeaseCheck
from pg_db import jobs as queue
from pg_db.models import Job
from pg_db.session import session_scope

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
DEVICE = {"platform": "android", "abi": "arm64-v8a"}


@pytest.fixture(autouse=True)
def empty_queue(engine: Engine) -> None:
    with session_scope(engine) as s:
        s.execute(delete(Job))


def spec(key: str, kind: JobType = JobType.RUN_TEST, **kw: object) -> JobSpec:
    return JobSpec.model_validate({"type": kind, "idempotency_key": key, **kw})


def put(engine: Engine, *specs: JobSpec, now: datetime = T0) -> list[int]:
    with session_scope(engine) as s:
        return queue.enqueue(s, specs, validation_id=None, now=now)


def take(
    s: Session,
    owner: str = "agent-1",
    types: tuple[JobType, ...] = (JobType.RUN_TEST, JobType.INSTALL_BUILD),
    now: datetime = T0,
    **kw: object,
) -> Job | None:
    return queue.claim(s, owner=owner, types=types, capabilities=DEVICE, now=now, **kw)  # type: ignore[arg-type]


def test_enqueue_is_idempotent_on_the_key(engine: Engine) -> None:
    assert len(put(engine, spec("a"), spec("b"))) == 2
    assert len(put(engine, spec("a"), spec("c"))) == 1  # only "c" is new
    with session_scope(engine) as s:
        assert s.query(Job).count() == 3


def test_claim_filters_by_type_capabilities_and_time(engine: Engine) -> None:
    put(
        engine,
        spec("worker-job", JobType.GENERATE),
        spec("needs-ios", requires={"platform": "ios"}),
        spec("android", requires={"platform": "android"}),
    )
    with session_scope(engine) as s:
        job = take(s)
        assert job is not None
        assert job.idempotency_key == "android"
        assert job.status == "leased"
        assert job.attempts == 1
        assert job.lease_owner == "agent-1"
        assert job.lease_expires_at == T0 + timedelta(seconds=120)
    with session_scope(engine) as s:
        assert take(s, owner="agent-2") is None  # the others are not for a device agent


def test_claim_order_is_priority_then_age_then_preferred_build(engine: Engine) -> None:
    put(
        engine,
        spec("old", payload={"build_sha": "x"}),
        spec("other-build", payload={"build_sha": "y"}),
        spec("urgent", JobType.INSTALL_BUILD, priority=100),
    )
    order = []
    for _ in range(3):
        with session_scope(engine) as s:
            job = take(s, max_concurrency=10, prefer_build_sha="y")
            assert job is not None
            order.append(job.idempotency_key)
    assert order == ["other-build", "urgent", "old"]  # the installed build first, then priority


def test_max_concurrency_is_enforced_per_owner(engine: Engine) -> None:
    put(engine, spec("a"), spec("b"))
    with session_scope(engine) as s:
        assert take(s) is not None
    with session_scope(engine) as s:
        assert take(s) is None  # agent-1 already holds its one lease
        assert take(s, owner="agent-2") is not None


def test_concurrent_claimers_never_get_the_same_job(engine: Engine) -> None:
    put(engine, *(spec(f"j{i}") for i in range(40)))
    claimed: list[int] = []
    lock = threading.Lock()

    def claimer(owner: str) -> None:
        while True:
            with session_scope(engine) as s:
                job = take(s, owner=owner, max_concurrency=1000)
                if job is None:
                    return
                with lock:
                    claimed.append(job.id)

    threads = [threading.Thread(target=claimer, args=(f"agent-{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(claimed) == 40
    assert len(set(claimed)) == 40


def test_a_locked_job_is_skipped_not_waited_for(engine: Engine) -> None:
    first, second = put(engine, spec("first"), spec("second"))
    with session_scope(engine) as holder:
        holder.execute(text("SELECT id FROM jobs WHERE id = :id FOR UPDATE"), {"id": first})
        with session_scope(engine) as s:
            s.execute(text("SET LOCAL lock_timeout = '2s'"))
            job = take(s, owner="agent-2")
            assert job is not None
            assert job.id == second


def test_a_retried_job_waits_for_its_backoff(engine: Engine) -> None:
    put(engine, spec("a"))
    with session_scope(engine) as s:
        job = take(s)
        assert job is not None
        t = queue.record_failure(
            job, job.lease_token or "", error="boom", retryable=True, now=T0, jitter=0.0
        )
        assert t is not None
        assert t.status is JobStatus.QUEUED
    with session_scope(engine) as s:
        assert take(s, now=T0 + timedelta(seconds=10)) is None
        job = take(s, now=T0 + timedelta(seconds=16))
        assert job is not None
        assert job.attempts == 2
        assert job.last_error == "boom"


def test_heartbeat_extends_only_the_current_lease(engine: Engine) -> None:
    (job_id,) = put(engine, spec("a"))
    with session_scope(engine) as s:
        job = take(s)
        assert job is not None
        token = job.lease_token or ""
    later = T0 + timedelta(seconds=100)
    with session_scope(engine) as s:
        assert queue.heartbeat(s, job_id, token, later) is LeaseCheck.OK
        assert queue.heartbeat(s, job_id, "not-the-token", later) is LeaseCheck.STALE
        assert queue.heartbeat(s, 999_999, token, later) is LeaseCheck.STALE
    with session_scope(engine) as s:
        assert s.get_one(Job, job_id).lease_expires_at == later + timedelta(seconds=120)


def test_lease_expiry_retry_and_exactly_one_completion(engine: Engine) -> None:
    (job_id,) = put(engine, spec("a"))
    with session_scope(engine) as s:
        job = take(s)
        assert job is not None
        old_token = job.lease_token or ""
    # the agent dies: no heartbeat; the reaper requeues the job after the TTL
    with session_scope(engine) as s:
        assert queue.expire_leases(s, T0 + timedelta(seconds=119)) == []
        expired = queue.expire_leases(s, T0 + timedelta(seconds=121))
        assert [j.id for j in expired] == [job_id]
        assert expired[0].status == "queued"
        assert expired[0].last_error == "lease expired (owner agent-1)"
    later = T0 + timedelta(seconds=130)
    with session_scope(engine) as s:
        job = take(s, now=later)
        assert job is not None
        assert job.attempts == 2
        new_token = job.lease_token or ""
    assert new_token != old_token
    result = {"outcome": "passed"}
    with session_scope(engine) as s:
        job = queue.locked(s, job_id)
        assert job is not None
        assert queue.record_completion(job, old_token, result, later) is CompletionAction.STALE
        assert queue.record_completion(job, new_token, result, later) is CompletionAction.STORE
    with session_scope(engine) as s:
        job = queue.locked(s, job_id)
        assert job is not None
        assert queue.record_completion(job, new_token, result, later) is CompletionAction.NO_OP
        assert queue.record_completion(job, old_token, result, later) is CompletionAction.NO_OP
        assert (
            queue.record_completion(job, new_token, {"outcome": "assertion"}, later)
            is CompletionAction.CONFLICT
        )
    with session_scope(engine) as s:
        job = s.get_one(Job, job_id)
        assert job.status == "succeeded"
        assert job.result == result
        assert job.lease_token is None
        assert job.finished_at == later


def test_expired_leases_make_a_job_dead_after_max_attempts(engine: Engine) -> None:
    (job_id,) = put(engine, spec("a", max_attempts=2))
    now = T0
    for _ in range(2):
        with session_scope(engine) as s:
            assert take(s, now=now) is not None
        now += timedelta(seconds=200)
        with session_scope(engine) as s:
            queue.expire_leases(s, now)
    with session_scope(engine) as s:
        job = s.get_one(Job, job_id)
        assert job.status == "dead"
        assert job.attempts == 2
        assert take(s, now=now) is None
    with session_scope(engine) as s:
        assert queue.retry_dead(s, job_id, now)
        assert not queue.retry_dead(s, job_id, now)  # only dead jobs
    with session_scope(engine) as s:
        job = take(s, now=now)
        assert job is not None
        assert job.attempts == 1


def test_release_returns_the_job_without_counting_the_attempt(engine: Engine) -> None:
    (job_id,) = put(engine, spec("a"))
    with session_scope(engine) as s:
        job = take(s)
        assert job is not None
        assert not queue.release(job, "wrong", T0)
        assert queue.release(job, job.lease_token or "", T0)
    with session_scope(engine) as s:
        job = s.get_one(Job, job_id)
        assert job.status == "queued"
        assert job.attempts == 0


def test_non_retryable_failure_and_stale_failure(engine: Engine) -> None:
    (job_id,) = put(engine, spec("a"))
    with session_scope(engine) as s:
        job = take(s)
        assert job is not None
        token = job.lease_token or ""
        assert queue.record_failure(job, "x", error="e", retryable=False, now=T0, jitter=0) is None
        t = queue.record_failure(job, token, error="refused", retryable=False, now=T0, jitter=0)
        assert t is not None
        assert t.status is JobStatus.FAILED
    with session_scope(engine) as s:
        job = s.get_one(Job, job_id)
        assert job.status == "failed"
        assert job.finished_at == T0
    with session_scope(engine) as s:
        assert queue.counts_by_status(s) == {"failed": 1}
