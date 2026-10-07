"""Shared types of the gates: execution outcomes and gate results."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Outcome(StrEnum):
    """How one test execution ended (from the runner's classification)."""

    PASSED = "passed"
    ASSERTION = "assertion"  # the test's assert failed
    PG_TIMEOUT = "pg_timeout"  # an SDK post-condition never held
    GAME_ERROR = "game_error"  # the game logged an error or exception during the test
    TEST_ERROR = "test_error"  # the test itself crashed (TypeError, ...): never a kill
    INFRA = "infra"  # device chain failed: never a kill, never a test result

    @property
    def is_product_failure(self) -> bool:
        """A failure the player could see: these count as kills on a bug build."""
        return self in (Outcome.ASSERTION, Outcome.PG_TIMEOUT, Outcome.GAME_ERROR)

    @property
    def is_valid(self) -> bool:
        """A run that says something about the test (infra runs are retried instead)."""
        return self is not Outcome.INFRA


class Execution(BaseModel):
    """One finished execution of a test, as the gates see it."""

    model_config = ConfigDict(frozen=True)

    outcome: Outcome
    duration_s: float = Field(ge=0)  # the test body only, without reset and connect
    flags: tuple[str, ...] = ()
    execution_id: str | None = None


class Reason(BaseModel):
    """One machine-readable reason, with a human-readable message."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class GateResult(BaseModel):
    """The verdict of one gate. `inconclusive` means it could not decide (e.g. infra failures
    left too few valid runs); the candidate then waits for more runs instead of a decision."""

    model_config = ConfigDict(frozen=True)

    gate: str
    passed: bool
    inconclusive: bool = False
    reasons: tuple[Reason, ...] = ()
    metrics: dict[str, Any] = Field(default_factory=dict)


def reason(code: str, message: str) -> Reason:
    return Reason(code=code, message=message)
