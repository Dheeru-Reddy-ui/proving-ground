"""Classify one test attempt from what the runner observed (pure rules, M1.6).

The pytest plugin records each test's failure kind in `pg_results.json`; these rules turn that,
the JUnit outcome and the process status into an `Outcome`, and decide when an attempt is
infra. Infra attempts are retried and never count as evidence (ADR-0004).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from pg_core.gates.base import Outcome

# Text that only appears when the device chain, not the game, failed.
INFRA_PATTERNS: tuple[tuple[str, str], ...] = (
    ("no_app", r"No app connected|NoAppConnected"),
    (
        "connection",
        r"ConnectionRefused|Connection refused|ConnectionError|AppDisconnected|WebSocket",
    ),
    ("device", r"device '?[\w.:-]*'? not found|device offline|no devices/emulators found"),
    ("adb", r"adb was not found"),
    (
        "driver_slot",
        r"MaxNoOfConnectionsDriversExceeded|MultipleDriversTryingToConnect|MultipleDriverError",
    ),
    ("infra_error", r"PGInfraError"),
)

_KIND_TO_OUTCOME = {
    "assertion": Outcome.ASSERTION,
    "pg_timeout": Outcome.PG_TIMEOUT,
    "game_error": Outcome.GAME_ERROR,
    "test_error": Outcome.TEST_ERROR,
    "infra": Outcome.INFRA,
}


class Observation(BaseModel):
    """What the runner saw for one test in one attempt."""

    model_config = ConfigDict(frozen=True)

    exit_code: int | None  # None when the process was killed on timeout
    timed_out: bool
    junit_outcome: str | None  # passed / failed / error / skipped; None if no JUnit entry
    plugin_entry: Mapping[str, Any] | None  # this test's entry in pg_results.json
    output_tail: str = ""


class Classification(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: Outcome
    rule: str
    detail: str = ""


def infra_pattern(text: str) -> str | None:
    for name, pattern in INFRA_PATTERNS:
        if re.search(pattern, text):
            return name
    return None


def classify(obs: Observation) -> Classification:
    """The outcome of one attempt, and which rule decided it."""
    if obs.timed_out:
        return Classification(outcome=Outcome.INFRA, rule="wall_clock_timeout")
    entry = obs.plugin_entry
    if entry is None:
        if obs.junit_outcome == "passed":
            return Classification(outcome=Outcome.TEST_ERROR, rule="no_plugin_record")
        return Classification(
            outcome=Outcome.INFRA,
            rule="no_plugin_record",
            detail=f"exit code {obs.exit_code}, junit {obs.junit_outcome}",
        )
    phases: Mapping[str, Any] = entry.get("phases", {})
    for phase in ("setup", "call", "teardown"):
        info = phases.get(phase)
        if info is None or info.get("outcome") == "passed":
            continue
        kind = str(info.get("failure_kind") or "")
        message = str(info.get("message") or "")
        if (
            phase == "teardown"
            and kind == "infra"
            and phases.get("call", {}).get("outcome") == "passed"
        ):
            # The test body finished and passed; a broken disconnect does not change that.
            return Classification(
                outcome=Outcome.PASSED, rule="passed_teardown_infra", detail=message[-300:]
            )
        if kind == "infra" or (pattern := infra_pattern(message)) is not None:
            rule = "plugin_infra" if kind == "infra" else f"pattern_{pattern}"
            return Classification(outcome=Outcome.INFRA, rule=rule, detail=message[-300:])
        outcome = _KIND_TO_OUTCOME.get(kind, Outcome.TEST_ERROR)
        return Classification(outcome=outcome, rule=f"plugin_{phase}", detail=message[-300:])
    if phases.get("call", {}).get("outcome") == "passed":
        return Classification(outcome=Outcome.PASSED, rule="plugin_passed")
    return Classification(
        outcome=Outcome.INFRA, rule="incomplete_record", detail=str(sorted(phases))
    )


def backoff_s(attempt: int, base_s: float, jitter: float) -> float:
    """Delay before retry number `attempt` (1-based): base * 2^(attempt-1), plus jitter in
    [0, jitter) supplied by the caller (pg_core takes no randomness of its own)."""
    return float(base_s * 2 ** (attempt - 1) + jitter)
