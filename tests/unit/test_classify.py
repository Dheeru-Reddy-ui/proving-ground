"""Attempt classification rules and the runner's pure helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pg_core.classify import Observation, backoff_s, classify, infra_pattern
from pg_core.gates.base import Outcome
from pg_runner.runner import junit_outcomes, read_pgtelem, scrubbed_env


def entry(
    call: str | None = "passed",
    kind: str | None = None,
    *,
    setup: str = "passed",
    message: str = "",
) -> dict[str, Any]:
    phases: dict[str, Any] = {
        "setup": {
            "outcome": setup,
            "failure_kind": "infra" if setup != "passed" else None,
            "message": message if setup != "passed" else None,
        }
    }
    if call is not None:
        phases["call"] = {"outcome": call, "failure_kind": kind, "message": message or None}
    phases["teardown"] = {"outcome": "passed", "failure_kind": None, "message": None}
    return {"phases": phases}


def obs(
    plugin: dict[str, Any] | None, *, timed_out: bool = False, junit: str | None = "passed"
) -> Observation:
    return Observation(
        exit_code=None if timed_out else 0,
        timed_out=timed_out,
        junit_outcome=junit,
        plugin_entry=plugin,
    )


@pytest.mark.parametrize(
    ("observation", "outcome", "rule"),
    [
        (obs(entry()), Outcome.PASSED, "plugin_passed"),
        (obs(entry("failed", "assertion")), Outcome.ASSERTION, "plugin_call"),
        (obs(entry("failed", "pg_timeout")), Outcome.PG_TIMEOUT, "plugin_call"),
        (obs(entry("failed", "game_error")), Outcome.GAME_ERROR, "plugin_call"),
        (obs(entry("failed", "test_error")), Outcome.TEST_ERROR, "plugin_call"),
        (obs(entry("failed", "infra")), Outcome.INFRA, "plugin_infra"),
        (
            obs(entry(None, setup="failed", message="PGTimeout: the main menu")),
            Outcome.INFRA,
            "plugin_infra",
        ),
        (
            obs(entry("failed", "test_error", message="alttester.exceptions.NoAppConnected: gone")),
            Outcome.INFRA,
            "pattern_no_app",
        ),
        (obs(entry(), timed_out=True), Outcome.INFRA, "wall_clock_timeout"),
        (obs(None, junit="error"), Outcome.INFRA, "no_plugin_record"),
        (obs(None, junit="passed"), Outcome.TEST_ERROR, "no_plugin_record"),
        (obs({"phases": {"setup": {"outcome": "passed"}}}), Outcome.INFRA, "incomplete_record"),
    ],
)
def test_classification_rules(observation: Observation, outcome: Outcome, rule: str) -> None:
    result = classify(observation)
    assert (result.outcome, result.rule) == (outcome, rule)


def test_a_broken_disconnect_after_a_passing_body_still_passes() -> None:
    record = entry()
    record["phases"]["teardown"] = {
        "outcome": "failed",
        "failure_kind": "infra",
        "message": "PGInfraError",
    }
    assert classify(obs(record)).outcome is Outcome.PASSED


def test_infra_patterns() -> None:
    assert infra_pattern("error: device 'R5CX' not found") == "device"
    assert infra_pattern("ConnectionRefusedError: [WinError 10061]") == "connection"
    assert infra_pattern("assert 0 == 750") is None


def test_backoff_doubles_and_adds_the_given_jitter() -> None:
    assert [backoff_s(n, 5.0, 0.0) for n in (1, 2, 3)] == [5.0, 10.0, 20.0]
    assert backoff_s(1, 5.0, 1.5) == 6.5


def test_scrubbed_env_keeps_only_the_allow_list() -> None:
    source = {
        "PATH": "C:/bin",
        "SYSTEMROOT": "C:/Windows",
        "PG_LLM_API_KEY": "secret",
        "DATABASE_URL": "postgres://x",
        "AWS_SECRET": "y",
    }
    env = scrubbed_env(source, Path("ctx.json"))
    assert env["PATH"] == "C:/bin"
    assert env["SYSTEMROOT"] == "C:/Windows"
    assert env["PG_RUN_CONTEXT"] == "ctx.json"
    assert not {"PG_LLM_API_KEY", "DATABASE_URL", "AWS_SECRET"} & set(env)


def test_junit_and_pgtelem_parsing(tmp_path: Path) -> None:
    (tmp_path / "junit.xml").write_text(
        '<testsuites><testsuite><testcase name="test_a"/><testcase name="test_b"><failure message="x"/></testcase>'
        '<testcase name="test_c"><error message="y"/></testcase></testsuite></testsuites>'
    )
    assert junit_outcomes(tmp_path / "junit.xml") == {
        "test_a": "passed",
        "test_b": "failed",
        "test_c": "error",
    }
    assert junit_outcomes(tmp_path / "missing.xml") == {}
    log = tmp_path / "game_log.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"message": 'PGTELEM {"v":1,"frame_ms_p95":17.1}'}),
                json.dumps({"message": "PGFLAGS sb_x"}),
                json.dumps({"message": "PGTELEM {broken"}),
                "not json",
            ]
        )
    )
    assert read_pgtelem(log) == [{"v": 1, "frame_ms_p95": 17.1}]


def test_leftover_artifacts_are_set_aside_not_deleted(tmp_path: Path) -> None:
    from pg_runner.runner import set_aside

    attempt = tmp_path / "attempt_1"
    assert set_aside(attempt) is None
    attempt.mkdir()
    assert set_aside(attempt) is None  # empty: nothing to protect
    (attempt / "pg_results.json").write_text("{}")
    moved = set_aside(attempt)
    assert moved is not None
    assert (moved / "pg_results.json").exists()
    assert not attempt.exists()
