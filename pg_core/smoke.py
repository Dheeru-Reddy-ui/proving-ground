"""Reliability baseline (M0.7): summarise repeated smoke runs into pass rate and durations."""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

REQUIRED_PASS_RATE = 0.95  # 19 of 20


class SmokeRun(BaseModel):
    """One smoke iteration: reset, launch, main menu, open store, close store."""

    model_config = ConfigDict(frozen=True)

    index: int
    passed: bool
    seconds: float
    steps: dict[str, float]  # seconds spent in each completed step
    failed_step: str | None = None
    error: str | None = None
    error_logs: int = 0  # error-level log notifications from the game during the run
    compat_dialog_dismissed: bool = False  # Android's 16 KB warning was tapped away (ADR-0008)


class SmokeSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    runs: int
    passed: int
    pass_rate: float
    p50_seconds: float | None  # over passing runs; failed runs stop early
    p95_seconds: float | None
    meets_bar: bool
    failures: tuple[SmokeRun, ...]


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(runs: Sequence[SmokeRun], required_rate: float = REQUIRED_PASS_RATE) -> SmokeSummary:
    passed = [run for run in runs if run.passed]
    durations = [run.seconds for run in passed]
    rate = len(passed) / len(runs) if runs else 0.0
    return SmokeSummary(
        runs=len(runs),
        passed=len(passed),
        pass_rate=rate,
        p50_seconds=percentile(durations, 50),
        p95_seconds=percentile(durations, 95),
        meets_bar=bool(runs) and rate >= required_rate,
        failures=tuple(run for run in runs if not run.passed),
    )
