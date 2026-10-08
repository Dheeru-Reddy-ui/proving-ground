"""Read models for builds, validations and candidates, shared by the JSON API and the dashboard.

`public=True` is the public demo view: no artifact links, and failure details reduced to one
line. Local paths (which name the device host's user) are removed from every view.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pg_api.schemas import BuildOut
from pg_db.models import (
    Bug,
    Build,
    Candidate,
    Execution,
    GenerationRun,
    Job,
    Kill,
    Spec,
    Validation,
)

_HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s'\"]+|/home/[^/\s'\"]+|/Users/[^/\s'\"]+")


def scrub(text: str | None) -> str:
    """Replace home-directory paths, which carry the host's user name."""
    return _HOME.sub("~", text or "")


def build_out(build: Build) -> BuildOut:
    return BuildOut(
        id=build.id,
        sha256=build.apk_sha256,
        label=build.label,
        locator_tag=build.locator_tag,
        source=build.source,
        patch_notes=build.patch_notes,
        has_apk=build.apk is not None,
        created_at=build.created_at,
    )


class CandidateRow(BaseModel):
    id: int
    name: str
    decision: str
    trust_score: int | None
    spec_ids: list[str]
    human_decision: str | None
    kills: list[str]


class DeadJob(BaseModel):
    id: int
    type: str
    status: str
    key: str
    attempts: int
    last_error: str


class ValidationOut(BaseModel):
    id: int
    build_id: int
    features: list[str]
    n: int
    status: str
    problems: list[str]
    requested_by: str
    created_at: datetime
    finished_at: datetime | None
    jobs: dict[str, int]  # status -> count
    decisions: dict[str, int]  # decision -> count
    generation_run_ids: list[int]
    device_seconds: float  # wall time of every RUN_TEST attempt


class ValidationDetail(ValidationOut):
    candidates: list[CandidateRow]
    dead_jobs: list[DeadJob]
    llm_cost_usd: str


class BuildDetail(BaseModel):
    build: BuildOut
    validations: list[ValidationOut]


def _runs_of(session: Session, validation_id: int) -> list[GenerationRun]:
    return list(
        session.execute(
            select(GenerationRun)
            .where(GenerationRun.validation_id == validation_id)
            .order_by(GenerationRun.id)
        ).scalars()
    )


def validation_out(session: Session, validation: Validation) -> ValidationOut:
    jobs = Counter(
        str(s)
        for s in session.execute(
            select(Job.status).where(Job.validation_id == validation.id)
        ).scalars()
    )
    runs = _runs_of(session, validation.id)
    run_ids = [r.id for r in runs]
    decisions: Counter[str] = Counter()
    if run_ids:
        decisions.update(
            str(d)
            for d in session.execute(
                select(Candidate.decision).where(Candidate.generation_run_id.in_(run_ids))
            ).scalars()
        )
    wall_ms = session.execute(
        select(func.coalesce(func.sum(Execution.wall_ms), 0))
        .join(Job, Execution.job_id == Job.id)
        .where(Job.validation_id == validation.id)
    ).scalar_one()
    return ValidationOut(
        id=validation.id,
        build_id=validation.build_id,
        features=list(validation.features),
        n=validation.n,
        status=validation.status,
        problems=list(validation.problems),
        requested_by=validation.requested_by,
        created_at=validation.created_at,
        finished_at=validation.finished_at,
        jobs=dict(jobs),
        decisions=dict(decisions),
        generation_run_ids=run_ids,
        device_seconds=round(int(wall_ms) / 1000, 1),
    )


def _kills_by_candidate(session: Session, candidate_ids: list[int]) -> dict[int, list[str]]:
    """Killed bug codes per candidate, from the latest kill row per (candidate, bug)."""
    if not candidate_ids:
        return {}
    codes = {b.id: b.code for b in session.execute(select(Bug)).scalars()}
    latest: dict[tuple[int, int], Kill] = {}
    for kill in session.execute(
        select(Kill).where(Kill.candidate_id.in_(candidate_ids)).order_by(Kill.id)
    ).scalars():
        latest[(kill.candidate_id or 0, kill.bug_id)] = kill
    result: dict[int, list[str]] = {}
    for (cid, bug_id), kill in sorted(latest.items()):
        if kill.killed:
            result.setdefault(cid, []).append(codes[bug_id])
    return result


