"""Read models for the dashboard pages. Aggregates are grouped queries, not one query per row.

The kill matrix covers dev bugs only: holdout bugs are never run before Phase 4 (ADR-0004).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pg_core.orchestrator import run_group
from pg_db.models import (
    Agent,
    Bug,
    Build,
    Candidate,
    Execution,
    GenerationRun,
    Job,
    Kill,
    LlmCall,
    Validation,
    Worker,
)

STALE_AGENT = timedelta(minutes=10)
DECISIONS = ("accept", "review", "reject", "pending")


class Money(BaseModel):
    llm_usd: Decimal
    device_minutes: float


class BuildRow(BaseModel):
    id: int
    label: str
    sha256: str
    source: str
    created_at: datetime
    decisions: dict[str, int]
    latest_validation: str | None
    llm_usd: Decimal
    device_minutes: float


def build_rows(s: Session, limit: int = 50) -> list[BuildRow]:
    builds = list(s.execute(select(Build).order_by(Build.id.desc()).limit(limit)).scalars())
    ids = [b.id for b in builds]
    decisions: dict[int, Counter[str]] = defaultdict(Counter)
    for build_id, decision, count in s.execute(
        select(GenerationRun.build_id, Candidate.decision, func.count())
        .join(Candidate, Candidate.generation_run_id == GenerationRun.id)
        .where(GenerationRun.build_id.in_(ids))
        .group_by(GenerationRun.build_id, Candidate.decision)
    ):
        decisions[build_id][decision] = count
    # .all(): dict() of a Result itself would treat it as a mapping (it has .keys())
    llm: dict[int, Any] = dict(
        s.execute(
            select(GenerationRun.build_id, func.sum(GenerationRun.cost_usd))
            .where(GenerationRun.build_id.in_(ids))
            .group_by(GenerationRun.build_id)
        ).all()
    )
    device: dict[int, Any] = dict(
        s.execute(
            select(Execution.build_id, func.sum(Execution.wall_ms))
            .where(Execution.build_id.in_(ids))
            .group_by(Execution.build_id)
        ).all()
    )
    latest: dict[int, str] = {}
    for build_id, status in s.execute(
        select(Validation.build_id, Validation.status)
        .where(Validation.build_id.in_(ids))
        .order_by(Validation.id)
    ):
        latest[build_id] = status
    return [
        BuildRow(
            id=b.id,
            label=b.label,
            sha256=b.apk_sha256,
            source=b.source,
            created_at=b.created_at,
            decisions={d: decisions[b.id].get(d, 0) for d in DECISIONS},
            latest_validation=latest.get(b.id),
            llm_usd=Decimal(llm.get(b.id) or 0),
            device_minutes=round(int(device.get(b.id) or 0) / 60000, 1),
        )
        for b in builds
    ]


class RunSummary(BaseModel):
    id: int
    feature: str
    model: str
    prompt_version: str
    status: str
    started_at: datetime
    validation_id: int | None
    decisions: dict[str, int]
    candidates: int
    g2_passed: int
    g2_decided: int
    llm_usd: Decimal
    tokens_in: int
    tokens_out: int
    device_minutes: float
    attempts: int
    infra_attempts: int

    @property
    def accepted(self) -> int:
        return self.decisions.get("accept", 0)


def run_summaries(s: Session, runs: list[GenerationRun]) -> list[RunSummary]:
    ids = [r.id for r in runs]
    cands = list(s.execute(select(Candidate).where(Candidate.generation_run_id.in_(ids))).scalars())
    by_run: dict[int, list[Candidate]] = defaultdict(list)
    for c in cands:
        by_run[c.generation_run_id].append(c)
    groups = {run_group(i): i for i in ids}
    device: dict[int, tuple[int, int, int]] = {}
    for group, wall, attempts, infra in s.execute(
        select(
            Execution.run_group,
            func.sum(Execution.wall_ms),
            func.count(),
            func.count().filter(Execution.outcome == "infra"),
        )
        .where(Execution.run_group.in_(list(groups)))
        .group_by(Execution.run_group)
    ):
        device[groups[group]] = (int(wall or 0), int(attempts), int(infra))
    out = []
    for r in runs:
        rc = by_run.get(r.id, [])
        g2: list[dict[str, Any]] = [c.gates["G2"] for c in rc if c.gates.get("G2")]
        wall, attempts, infra = device.get(r.id, (0, 0, 0))
        out.append(
            RunSummary(
                id=r.id,
                feature=r.feature,
                model=r.model,
                prompt_version=r.prompt_version,
                status=r.status,
                started_at=r.started_at,
                validation_id=r.validation_id,
                decisions={d: sum(c.decision == d for c in rc) for d in DECISIONS},
                candidates=len(rc),
                g2_passed=sum(bool(g.get("passed")) for g in g2),
                g2_decided=sum(not g.get("inconclusive") for g in g2),
                llm_usd=r.cost_usd,
                tokens_in=r.tokens_in,
                tokens_out=r.tokens_out,
                device_minutes=round(wall / 60000, 1),
                attempts=attempts,
                infra_attempts=infra,
            )
        )
    return out


def runs_of_build(s: Session, build_id: int) -> list[RunSummary]:
    runs = list(
        s.execute(
            select(GenerationRun)
            .where(GenerationRun.build_id == build_id)
            .order_by(GenerationRun.id.desc())
        ).scalars()
    )
    return run_summaries(s, runs)


class MatrixRow(BaseModel):
    candidate_id: int
    name: str
    decision: str
    note: str | None  # why it has no bug runs
    cells: dict[str, str]  # bug code -> killed | unstable | survived


class KillMatrix(BaseModel):
    bugs: list[str]
    rows: list[MatrixRow]


def kill_matrix(s: Session, generation_run_id: int) -> KillMatrix:
    cands = list(
        s.execute(
            select(Candidate)
            .where(Candidate.generation_run_id == generation_run_id)
            .order_by(Candidate.id)
        ).scalars()
    )
    dev = {b.id: b.code for b in s.execute(select(Bug).where(Bug.split == "dev")).scalars()}
    latest: dict[tuple[int, int], Kill] = {}
    for kill in s.execute(
        select(Kill).where(Kill.run_group == run_group(generation_run_id)).order_by(Kill.id)
    ).scalars():
        if kill.candidate_id is not None and kill.bug_id in dev:
            latest[(kill.candidate_id, kill.bug_id)] = kill
    bugs = sorted({dev[b] for _, b in latest})
    rows = []
    for c in cands:
        cells = {}
        for (cid, bug_id), kill in latest.items():
            if cid == c.id:
                state = "killed" if kill.killed else "unstable" if kill.unstable else "survived"
                cells[dev[bug_id]] = state
        note = None
        gates = c.gates or {}
        if gates.get("G1") and not gates["G1"].get("passed"):
            note = "rejected by G1: never run"
        elif gates.get("G2") and not gates["G2"].get("passed"):
            note = "failed G2: no bug runs"
        elif not cells:
            note = "no bug runs"
        rows.append(
            MatrixRow(candidate_id=c.id, name=c.name, decision=c.decision, note=note, cells=cells)
        )
    return KillMatrix(bugs=bugs, rows=rows)


class ReviewItem(BaseModel):
    id: int
    name: str
    intent: str
    spec_ids: list[str]
    trust_score: int | None
    reasons: list[dict[str, Any]]
    generation_run_id: int
    feature: str
    human_decision: str | None
    human_reason: str | None
    human_decided_at: datetime | None


def review_items(s: Session) -> tuple[list[ReviewItem], list[ReviewItem]]:
    """(waiting for a human, decided by a human), newest first."""
    rows = s.execute(
        select(Candidate, GenerationRun.feature)
        .join(GenerationRun, GenerationRun.id == Candidate.generation_run_id)
        .where(Candidate.decision == "review")
        .order_by(Candidate.id.desc())
    ).all()
    items = [
        ReviewItem(
            id=c.id,
            name=c.name,
            intent=c.intent,
            spec_ids=list(c.spec_ids),
            trust_score=c.trust_score,
            reasons=list(c.reasons),
            generation_run_id=c.generation_run_id,
            feature=feature,
            human_decision=c.human_decision,
            human_reason=c.human_reason,
            human_decided_at=c.human_decided_at,
        )
        for c, feature in rows
    ]
    return [i for i in items if i.human_decision is None], [
        i for i in items if i.human_decision is not None
    ]


class AgentRow(BaseModel):
    name: str
    last_seen_at: datetime | None
    stale: bool
    healthy: bool | None
    failing: list[str]
    revoked: bool
    current_job_id: int | None


class WorkerRow(BaseModel):
    name: str
    last_seen_at: datetime
    stale: bool
    generation_enabled: bool


class DeadJob(BaseModel):
    id: int
    type: str
    status: str
    key: str
    attempts: int
    last_error: str
    finished_at: datetime | None


class SystemStatus(BaseModel):
    agents: list[AgentRow]
    workers: list[WorkerRow]
    queue: dict[str, dict[str, int]]  # type -> status -> count
    dead: list[DeadJob]
    spend_today_usd: Decimal
    calls_today: int


def system_status(s: Session, now: datetime) -> SystemStatus:
    agents = []
    for a in s.execute(select(Agent).order_by(Agent.name)).scalars():
        health = a.health or {}
        agents.append(
            AgentRow(
                name=a.name,
                last_seen_at=a.last_seen_at,
                stale=a.last_seen_at is None or now - a.last_seen_at > STALE_AGENT,
                healthy=health.get("healthy"),
                failing=[c["name"] for c in health.get("checks", []) if not c.get("ok")],
                revoked=a.revoked_at is not None,
                current_job_id=health.get("current_job_id"),
            )
        )
    workers = [
        WorkerRow(
            name=w.name,
            last_seen_at=w.last_seen_at,
            stale=now - w.last_seen_at > STALE_AGENT,
            generation_enabled=w.generation_enabled,
        )
        for w in s.execute(select(Worker).order_by(Worker.name)).scalars()
    ]
    queue: dict[str, dict[str, int]] = defaultdict(dict)
    for kind, status, count in s.execute(
        select(Job.type, Job.status, func.count()).group_by(Job.type, Job.status)
    ):
        queue[kind][status] = count
    dead = [
        DeadJob(
            id=j.id,
            type=j.type,
            status=j.status,
            key=j.idempotency_key,
            attempts=j.attempts,
            last_error=j.last_error or "",
            finished_at=j.finished_at,
        )
        for j in s.execute(
            select(Job).where(Job.status.in_(("dead", "failed"))).order_by(Job.id.desc()).limit(50)
        ).scalars()
    ]
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spend, calls = s.execute(
        select(func.coalesce(func.sum(LlmCall.cost_usd), 0), func.count()).where(
            LlmCall.created_at >= day_start
        )
    ).one()
    return SystemStatus(
        agents=agents,
        workers=workers,
        queue=dict(queue),
        dead=dead,
        spend_today_usd=Decimal(spend),
        calls_today=int(calls),
    )


class LlmTrace(BaseModel):
    call_index: int
    status: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int
    attempts: int
    finish_reason: str | None
    error: str | None
    created_at: datetime


def llm_trace(s: Session, generation_run_id: int) -> list[LlmTrace]:
    """The run's model calls: `llm_calls` rows (Phase 2), else the run's stored call log."""
    rows = list(
        s.execute(
            select(LlmCall)
            .where(LlmCall.generation_run_id == generation_run_id)
            .order_by(LlmCall.id)
        ).scalars()
    )
    if rows:
        return [
            LlmTrace(
                call_index=r.call_index,
                status=r.status,
                model=r.model,
                tokens_in=r.tokens_in,
                tokens_out=r.tokens_out,
                cost_usd=r.cost_usd,
                latency_ms=r.latency_ms,
                attempts=r.attempts,
                finish_reason=r.finish_reason,
                error=r.error,
                created_at=r.created_at,
            )
            for r in rows
        ]
    run = s.get_one(GenerationRun, generation_run_id)
    return [
        LlmTrace(
            call_index=i,
            status="error" if c.get("error") else "ok",
            model=str(c.get("model", run.model)),
            tokens_in=int(c.get("tokens_in", 0)),
            tokens_out=int(c.get("tokens_out", 0)),
            cost_usd=Decimal(str(c.get("cost_usd", 0))),
            latency_ms=int(c.get("latency_ms", 0)),
            attempts=int(c.get("attempts", 1)),
            finish_reason=c.get("finish_reason"),
            error=c.get("error"),
            created_at=run.started_at,
        )
        for i, c in enumerate(run.llm_calls or [])
    ]
