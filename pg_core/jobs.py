"""Job queue rules (ADR-0006): job types, states and every state transition, as pure functions.

`pg_db/jobs.py` holds the SQL; it asks these functions what to do, so the rules are unit-tested
with a fake clock. Time and jitter are always passed in.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pg_core.classify import backoff_s

DEFAULT_LEASE_TTL_S = 120.0
DEFAULT_MAX_ATTEMPTS = 3
RETRY_BASE_S = 15.0  # first retry after 15 s (+ jitter), then 30 s, 60 s, ...
RETRY_CAP_S = 600.0


class JobType(StrEnum):
    INSTALL_BUILD = "INSTALL_BUILD"
    RUN_TEST = "RUN_TEST"
    GENERATE = "GENERATE"
    SCORE = "SCORE"
    CRAWL = "CRAWL"  # Phase 3 stub: enqueued by nothing yet, claimed by nobody


DEVICE_JOB_TYPES = frozenset({JobType.INSTALL_BUILD, JobType.RUN_TEST})
WORKER_JOB_TYPES = frozenset({JobType.GENERATE, JobType.SCORE})


class JobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # a non-retryable failure reported by the claimer
    DEAD = "dead"  # retries or lease expiries used up every attempt

    @property
    def is_final(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.DEAD)


class JobSpec(BaseModel):
    """A job to enqueue. The idempotency key names the work, so planning it twice is harmless."""

    model_config = ConfigDict(frozen=True)

    type: JobType
    idempotency_key: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    requires: dict[str, Any] = Field(default_factory=dict)  # must be contained in capabilities
    priority: int = 0  # higher first
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1)


def canonical_sha(value: Mapping[str, Any]) -> str:
    """sha256 of a JSON value written canonically (sorted keys, no whitespace)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def lease_expiry(now: datetime, ttl_s: float = DEFAULT_LEASE_TTL_S) -> datetime:
    return now + timedelta(seconds=ttl_s)


def is_expired(lease_expires_at: datetime | None, now: datetime) -> bool:
    return lease_expires_at is not None and lease_expires_at < now


def retry_delay_s(attempts: int, jitter: float) -> float:
    """Backoff before the next attempt after `attempts` failed ones, capped."""
    return min(RETRY_CAP_S, backoff_s(max(attempts, 1), RETRY_BASE_S, jitter))


class Transition(BaseModel):
    """Where a leased job goes after a failure or a lost lease."""

    model_config = ConfigDict(frozen=True)

    status: JobStatus
    run_after: datetime | None = None  # set when it goes back to the queue


def after_failure(
    *, attempts: int, max_attempts: int, retryable: bool, now: datetime, jitter: float
) -> Transition:
    """A claimer reported a failure. Retryable failures go back to the queue with backoff until
    every attempt is used; then the job is dead. Non-retryable failures are final at once."""
    if not retryable:
        return Transition(status=JobStatus.FAILED)
    if attempts >= max_attempts:
        return Transition(status=JobStatus.DEAD)
    delay = retry_delay_s(attempts, jitter)
    return Transition(status=JobStatus.QUEUED, run_after=now + timedelta(seconds=delay))


def after_lease_expired(*, attempts: int, max_attempts: int, now: datetime) -> Transition:
    """The claimer stopped heartbeating (crashed, lost network). The attempt counts."""
    if attempts >= max_attempts:
        return Transition(status=JobStatus.DEAD)
    return Transition(status=JobStatus.QUEUED, run_after=now)


class LeaseCheck(StrEnum):
    OK = "ok"
    STALE = "stale"  # wrong token, or the job is no longer leased


def check_lease(status: JobStatus, current_token: str | None, token: str) -> LeaseCheck:
    if status is JobStatus.LEASED and current_token is not None and current_token == token:
        return LeaseCheck.OK
    return LeaseCheck.STALE


class CompletionAction(StrEnum):
    STORE = "store"  # first completion under the current lease: store the result
    NO_OP = "no_op"  # the same result again: answer success, change nothing
    CONFLICT = "conflict"  # a different result for a job that already succeeded: reject, log
    STALE = "stale"  # not the current lease (expired and re-leased, released, dead): reject


def completion_action(
    *,
    status: JobStatus,
    current_token: str | None,
    current_result_sha: str | None,
    token: str,
    result_sha: str,
) -> CompletionAction:
    """Completing a job is idempotent: exactly one result is ever stored."""
    if status is JobStatus.SUCCEEDED:
        return (
            CompletionAction.NO_OP
            if current_result_sha == result_sha
            else CompletionAction.CONFLICT
        )
    if check_lease(status, current_token, token) is LeaseCheck.OK:
        return CompletionAction.STORE
    return CompletionAction.STALE


def can_claim_more(leased_by_claimer: int, max_concurrency: int) -> bool:
    return leased_by_claimer < max_concurrency
