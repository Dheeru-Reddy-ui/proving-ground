"""The build-validation DAG (ADR-0006), planned by a pure function.

    INSTALL_BUILD → GENERATE (per feature) → SCORE static → RUN_TEST clean x3
                  → RUN_TEST dev bugs x2 each → SCORE final

`plan_validation` takes a snapshot of one validation (its jobs and what they found) and returns
the jobs that have become possible plus the validation's status. It makes the same runs
`pg prove` makes, because it asks the same gate rules:

- clean runs go one repeat at a time while `determinism_needs_more` (a failure stops them);
- bug runs start only after G2 passed, only for relevant **dev** bugs, one repeat at a time
  while `detection_needs_more` (the second run is skipped once a kill is impossible);
- a repeat whose job died (infra every attempt) counts as an infra run, and, as in `pg prove`,
  up to `EXTRA_REPEATS` more repeats may replace such runs;
- SCORE final runs once no candidate of the generation run has anything left to run.

Every job carries an idempotency key that names its work, so planning twice never duplicates.
The holdout split never reaches this module: the snapshot holds dev bugs only.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from pg_core.gates.base import Execution, Outcome
from pg_core.gates.runs import (
    BUG_RUNS,
    CLEAN_RUNS,
    BugRef,
    check_determinism,
    detection_needs_more,
    determinism_needs_more,
    relevant_bugs,
)
from pg_core.jobs import JobSpec, JobStatus, JobType

EXTRA_REPEATS = 2  # as `pg prove`: repeats that may replace runs which ended without a result
CLEAN = "clean"  # the run key of clean runs; bug runs are keyed by their flag
DEVICE_REQUIRES = {"platform": "android"}
PRIORITY_INSTALL = 100


class ValidationStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"  # every feature finished; at least one was scored
    FAILED = "failed"  # the build could not be installed, or no feature could be scored


class RunJob(BaseModel):
    """One RUN_TEST job of a candidate (one repeat of one flag set)."""

    model_config = ConfigDict(frozen=True)

    repeat: int = Field(ge=1)
    status: JobStatus
    outcome: Outcome | None = None  # the final outcome, once the job succeeded
    duration_s: float = Field(default=0.0, ge=0)

    def as_execution(self) -> Execution | None:
        """What the gates see: the outcome once finished; None while pending. A job that
        failed or died produced no valid run, which the gates treat like an infra run."""
        if self.status is JobStatus.SUCCEEDED and self.outcome is not None:
            return Execution(outcome=self.outcome, duration_s=self.duration_s)
        if self.status.is_final:
            return Execution(outcome=Outcome.INFRA, duration_s=0.0)
        return None


class CandidateState(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    code_sha: str
    g1_passed: bool | None = None  # None until SCORE static ran
    pages_used: tuple[str, ...] = ()
    runs: dict[str, tuple[RunJob, ...]] = Field(default_factory=dict)  # CLEAN or a flag


class FeatureState(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature: str
    generate: JobStatus | None = None
    generation_run_id: int | None = None
    score_static: JobStatus | None = None
    score_final: JobStatus | None = None
    candidates: tuple[CandidateState, ...] = ()


class ValidationSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    validation_id: int
    build_sha: str
    locator_tag: str
    features: tuple[str, ...]
    n: int = Field(ge=1)
    seed: int | None = None
    manifest_sha: str
    run_timeout_s: float = Field(gt=0)
    install: JobStatus | None = None
    feature_states: dict[str, FeatureState] = Field(default_factory=dict)
    dev_bugs: tuple[BugRef, ...] = ()


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True)

    jobs: tuple[JobSpec, ...]
    status: ValidationStatus
    problems: tuple[str, ...] = ()  # why a feature or the validation failed


def run_group(generation_run_id: int) -> str:
    """Executions of a validation's runs are grouped like `pg prove`'s, so reports match."""
    return f"prove-{generation_run_id}"


def install_key(validation_id: int) -> str:
    return f"validation:{validation_id}:install"


def generate_key(validation_id: int, feature: str) -> str:
    return f"validation:{validation_id}:generate:{feature}"


def score_key(validation_id: int, generation_run_id: int, stage: str) -> str:
    return f"validation:{validation_id}:score-{stage}:{generation_run_id}"


def run_key(generation_run_id: int, candidate_id: int, key: str, repeat: int) -> str:
    return f"run:{run_group(generation_run_id)}:c{candidate_id}:{key}:r{repeat}"


def run_key_of(flags: Sequence[str]) -> str:
    """The run key of a RUN_TEST job's flags: CLEAN, or its one bug flag."""
    return flags[0] if flags else CLEAN


