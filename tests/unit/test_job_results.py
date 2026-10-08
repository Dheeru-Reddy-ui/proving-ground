"""RUN_TEST results as the agent reports them: validated strictly at the boundary."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pg_core.gates.base import Outcome
from pg_core.job_results import AttemptReport, RunTestResult, run_outcome

T = datetime(2026, 10, 8, tzinfo=UTC)


def attempt(n: int, outcome: Outcome, duration: float = 4.0) -> AttemptReport:
    return AttemptReport(attempt=n, outcome=outcome, rule="r", duration_s=duration, wall_s=9.0)


def test_the_last_attempt_is_the_result() -> None:
    result = RunTestResult(
        attempts=(attempt(1, Outcome.INFRA, 0.0), attempt(2, Outcome.ASSERTION, 7.5)),
        started_at=T,
        finished_at=T,
    )
    assert result.final.outcome is Outcome.ASSERTION
    assert run_outcome(result.model_dump(mode="json")) == (Outcome.ASSERTION, 7.5)


def test_no_result_means_no_outcome() -> None:
    assert run_outcome(None) is None
    assert run_outcome({}) is None


def test_unknown_fields_and_bad_values_are_refused() -> None:
    good = {"attempt": 1, "outcome": "passed", "rule": "r", "duration_s": 1.0, "wall_s": 2.0}
    AttemptReport.model_validate(good)
    for bad in (
        {**good, "local_path": "C:/secret"},
        {**good, "outcome": "kill"},
        {**good, "attempt": 0},
        {**good, "duration_s": -1},
        {**good, "detail": "x" * 4001},
    ):
        with pytest.raises(ValidationError):
            AttemptReport.model_validate(bad)


def test_a_result_needs_one_to_ten_attempts() -> None:
    with pytest.raises(ValidationError):
        RunTestResult(attempts=(), started_at=T, finished_at=T)
    with pytest.raises(ValidationError):
        RunTestResult(
            attempts=tuple(attempt(i, Outcome.INFRA) for i in range(1, 12)),
            started_at=T,
            finished_at=T,
        )
