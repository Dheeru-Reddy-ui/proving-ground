"""Unit tests for the smoke-baseline summary (pg_core.smoke)."""

import pytest

from pg_core.smoke import SmokeRun, percentile, summarize


def run(index: int, passed: bool, seconds: float) -> SmokeRun:
    return SmokeRun(
        index=index,
        passed=passed,
        seconds=seconds,
        steps={},
        failed_step=None if passed else "open_store",
        error=None if passed else "StepFailed: timed out",
    )


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [
        ([], 50, None),
        ([7.0], 95, 7.0),
        ([1.0, 2.0, 3.0, 4.0], 50, 2.0),
        ([float(i) for i in range(1, 21)], 95, 19.0),
        ([float(i) for i in range(1, 21)], 50, 10.0),
        ([3.0, 1.0, 2.0], 100, 3.0),
    ],
)
def test_nearest_rank_percentile(values: list[float], q: float, expected: float | None) -> None:
    assert percentile(values, q) == expected


def test_nineteen_of_twenty_meets_the_bar() -> None:
    runs = [run(i, passed=i != 7, seconds=float(i)) for i in range(1, 21)]
    summary = summarize(runs)
    assert summary.passed == 19
    assert summary.meets_bar
    assert [f.index for f in summary.failures] == [7]
    # durations come from passing runs only: 1-6 and 8-20, so the 10th value is 11
    assert summary.p50_seconds == 11.0


def test_eighteen_of_twenty_misses_the_bar() -> None:
    runs = [run(i, passed=i not in (3, 9), seconds=10.0) for i in range(1, 21)]
    assert not summarize(runs).meets_bar


def test_no_runs_never_meets_the_bar() -> None:
    summary = summarize([])
    assert summary.pass_rate == 0.0
    assert not summary.meets_bar
    assert summary.p95_seconds is None
