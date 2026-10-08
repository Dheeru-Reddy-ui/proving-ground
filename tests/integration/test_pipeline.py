"""A whole build validation through the job queue, with a fake worker and a fake device agent.

The fakes stand in for `pg_worker` and `pg_agent` (M2.3, M2.4): they claim jobs, write what the
real ones would write, and complete. The point is the DAG: which jobs appear, in what order,
that the holdout split is never run, and that crashes and duplicates leave one result.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from pg_core.gates.base import Outcome
from pg_core.job_results import AttemptReport, RunTestResult
from pg_core.jobs import CompletionAction, JobType
from pg_db import jobs as queue
from pg_db import pipeline, repo
from pg_db.models import (
    Bug,
    Build,
    Candidate,
    Execution,
    GenerationRun,
    Job,
    Kill,
    Validation,
)
from pg_db.session import session_scope

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
SHA = "d" * 64
DEVICE = {"platform": "android"}
WORKER = (JobType.GENERATE, JobType.SCORE)
AGENT = (JobType.INSTALL_BUILD, JobType.RUN_TEST)
Oracle = Callable[[int, str, int], Outcome]  # (candidate idx, flag or "clean", repeat)


@pytest.fixture(autouse=True)
def clean_tables(engine: Engine) -> None:
    with session_scope(engine) as s:
        for table in (Kill, Execution, Job, Candidate, GenerationRun, Validation, Bug, Build):
            s.execute(delete(table))
        repo.register_build(s, SHA, "fake build", "dddddddddddd")
        for code, flag, split, pages in (
            ("D1", "dev_store_a", "dev", ["store"]),
            ("D2", "dev_store_b", "dev", ["store", "main_menu"]),
            ("D3", "dev_missions", "dev", ["missions"]),
            ("H1", "holdout_store", "holdout", ["store"]),
        ):
            s.add(
                Bug(code=code, flag=flag, category="c", feature="store", split=split, pages=pages)
            )


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def tick(self, seconds: float = 1.0) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


def start(engine: Engine, clock: Clock, features: tuple[str, ...] = ("store",)) -> int:
    with session_scope(engine) as s:
        build = s.execute(select(Build)).scalar_one()
        validation, created = pipeline.create_validation(
            s,
            build_id=build.id,
            features=features,
            n=3,
            seed=None,
            manifest_sha="m" * 64,
            run_timeout_s=300.0,
            requested_by="test",
            now=clock.now,
            idempotency_key="request-1",
        )
        assert created
        return validation.id


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


def agent_step(s: Session, job: Job, clock: Clock, oracle: Oracle) -> None:
    token = job.lease_token or ""
    if job.type == "INSTALL_BUILD":
        action = pipeline.complete(s, job.id, token, {"installed": SHA}, clock.tick())
        assert action is CompletionAction.STORE
        return
    payload = job.payload
    cand = s.get_one(Candidate, payload["candidate_id"])
    flags = payload["flags"]
    outcome = oracle(cand.idx or 0, flags[0] if flags else "clean", payload["repeat"])
    result = run_result(outcome)
    action = pipeline.complete_run_test(s, job.id, token, result, clock.tick(), jitter=0.0)
    assert action is CompletionAction.STORE


def run_result(outcome: Outcome) -> RunTestResult:
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
            )
            for i, o in enumerate(attempts, 1)
        ),
        started_at=T0,
        finished_at=T0,
    )


def drive(engine: Engine, clock: Clock, oracle: Oracle, fail_g1: Collection[int] = ()) -> list[str]:
    """Let the fake worker and agent take turns until no job is claimable; returns the keys
    of every job in the order it was claimed."""
    claimed: list[str] = []
    for _ in range(500):
        progressed = False
        for owner, types in (("worker-1", WORKER), ("agent-1", AGENT)):
            with session_scope(engine) as s:
                job = queue.claim(
                    s, owner=owner, types=types, capabilities=DEVICE, now=clock.tick(0.1)
                )
                if job is None:
                    continue
                claimed.append(job.idempotency_key)
                if owner == "worker-1":
                    worker_step(s, job, clock, fail_g1)
                else:
                    agent_step(s, job, clock, oracle)
                progressed = True
        if not progressed:
            # infra retries wait for their backoff; let time pass before giving up
            with session_scope(engine) as s:
                waiting = s.execute(
                    select(func.count()).select_from(Job).where(Job.status == "queued")
                ).scalar_one()
            if not waiting:
                return claimed
            clock.tick(60)
    raise AssertionError("the DAG did not finish")


def validation_row(engine: Engine, validation_id: int) -> Validation:
    with session_scope(engine) as s:
        return s.get_one(Validation, validation_id)


def test_a_validation_runs_the_whole_dag(engine: Engine) -> None:
    clock = Clock()

    def oracle(idx: int, key: str, repeat: int) -> Outcome:
        if idx == 0:
            return Outcome.PASSED if key in ("clean", "dev_store_b") else Outcome.ASSERTION
        if idx == 1:
            return Outcome.ASSERTION if (key, repeat) == ("clean", 2) else Outcome.PASSED
        raise AssertionError("candidate 2 fails G1 and must never run")

    vid = start(engine, clock)
    claimed = drive(engine, clock, oracle, fail_g1={2})
    assert claimed[0] == f"validation:{vid}:generate:store"  # the worker goes first each turn
    assert claimed[1] == f"validation:{vid}:install"
    with session_scope(engine) as s:
        run = s.execute(select(GenerationRun)).scalar_one()
        c0, c1, _ = repo.candidates_of_run(s, run.id)
        group = f"prove-{run.id}"
    runs = [k for k in claimed if k.startswith("run:")]
    assert runs == [
        f"run:{group}:c{c0.id}:clean:r1",
        f"run:{group}:c{c1.id}:clean:r1",
        f"run:{group}:c{c0.id}:clean:r2",
        f"run:{group}:c{c1.id}:clean:r2",  # fails: c1 stops here
        f"run:{group}:c{c0.id}:clean:r3",
        f"run:{group}:c{c0.id}:dev_store_a:r1",
        f"run:{group}:c{c0.id}:dev_store_b:r1",  # passes: no second run
        f"run:{group}:c{c0.id}:dev_store_a:r2",  # fails twice: a kill
    ]
    assert not any("holdout" in k or "dev_missions" in k for k in claimed)
    assert claimed[-1] == f"validation:{vid}:score-final:{run.id}"
    validation = validation_row(engine, vid)
    assert validation.status == "succeeded"
    assert validation.problems == []
    assert validation.finished_at is not None
    with session_scope(engine) as s:
        executions = s.execute(select(Execution).order_by(Execution.id)).scalars().all()
        assert len(executions) == len(runs)
        assert {e.run_group for e in executions} == {group}
        assert all(e.job_id is not None for e in executions)
        assert {tuple(e.flags) for e in executions if e.purpose == "bug"} == {
            ("dev_store_a",),
            ("dev_store_b",),
        }


def test_creating_the_same_validation_twice_returns_the_first(engine: Engine) -> None:
    clock = Clock()
    vid = start(engine, clock)
    with session_scope(engine) as s:
        build = s.execute(select(Build)).scalar_one()
        again, created = pipeline.create_validation(
            s,
            build_id=build.id,
            features=("store",),
            n=3,
            seed=None,
            manifest_sha="m" * 64,
            run_timeout_s=300.0,
            requested_by="test",
            now=clock.now,
            idempotency_key="request-1",
        )
        assert not created
        assert again.id == vid
        assert s.query(Job).count() == 2  # install + generate, enqueued once


def test_infra_retries_the_same_repeat_and_keeps_every_attempt(engine: Engine) -> None:
    clock = Clock()
    calls: dict[tuple[int, str, int], int] = {}

    def oracle(idx: int, key: str, repeat: int) -> Outcome:
        calls[(idx, key, repeat)] = calls.get((idx, key, repeat), 0) + 1
        if (idx, key, repeat) == (0, "clean", 1) and calls[(idx, key, repeat)] == 1:
            return Outcome.INFRA  # the phone dropped off once
        return Outcome.ASSERTION if key == "clean" and repeat == 2 else Outcome.PASSED

    vid = start(engine, clock)
    drive(engine, clock, oracle, fail_g1={1, 2})
    assert calls[(0, "clean", 1)] == 2
    with session_scope(engine) as s:
        job = s.execute(select(Job).where(Job.idempotency_key.like("run:%:clean:r1"))).scalar_one()
        assert job.status == "succeeded"
        assert job.attempts == 2
        rows = (
            s.execute(select(Execution).where(Execution.repeat == 1).order_by(Execution.attempt))
            .scalars()
            .all()
        )
        assert [(r.attempt, r.outcome) for r in rows] == [
            (1, "infra"),
            (2, "infra"),
            (3, "infra"),
            (4, "passed"),
        ]
    assert validation_row(engine, vid).status == "succeeded"


def test_a_run_that_is_infra_every_attempt_dies_and_leaves_the_candidate_pending(
    engine: Engine,
) -> None:
    clock = Clock()

    def oracle(idx: int, key: str, repeat: int) -> Outcome:
        return Outcome.INFRA

    vid = start(engine, clock)
    drive(engine, clock, oracle, fail_g1={1, 2})
    with session_scope(engine) as s:
        dead = s.execute(select(Job).where(Job.status == "dead")).scalars().all()
        # every clean repeat (3 + 2 extra) died after 3 attempts with the infra detail
        assert len(dead) == 5
        assert all(j.attempts == 3 for j in dead)
        assert all(
            j.last_error == "infra on every attempt (rule_infra) device offline" for j in dead
        )
    validation = validation_row(engine, vid)
    assert validation.status == "succeeded"  # scoring ran; the candidate will stay PENDING


def test_an_agent_crash_mid_job_gives_exactly_one_completion(engine: Engine) -> None:
    clock = Clock()
    start(engine, clock)
    # run the DAG up to the first RUN_TEST
    with session_scope(engine) as s:
        for owner, types in (("worker-1", WORKER), ("agent-1", AGENT), ("worker-1", WORKER)):
            job = queue.claim(s, owner=owner, types=types, capabilities=DEVICE, now=clock.tick())
            assert job is not None
            if owner == "worker-1":
                worker_step(s, job, clock, ())
            else:
                agent_step(s, job, clock, lambda *a: Outcome.PASSED)
    with session_scope(engine) as s:
        first = queue.claim(s, owner="agent-1", types=AGENT, capabilities=DEVICE, now=clock.tick())
        assert first is not None
        job_id, old_token = first.id, first.lease_token or ""
    # the agent is killed: no heartbeat, no completion. The reaper requeues after the TTL.
    clock.tick(121)
    with session_scope(engine) as s:
        assert pipeline.expire_leases(s, clock.now) == [job_id]
    with session_scope(engine) as s:
        again = queue.claim(s, owner="agent-1", types=AGENT, capabilities=DEVICE, now=clock.tick())
        assert again is not None
        assert again.id == job_id
        new_token = again.lease_token or ""
    result = run_result(Outcome.PASSED)
    with session_scope(engine) as s:
        action = pipeline.complete_run_test(s, job_id, new_token, result, clock.tick(), 0.0)
        assert action is CompletionAction.STORE
    with session_scope(engine) as s:
        # the dead agent's late answer and a duplicate of the real one change nothing
        late = pipeline.complete_run_test(s, job_id, old_token, result, clock.tick(), 0.0)
        dup = pipeline.complete_run_test(s, job_id, new_token, result, clock.tick(), 0.0)
        assert late is CompletionAction.NO_OP
        assert dup is CompletionAction.NO_OP
    with session_scope(engine) as s:
        assert s.query(Execution).filter(Execution.job_id == job_id).count() == 1
        job = s.get_one(Job, job_id)
        assert job.status == "succeeded"
        assert job.attempts == 2