def install_job(s: ValidationSnapshot) -> JobSpec:
    return JobSpec(
        type=JobType.INSTALL_BUILD,
        idempotency_key=install_key(s.validation_id),
        payload={
            "validation_id": s.validation_id,
            "build_sha": s.build_sha,
            "locator_tag": s.locator_tag,
        },
        requires=DEVICE_REQUIRES,
        priority=PRIORITY_INSTALL,
    )


def generate_job(s: ValidationSnapshot, feature: str) -> JobSpec:
    return JobSpec(
        type=JobType.GENERATE,
        idempotency_key=generate_key(s.validation_id, feature),
        payload={"validation_id": s.validation_id, "feature": feature, "n": s.n, "seed": s.seed},
    )


def score_job(s: ValidationSnapshot, generation_run_id: int, stage: str) -> JobSpec:
    return JobSpec(
        type=JobType.SCORE,
        idempotency_key=score_key(s.validation_id, generation_run_id, stage),
        payload={
            "validation_id": s.validation_id,
            "generation_run_id": generation_run_id,
            "stage": stage,
        },
    )


def run_job(
    s: ValidationSnapshot, generation_run_id: int, candidate: CandidateState, key: str, repeat: int
) -> JobSpec:
    group = run_group(generation_run_id)
    flags = [] if key == CLEAN else [key]
    return JobSpec(
        type=JobType.RUN_TEST,
        idempotency_key=run_key(generation_run_id, candidate.id, key, repeat),
        payload={
            "validation_id": s.validation_id,
            "generation_run_id": generation_run_id,
            "run_group": group,
            "candidate_id": candidate.id,
            "code_sha": candidate.code_sha,
            "flags": flags,
            "purpose": "clean" if key == CLEAN else "bug",
            "repeat": repeat,
            "build_sha": s.build_sha,
            "locator_tag": s.locator_tag,
            "manifest_sha": s.manifest_sha,
            "timeout_s": s.run_timeout_s,
        },
        requires=DEVICE_REQUIRES,
    )


class _Runs(BaseModel):
    model_config = ConfigDict(frozen=True)

    executions: tuple[Execution, ...]
    pending: bool
    next_repeat: int


def _runs(candidate: CandidateState, key: str) -> _Runs:
    jobs = sorted(candidate.runs.get(key, ()), key=lambda j: j.repeat)
    executions = [e for j in jobs if (e := j.as_execution()) is not None]
    return _Runs(
        executions=tuple(executions),
        pending=any(not j.status.is_final for j in jobs),
        next_repeat=max((j.repeat for j in jobs), default=0) + 1,
    )


def plan_candidate(
    s: ValidationSnapshot, generation_run_id: int, candidate: CandidateState, device_ready: bool
) -> tuple[list[JobSpec], bool]:
    """(jobs to enqueue, settled). Settled means nothing is left to run for this candidate:
    its gates are decided or out of repeats."""
    clean = _runs(candidate, CLEAN)
    if clean.pending:
        return [], False
    if determinism_needs_more(clean.executions):
        if clean.next_repeat > CLEAN_RUNS + EXTRA_REPEATS:
            return [], True  # G2 stays inconclusive: the candidate will be PENDING
        if not device_ready:
            return [], False
        return [run_job(s, generation_run_id, candidate, CLEAN, clean.next_repeat)], False
    if not check_determinism(clean.executions).passed:
        return [], True  # G2 failed: no bug runs (as `pg prove`)
    relevant, _ = relevant_bugs(candidate.pages_used, s.dev_bugs)
    jobs: list[JobSpec] = []
    settled = True
    for bug in relevant:
        runs = _runs(candidate, bug.flag)
        if runs.pending:
            settled = False
            continue
        if not detection_needs_more(runs.executions):
            continue
        if runs.next_repeat > BUG_RUNS + EXTRA_REPEATS:
            continue  # G3 stays inconclusive for this bug
        settled = False
        if device_ready:
            jobs.append(run_job(s, generation_run_id, candidate, bug.flag, runs.next_repeat))
    return jobs, settled