def validation_detail(session: Session, validation: Validation) -> ValidationDetail:
    base = validation_out(session, validation)
    runs = _runs_of(session, validation.id)
    candidates = (
        list(
            session.execute(
                select(Candidate)
                .where(Candidate.generation_run_id.in_([r.id for r in runs]))
                .order_by(Candidate.id)
            ).scalars()
        )
        if runs
        else []
    )
    kills = _kills_by_candidate(session, [c.id for c in candidates])
    dead = session.execute(
        select(Job)
        .where(Job.validation_id == validation.id, Job.status.in_(("dead", "failed")))
        .order_by(Job.id)
    ).scalars()
    return ValidationDetail(
        **base.model_dump(),
        candidates=[
            CandidateRow(
                id=c.id,
                name=c.name,
                decision=c.decision,
                trust_score=c.trust_score,
                spec_ids=list(c.spec_ids),
                human_decision=c.human_decision,
                kills=kills.get(c.id, []),
            )
            for c in candidates
        ],
        dead_jobs=[
            DeadJob(
                id=j.id,
                type=j.type,
                status=j.status,
                key=j.idempotency_key,
                attempts=j.attempts,
                last_error=scrub(j.last_error),
            )
            for j in dead
        ],
        llm_cost_usd=str(sum((r.cost_usd for r in runs), start=0)),
    )


def build_detail(session: Session, build: Build) -> BuildDetail:
    validations = session.execute(
        select(Validation).where(Validation.build_id == build.id).order_by(Validation.id.desc())
    ).scalars()
    return BuildDetail(
        build=build_out(build), validations=[validation_out(session, v) for v in validations]
    )


class KillOut(BaseModel):
    bug: str
    killed: bool
    unstable: bool
    evidence_execution_ids: list[int]


class ExecutionOut(BaseModel):
    id: int
    flags: list[str]
    purpose: str
    repeat: int
    attempt: int
    outcome: str
    failure_kind: str | None
    detail: str
    duration_ms: int
    wall_ms: int
    started_at: datetime
    artifacts: list[str]  # names; fetched through /v1/executions/{id}/artifacts/{name}


class CandidateDetail(BaseModel):
    id: int
    generation_run_id: int
    name: str
    intent: str
    code: str
    spec_ids: list[str]
    specs: dict[str, str]
    pages_used: list[str]
    decision: str
    trust_score: int | None
    reasons: list[dict[str, Any]]
    gates: dict[str, Any]
    human_decision: str | None
    human_reason: str | None
    model: str
    prompt_version: str
    kills: list[KillOut]
    executions: list[ExecutionOut]


def candidate_detail(session: Session, cand: Candidate, *, public: bool) -> CandidateDetail:
    run = session.get_one(GenerationRun, cand.generation_run_id)
    specs = {
        s.spec_id: s.text
        for s in session.execute(select(Spec).where(Spec.spec_id.in_(cand.spec_ids))).scalars()
    }
    codes = {b.id: b.code for b in session.execute(select(Bug)).scalars()}
    latest: dict[int, Kill] = {}
    for kill in session.execute(
        select(Kill).where(Kill.candidate_id == cand.id).order_by(Kill.id)
    ).scalars():
        latest[kill.bug_id] = kill
    executions = session.execute(
        select(Execution).where(Execution.candidate_id == cand.id).order_by(Execution.id)
    ).scalars()
    return CandidateDetail(
        id=cand.id,
        generation_run_id=cand.generation_run_id,
        name=cand.name,
        intent=cand.intent,
        code=cand.code,
        spec_ids=list(cand.spec_ids),
        specs=specs,
        pages_used=list(cand.pages_used),
        decision=cand.decision,
        trust_score=cand.trust_score,
        reasons=list(cand.reasons),
        gates=dict(cand.gates),
        human_decision=cand.human_decision,
        human_reason=None if public else cand.human_reason,
        model=run.model,
        prompt_version=run.prompt_version,
        kills=[
            KillOut(
                bug=codes[k.bug_id],
                killed=k.killed,
                unstable=k.unstable,
                evidence_execution_ids=list(k.evidence_execution_ids),
            )
            for _, k in sorted(latest.items(), key=lambda item: codes[item[0]])
        ],
        executions=[_execution_out(e, public=public) for e in executions],
    )


def _execution_out(execution: Execution, *, public: bool) -> ExecutionOut:
    detail = scrub(execution.detail)
    if public:
        lines = detail.strip().splitlines()
        detail = lines[-1][:200] if lines else ""
    keys = (execution.artifacts or {}).get("keys") or {}
    return ExecutionOut(
        id=execution.id,
        flags=list(execution.flags),
        purpose=execution.purpose,
        repeat=execution.repeat,
        attempt=execution.attempt,
        outcome=execution.outcome,
        failure_kind=execution.failure_kind,
        detail=detail,
        duration_ms=execution.duration_ms,
        wall_ms=execution.wall_ms,
        started_at=execution.started_at,
        artifacts=[] if public else sorted(keys),
    )
