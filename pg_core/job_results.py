"""What claimers report when they finish a job: validated at the API boundary, stored as the
job's result, and turned into domain rows (executions) by `pg_db.pipeline`."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from pg_core.gates.base import Outcome

MAX_ATTEMPTS_REPORTED = 10  # the runner makes 1 + MAX_RETRIES (2) attempts


class AttemptReport(BaseModel):
    """One runner attempt of a RUN_TEST job (`pg_runner.runner.Attempt`, without local paths)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt: int = Field(ge=1)
    outcome: Outcome
    rule: str = Field(max_length=64)
    detail: str = Field(default="", max_length=4000)
    duration_s: float = Field(ge=0)  # the test body only
    wall_s: float = Field(ge=0)
    exit_code: int | None = None
    timed_out: bool = False
    game_errors: tuple[str, ...] = Field(default=(), max_length=50)
    pgtelem_lines: int = Field(default=0, ge=0)
    actions: tuple[str, ...] = Field(default=(), max_length=500)
    artifacts: dict[str, str] = Field(default_factory=dict)  # artifact name -> storage key


class RunTestResult(BaseModel):
    """Every attempt of one RUN_TEST job; the last one is the result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempts: tuple[AttemptReport, ...] = Field(min_length=1, max_length=MAX_ATTEMPTS_REPORTED)
    started_at: datetime
    finished_at: datetime

    @property
    def final(self) -> AttemptReport:
        return self.attempts[-1]


def run_outcome(result: dict[str, object] | None) -> tuple[Outcome, float] | None:
    """(final outcome, test-body seconds) of a stored RUN_TEST result, or None without one."""
    if not result:
        return None
    parsed = RunTestResult.model_validate(result)
    return parsed.final.outcome, parsed.final.duration_s
