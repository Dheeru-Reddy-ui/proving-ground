"""`/metrics` in the Prometheus text format, computed from the tables on each request (ADR-0010:
nothing scrapes on the free tier, but any scraper or `curl` can read it). Admin only."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from pg_api import auth
from pg_api.state import AppState, app_state
from pg_dashboard.queries import system_status
from pg_db.models import Validation
from pg_db.session import session_scope

router = APIRouter(tags=["metrics"])
State = Annotated[AppState, Depends(app_state)]


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _metric(
    name: str, kind: str, help_text: str, samples: Iterable[tuple[dict[str, str], float]]
) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
    for labels, value in samples:
        rendered = ",".join(f'{k}="{_label(v)}"' for k, v in sorted(labels.items()))
        lines.append(f"{name}{{{rendered}}} {value:g}" if rendered else f"{name} {value:g}")
    return lines


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request, state: State) -> PlainTextResponse:
    now = state.now()
    with session_scope(state.engine) as s:
        auth.require_admin(s, request, now)
        status = system_status(s, now)
        validations = s.execute(
            select(Validation.status, func.count()).group_by(Validation.status)
        ).all()
    lines: list[str] = []
    lines += _metric(
        "pg_jobs",
        "gauge",
        "Jobs by type and status.",
        (
            ({"type": kind, "status": st}, float(n))
            for kind, counts in status.queue.items()
            for st, n in counts.items()
        ),
    )
    lines += _metric(
        "pg_validations",
        "gauge",
        "Build validations by status.",
        (({"status": str(st)}, float(n)) for st, n in validations),
    )
    lines += _metric(
        "pg_agent_seconds_since_seen",
        "gauge",
        "Seconds since each device agent last called the API.",
        (
            ({"agent": a.name}, (now - a.last_seen_at).total_seconds())
            for a in status.agents
            if a.last_seen_at is not None
        ),
    )
    lines += _metric(
        "pg_agent_healthy",
        "gauge",
        "1 when the agent's last self-check passed.",
        (({"agent": a.name}, 1.0 if a.healthy else 0.0) for a in status.agents),
    )
    lines += _metric(
        "pg_worker_seconds_since_seen",
        "gauge",
        "Seconds since each worker's last loop.",
        (({"worker": w.name}, (now - w.last_seen_at).total_seconds()) for w in status.workers),
    )
    lines += _metric(
        "pg_llm_spend_today_usd",
        "gauge",
        "LLM spend since 00:00 UTC.",
        [({}, float(status.spend_today_usd))],
    )
    lines += _metric(
        "pg_llm_calls_today",
        "gauge",
        "LLM calls since 00:00 UTC.",
        [({}, float(status.calls_today))],
    )
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
