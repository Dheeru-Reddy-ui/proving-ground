"""Build validations on top of the job queue (ADR-0006).

Every change to a validation's jobs (completion, failure, lease expiry) calls `advance` in the
same transaction: it locks the validation row, loads a snapshot, asks the pure planner
(`pg_core.orchestrator`) what has become possible and enqueues it. Results, their follow-up jobs
and the validation's status are therefore committed together or not at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pg_core.gates.runs import BugRef
from pg_core.job_results import RunTestResult, run_outcome
from pg_core.jobs import CompletionAction, JobStatus, JobType, Transition, completion_action
from pg_core.jobs import canonical_sha as result_sha
from pg_core.orchestrator import (
    CandidateState,
    FeatureState,
    Plan,
    RunJob,
    ValidationSnapshot,
    ValidationStatus,
    generate_key,
    install_key,
    plan_validation,
    run_key_of,
    score_key,
)
from pg_db import jobs as queue
from pg_db.models import Bug, Build, Candidate, Execution, GenerationRun, Job, Validation


class PipelineError(RuntimeError):
    """A request that cannot apply (unknown job, wrong job type)."""


# --- validations --------------------------------------------------------------------------


def create_validation(
    session: Session,
    *,
    build_id: int,
    features: Sequence[str],
    n: int,
    seed: int | None,
    manifest_sha: str,
    run_timeout_s: float,
    requested_by: str,
    now: datetime,
    idempotency_key: str | None = None,
) -> tuple[Validation, bool]:
    """Create a validation and enqueue its first jobs; with an idempotency key, a repeat of the
    same request returns the existing validation (False)."""
    statement = (
        insert(Validation)
        .values(
            build_id=build_id,
            features=list(features),
            n=n,
            seed=seed,
            manifest_sha=manifest_sha,
            run_timeout_s=run_timeout_s,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            status=ValidationStatus.RUNNING.value,
            problems=[],
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(Validation.id)
    )
    new_id = session.execute(statement).scalar_one_or_none()
    if new_id is None:
        existing = session.execute(
            select(Validation).where(Validation.idempotency_key == idempotency_key)
        ).scalar_one()
        return existing, False
    advance(session, new_id, now)
    return session.get_one(Validation, new_id), True


def dev_bug_refs(session: Session) -> tuple[BugRef, ...]:
    """Dev bugs only: the holdout split never reaches the planner (ADR-0004)."""
    rows = session.execute(select(Bug).where(Bug.split == "dev").order_by(Bug.code)).scalars()
    return tuple(BugRef(id=b.code, flag=b.flag, pages=tuple(b.pages)) for b in rows)


def _status(job: Job | None) -> JobStatus | None:
    return None if job is None else JobStatus(job.status)


def load_snapshot(session: Session, validation: Validation) -> ValidationSnapshot:
    build = session.get_one(Build, validation.build_id)
    jobs = queue.of_validation(session, validation.id)
    by_key = {j.idempotency_key: j for j in jobs}
    runs: dict[int, dict[str, list[RunJob]]] = {}
    for job in jobs:
        if job.type != JobType.RUN_TEST.value:
            continue
        payload = job.payload
        outcome = run_outcome(job.result) if job.status == JobStatus.SUCCEEDED.value else None
        run = RunJob(
            repeat=int(payload["repeat"]),
            status=JobStatus(job.status),
            outcome=outcome[0] if outcome else None,
            duration_s=outcome[1] if outcome else 0.0,
        )
        key = run_key_of(list(payload.get("flags") or []))
        runs.setdefault(int(payload["candidate_id"]), {}).setdefault(key, []).append(run)
    features: dict[str, FeatureState] = {}
    for feature in validation.features:
        gen_job = by_key.get(generate_key(validation.id, feature))
        gen_run = session.execute(
            select(GenerationRun).where(
                GenerationRun.job_key == generate_key(validation.id, feature)
            )
        ).scalar_one_or_none()
        candidates: list[CandidateState] = []
        static = final = None
        if gen_run is not None:
            static = by_key.get(score_key(validation.id, gen_run.id, "static"))
            final = by_key.get(score_key(validation.id, gen_run.id, "final"))
            rows = session.execute(
                select(Candidate)
                .where(Candidate.generation_run_id == gen_run.id)
                .order_by(Candidate.id)
            ).scalars()
            for cand in rows:
                g1 = (cand.gates or {}).get("G1")
                candidates.append(
                    CandidateState(
                        id=cand.id,
                        code_sha=cand.code_sha,
                        g1_passed=None if g1 is None else bool(g1.get("passed")),
                        pages_used=tuple(cand.pages_used),
                        runs={k: tuple(v) for k, v in runs.get(cand.id, {}).items()},
                    )
                )
        features[feature] = FeatureState(
            feature=feature,
            generate=_status(gen_job),
            generation_run_id=gen_run.id if gen_run is not None else None,
            score_static=_status(static),
            score_final=_status(final),
            candidates=tuple(candidates),
        )
    return ValidationSnapshot(
        validation_id=validation.id,
        build_sha=build.apk_sha256,
        locator_tag=build.locator_tag,
        features=tuple(validation.features),
        n=validation.n,
        seed=validation.seed,
        manifest_sha=validation.manifest_sha,
        run_timeout_s=validation.run_timeout_s,
        install=_status(by_key.get(install_key(validation.id))),
        feature_states=features,
        dev_bugs=dev_bug_refs(session),
    )


def advance(session: Session, validation_id: int, now: datetime) -> Plan:
    """Enqueue whatever has become possible and update the validation's status."""
    validation = session.get(Validation, validation_id, with_for_update=True)
    if validation is None:
        raise PipelineError(f"no validation {validation_id}")
    session.flush()  # the snapshot must see this transaction's job changes
    plan = plan_validation(load_snapshot(session, validation))
    queue.enqueue(session, plan.jobs, validation_id=validation.id, now=now)
    validation.status = plan.status.value
    validation.problems = list(plan.problems)
    if plan.status is not ValidationStatus.RUNNING and validation.finished_at is None:
        validation.finished_at = now
    return plan