class _FeaturePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    jobs: tuple[JobSpec, ...] = ()
    done: bool = False
    scored: bool = False
    problem: str | None = None


def _failed(feature: str, stage: str, status: JobStatus) -> _FeaturePlan:
    return _FeaturePlan(done=True, problem=f"{feature}: {stage} {status.value}")


def plan_feature(s: ValidationSnapshot, state: FeatureState, device_ready: bool) -> _FeaturePlan:
    feature = state.feature
    if state.generate is None:
        return _FeaturePlan(jobs=(generate_job(s, feature),))
    if state.generate in (JobStatus.FAILED, JobStatus.DEAD):
        return _failed(feature, "GENERATE", state.generate)
    if state.generate is not JobStatus.SUCCEEDED:
        return _FeaturePlan()
    run_id = state.generation_run_id
    if run_id is None:
        return _FeaturePlan(done=True, problem=f"{feature}: GENERATE left no generation run")
    if state.score_static is None:
        return _FeaturePlan(jobs=(score_job(s, run_id, "static"),))
    if state.score_static in (JobStatus.FAILED, JobStatus.DEAD):
        return _failed(feature, "SCORE static", state.score_static)
    if state.score_static is not JobStatus.SUCCEEDED:
        return _FeaturePlan()
    jobs: list[JobSpec] = []
    settled = True
    for candidate in state.candidates:
        if not candidate.g1_passed:
            continue  # rejected by G1 (or never scored): nothing to run
        new, done = plan_candidate(s, run_id, candidate, device_ready)
        jobs += new
        settled = settled and done
    if not settled:
        return _FeaturePlan(jobs=tuple(jobs))
    if state.score_final is None:
        return _FeaturePlan(jobs=(score_job(s, run_id, "final"),))
    if state.score_final in (JobStatus.FAILED, JobStatus.DEAD):
        return _failed(feature, "SCORE final", state.score_final)
    if state.score_final is not JobStatus.SUCCEEDED:
        return _FeaturePlan()
    return _FeaturePlan(done=True, scored=True)


def plan_validation(s: ValidationSnapshot) -> Plan:
    """The jobs that have become possible and the validation's status."""
    jobs: list[JobSpec] = []
    problems: list[str] = []
    if s.install is None:
        jobs.append(install_job(s))
    install_failed = s.install in (JobStatus.FAILED, JobStatus.DEAD)
    device_ready = s.install is JobStatus.SUCCEEDED
    plans = [
        plan_feature(s, s.feature_states.get(f) or FeatureState(feature=f), device_ready)
        for f in s.features
    ]
    for plan in plans:
        jobs += plan.jobs
        if plan.problem:
            problems.append(plan.problem)
    if install_failed:
        status = ValidationStatus.FAILED
        problems.insert(0, f"INSTALL_BUILD {s.install}: build {s.build_sha[:12]} not installed")
        jobs = [j for j in jobs if j.type is not JobType.RUN_TEST]
    elif all(p.done for p in plans):
        scored = any(p.scored for p in plans)
        status = ValidationStatus.SUCCEEDED if scored else ValidationStatus.FAILED
    else:
        status = ValidationStatus.RUNNING
    return Plan(jobs=_unique(jobs), status=status, problems=tuple(problems))


def _unique(jobs: Sequence[JobSpec]) -> tuple[JobSpec, ...]:
    seen: dict[str, JobSpec] = {}
    for job in jobs:
        seen.setdefault(job.idempotency_key, job)
    return tuple(seen.values())
