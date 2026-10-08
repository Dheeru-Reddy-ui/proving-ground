"""Fakes shared by integration tests: a clock, the writes pg_worker makes for GENERATE and SCORE
jobs (fake LLM, fake gates), and RUN_TEST results as the agent reports them."""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from pg_core.gates.base import Outcome
from pg_core.job_results import AttemptReport, RunTestResult
from pg_core.jobs import CompletionAction
from pg_db import pipeline, repo
from pg_db.models import Job, Validation

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def tick(self, seconds: float = 1.0) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


def worker_step(s: Session, job: Job, clock: Clock, fail_g1: Collection[int]) -> None:
    """What pg_worker writes for GENERATE and SCORE jobs (fake LLM, fake gates)."""
    token = job.lease_token or ""
    assert pipeline.holds_lease(s, job.id, token) is not None
    payload = job.payload
    if job.type == "GENERATE":
        validation = s.get_one(Validation, payload["validation_id"])
        run = repo.create_generation_run(
            s,
            build_id=validation.build_id,
            feature=payload["feature"],
            provider="fake",
            model="fake",
            prompt_version="v2",
            prompt_hash="h",
            n_requested=payload["n"],
            validation_id=validation.id,
            job_key=job.idempotency_key,
        )
        for i in range(payload["n"]):
            repo.add_candidate(s, run.id, f"test_{i}", "", f"code {i}", [f"STORE-{i + 1}"], idx=i)
        result = {"generation_run_id": run.id}
    elif payload["stage"] == "static":
        for cand in repo.candidates_of_run(s, payload["generation_run_id"]):
            passed = cand.idx not in fail_g1
            cand.gates = {"G1": {"gate": "G1", "passed": passed}}
            cand.pages_used = ["main_menu", "store"]
        result = {"scored": "static"}
    else:
        result = {"scored": "final"}
    assert pipeline.complete(s, job.id, token, result, clock.tick()) is CompletionAction.STORE


def run_result(outcome: Outcome, artifacts: dict[str, str] | None = None) -> RunTestResult:
    attempts = [outcome] if outcome is not Outcome.INFRA else [Outcome.INFRA] * 3
    return RunTestResult(
        attempts=tuple(
            AttemptReport(
                attempt=i,
                outcome=o,
                rule="plugin_passed" if o is Outcome.PASSED else f"rule_{o.value}",
                detail="" if o is not Outcome.INFRA else "NoAppConnected\ndevice offline",
                duration_s=12.5,
                wall_s=30.0,
                artifacts=artifacts or {},
            )
            for i, o in enumerate(attempts, 1)
        ),
        started_at=T0,
        finished_at=T0,
    )