def _advance_for(session: Session, job: Job, now: datetime) -> None:
    if job.validation_id is not None:
        advance(session, job.validation_id, now)


# --- job events that move a validation ----------------------------------------------------


def complete(
    session: Session, job_id: int, token: str, result: Mapping[str, Any], now: datetime
) -> CompletionAction:
    """Store a job's result (once) and advance its validation. For GENERATE and SCORE the
    caller writes the result's domain rows in the same transaction, after `holds_lease`."""
    job = queue.locked(session, job_id)
    if job is None:
        return CompletionAction.STALE
    if job.type == JobType.RUN_TEST.value:
        raise PipelineError("RUN_TEST results go through complete_run_test")
    action = queue.record_completion(job, token, result, now)
    if action is CompletionAction.STORE:
        _advance_for(session, job, now)
    return action


def fail(
    session: Session,
    job_id: int,
    token: str,
    *,
    error: str,
    retryable: bool,
    now: datetime,
    jitter: float,
) -> Transition | None:
    """A claimer reports a failure; None when its lease is not current."""
    job = queue.locked(session, job_id)
    if job is None:
        return None
    transition = queue.record_failure(
        job, token, error=error, retryable=retryable, now=now, jitter=jitter
    )
    if transition is not None:
        _advance_for(session, job, now)
    return transition


def expire_leases(session: Session, now: datetime) -> list[int]:
    """Reaper: return expired leases to the queue (or dead) and advance their validations."""
    jobs = queue.expire_leases(session, now)
    for validation_id in sorted({j.validation_id for j in jobs if j.validation_id is not None}):
        advance(session, validation_id, now)
    return [j.id for j in jobs]


def holds_lease(session: Session, job_id: int, token: str) -> Job | None:
    """The locked job when `token` is its current lease, else None. Workers call this before
    writing a result's domain rows, so a worker whose lease expired writes nothing."""
    job = queue.locked(session, job_id)
    if job is None or job.status != JobStatus.LEASED.value or job.lease_token != token:
        return None
    return job


# --- RUN_TEST results ---------------------------------------------------------------------


def complete_run_test(
    session: Session,
    job_id: int,
    token: str,
    result: RunTestResult,
    now: datetime,
    jitter: float,
) -> CompletionAction:
    """Store every attempt of a RUN_TEST job as an execution, then either complete the job or,
    when the final attempt was infra, fail it (retryable): infra is never a test result
    (ADR-0004), so the same repeat is tried again until the job is dead."""
    job = queue.locked(session, job_id)
    if job is None:
        return CompletionAction.STALE
    if job.type != JobType.RUN_TEST.value:
        raise PipelineError(f"job {job_id} is {job.type}, not RUN_TEST")
    stored = result.model_dump(mode="json")
    action = completion_action(
        status=JobStatus(job.status),
        current_token=job.lease_token,
        current_result_sha=job.result_sha,
        token=token,
        result_sha=result_sha(stored),
    )
    if action is not CompletionAction.STORE:
        return action
    _store_executions(session, job, result)
    final = result.final
    if final.outcome.is_valid:
        queue.record_completion(job, token, stored, now)
    else:
        detail = final.detail.strip().splitlines()[-1] if final.detail.strip() else ""
        queue.record_failure(
            job,
            token,
            error=f"infra on every attempt ({final.rule}) {detail}".strip(),
            retryable=True,
            now=now,
            jitter=jitter,
        )
    _advance_for(session, job, now)
    return action


def _store_executions(session: Session, job: Job, result: RunTestResult) -> None:
    payload = job.payload
    build = session.execute(
        select(Build).where(Build.apk_sha256 == payload["build_sha"])
    ).scalar_one()
    flags = sorted(payload.get("flags") or [])
    subject = {"candidate_id": int(payload["candidate_id"])}
    previous = session.execute(
        select(func.max(Execution.attempt)).where(
            Execution.run_group == payload["run_group"],
            Execution.candidate_id == subject["candidate_id"],
            Execution.flags == flags,
            Execution.repeat == int(payload["repeat"]),
        )
    ).scalar_one()
    offset = int(previous or 0)  # a retried job keeps the earlier attempts as evidence
    for report in result.attempts:
        session.execute(
            insert(Execution)
            .values(
                **subject,
                build_id=build.id,
                run_group=payload["run_group"],
                flags=flags,
                purpose=payload["purpose"],
                repeat=int(payload["repeat"]),
                attempt=offset + report.attempt,
                outcome=report.outcome.value,
                failure_kind=report.rule,
                detail=report.detail or None,
                duration_ms=int(report.duration_s * 1000),
                wall_ms=int(report.wall_s * 1000),
                artifacts={
                    "keys": dict(report.artifacts),
                    "game_errors": list(report.game_errors),
                    "pgtelem_lines": report.pgtelem_lines,
                    "actions": list(report.actions),
                },
                started_at=result.started_at,
                finished_at=result.finished_at,
                job_id=job.id,
            )
            .on_conflict_do_nothing(constraint="executions_idempotent")
        )
