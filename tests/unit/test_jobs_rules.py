"""Job queue rules (ADR-0006): retries, dead jobs, lease checks and idempotent completion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pg_core.jobs import (
    RETRY_CAP_S,
    CompletionAction,
    JobSpec,
    JobStatus,
    JobType,
    LeaseCheck,
    after_failure,
    after_lease_expired,
    can_claim_more,
    canonical_sha,
    check_lease,
    completion_action,
    is_expired,
    lease_expiry,
    retry_delay_s,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def test_retryable_failure_goes_back_to_the_queue_with_backoff() -> None:
    t = after_failure(attempts=1, max_attempts=3, retryable=True, now=NOW, jitter=0.5)
    assert t.status is JobStatus.QUEUED
    assert t.run_after == NOW + timedelta(seconds=15.5)
    t2 = after_failure(attempts=2, max_attempts=3, retryable=True, now=NOW, jitter=0.0)
    assert t2.run_after == NOW + timedelta(seconds=30)


def test_the_last_retryable_failure_makes_the_job_dead() -> None:
    t = after_failure(attempts=3, max_attempts=3, retryable=True, now=NOW, jitter=0.0)
    assert t.status is JobStatus.DEAD
    assert t.run_after is None


def test_a_non_retryable_failure_is_final_at_once() -> None:
    t = after_failure(attempts=1, max_attempts=3, retryable=False, now=NOW, jitter=0.0)
    assert t.status is JobStatus.FAILED


def test_backoff_is_capped() -> None:
    assert retry_delay_s(20, 0.0) == RETRY_CAP_S
    assert retry_delay_s(0, 0.0) == 15.0  # never below the first delay


def test_an_expired_lease_requeues_until_attempts_run_out() -> None:
    assert after_lease_expired(attempts=1, max_attempts=2, now=NOW).status is JobStatus.QUEUED
    assert after_lease_expired(attempts=1, max_attempts=2, now=NOW).run_after == NOW
    assert after_lease_expired(attempts=2, max_attempts=2, now=NOW).status is JobStatus.DEAD


def test_lease_expiry_and_check() -> None:
    expires = lease_expiry(NOW, 120)
    assert expires == NOW + timedelta(seconds=120)
    assert not is_expired(expires, NOW + timedelta(seconds=120))
    assert is_expired(expires, NOW + timedelta(seconds=121))
    assert not is_expired(None, NOW)


@pytest.mark.parametrize(
    ("status", "current", "token", "expected"),
    [
        (JobStatus.LEASED, "t1", "t1", LeaseCheck.OK),
        (JobStatus.LEASED, "t1", "t2", LeaseCheck.STALE),  # re-leased after expiry
        (JobStatus.LEASED, None, "t1", LeaseCheck.STALE),
        (JobStatus.QUEUED, None, "t1", LeaseCheck.STALE),  # lease expired, back in the queue
        (JobStatus.DEAD, "t1", "t1", LeaseCheck.STALE),
        (JobStatus.SUCCEEDED, "t1", "t1", LeaseCheck.STALE),
    ],
)
def test_check_lease(
    status: JobStatus, current: str | None, token: str, expected: LeaseCheck
) -> None:
    assert check_lease(status, current, token) is expected


@pytest.mark.parametrize(
    ("status", "current_token", "current_sha", "token", "sha", "expected"),
    [
        (JobStatus.LEASED, "t1", None, "t1", "r1", CompletionAction.STORE),
        (JobStatus.SUCCEEDED, "t1", "r1", "t1", "r1", CompletionAction.NO_OP),
        # a retry of the same completion from the old lease is still a no-op
        (JobStatus.SUCCEEDED, None, "r1", "t0", "r1", CompletionAction.NO_OP),
        (JobStatus.SUCCEEDED, "t1", "r1", "t1", "r2", CompletionAction.CONFLICT),
        (JobStatus.LEASED, "t2", None, "t1", "r1", CompletionAction.STALE),
        (JobStatus.QUEUED, None, None, "t1", "r1", CompletionAction.STALE),
        (JobStatus.DEAD, None, None, "t1", "r1", CompletionAction.STALE),
        (JobStatus.FAILED, "t1", None, "t1", "r1", CompletionAction.STALE),
    ],
)
def test_completion_is_idempotent(
    status: JobStatus,
    current_token: str | None,
    current_sha: str | None,
    token: str,
    sha: str,
    expected: CompletionAction,
) -> None:
    action = completion_action(
        status=status,
        current_token=current_token,
        current_result_sha=current_sha,
        token=token,
        result_sha=sha,
    )
    assert action is expected


def test_canonical_sha_ignores_key_order_and_whitespace() -> None:
    assert canonical_sha({"a": 1, "b": [1, 2]}) == canonical_sha({"b": [1, 2], "a": 1})
    assert canonical_sha({"a": 1}) != canonical_sha({"a": 2})


def test_max_concurrency() -> None:
    assert can_claim_more(0, 1)
    assert not can_claim_more(1, 1)


def test_final_states() -> None:
    assert {s for s in JobStatus if s.is_final} == {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.DEAD,
    }


def test_job_spec_validation() -> None:
    with pytest.raises(ValidationError):
        JobSpec(type=JobType.SCORE, idempotency_key="")
    with pytest.raises(ValidationError):
        JobSpec(type=JobType.SCORE, idempotency_key="k", max_attempts=0)
