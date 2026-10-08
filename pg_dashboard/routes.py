"""Dashboard pages (M2.5): server-rendered Jinja2, HTMX only for live progress and inline review.

Every page works without JavaScript. Reads need the admin session, or public demo mode
(`PG_PUBLIC_DEMO=true`, read-only: no review, no retries, no artifacts). Every write needs the
admin session and its CSRF token.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from pg_api import auth, logs, views
from pg_api.errors import ApiError
from pg_api.state import AppState, app_state
from pg_core.jobs import JobStatus, JobType
from pg_dashboard import queries
from pg_db import jobs as queue
from pg_db.models import Build, Candidate, GenerationRun, Job, Validation
from pg_db.pipeline import load_snapshot
from pg_db.session import session_scope

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
router = APIRouter(include_in_schema=False)
log = logs.get("pg_dashboard")
State = Annotated[AppState, Depends(app_state)]
REPO_URL = "https://github.com/Dheeru-Reddy-ui/proving-ground"


# --- template helpers ---------------------------------------------------------------------


def _utc(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "never"


def _ago(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "never"
    seconds = int((now - value).total_seconds())
    if seconds < 90:
        return f"{max(seconds, 0)} s ago"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


def _usd(value: Decimal | float | int | None) -> str:
    amount = Decimal(value or 0)
    return f"${amount:.2f}" if amount >= Decimal("0.01") or amount == 0 else f"${amount:.6f}"


templates.env.filters["utc"] = _utc
templates.env.filters["usd"] = _usd
templates.env.filters["sha"] = lambda v: str(v)[:12]
templates.env.filters["pretty"] = lambda v: json.dumps(v, indent=2, sort_keys=True, default=str)
templates.env.globals["repo_url"] = REPO_URL
# Static URLs carry a content hash, so a deploy never serves a stale stylesheet or script.
templates.env.globals["static_version"] = {
    name: hashlib.sha256((HERE / "static" / name).read_bytes()).hexdigest()[:10]
    for name in ("app.css", "htmx.min.js")
}


class Viewer:
    def __init__(self, request: Request, state: AppState) -> None:
        self.admin = auth.session_admin(request) is not None
        self.demo = state.settings.pg_public_demo and not self.admin
        self.csrf = request.session.get("csrf") if self.admin else None
        self.now = state.now()

    @property
    def allowed(self) -> bool:
        return self.admin or self.demo


def csp(state: AppState) -> str:
    images = "'self' data:"
    if state.settings.supabase_url:
        parsed = urlparse(state.settings.supabase_url)
        images += f" {parsed.scheme}://{parsed.netloc}"
    return (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        f"img-src {images}; connect-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    )


def page(
    request: Request,
    state: AppState,
    viewer: Viewer,
    name: str,
    context: Mapping[str, Any],
    status_code: int = 200,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        name,
        {
            "viewer": viewer,
            "now": viewer.now,
            "ago": lambda v: _ago(v, viewer.now),
            **context,
        },
        status_code=status_code,
    )
    response.headers["Content-Security-Policy"] = csp(state)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


def guard(request: Request, state: AppState) -> Viewer | RedirectResponse:
    viewer = Viewer(request, state)
    if not viewer.allowed:
        return RedirectResponse("/login", status_code=303)
    return viewer


def not_found(request: Request, state: AppState, viewer: Viewer, what: str) -> HTMLResponse:
    return page(request, state, viewer, "error.html", {"message": f"No {what}."}, 404)


def require_admin_form(request: Request, csrf_token: str) -> None:
    if auth.session_admin(request) is None:
        raise ApiError(401, "unauthorized", "log in as the admin first")
    auth.check_csrf(request, csrf_token or request.headers.get(auth.CSRF_HEADER))


# --- pages --------------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, state: State) -> HTMLResponse:
    return page(request, state, Viewer(request, state), "login.html", {"failed": False})


@router.post("/login", response_model=None)
def login(
    request: Request, state: State, password: Annotated[str, Form(max_length=200)]
) -> Response:
    """Admin login: the password is checked against the argon2 hash in PG_ADMIN_PASSWORD_HASH;
    the session cookie is signed, HttpOnly and SameSite=Strict."""
    stored = state.settings.pg_admin_password_hash
    if stored is None:
        message = "Admin login is switched off: PG_ADMIN_PASSWORD_HASH is not set."
        return page(request, state, Viewer(request, state), "login.html", {"failed": message}, 503)
    if not auth.password_ok(stored.get_secret_value(), password):
        log.warning("admin_login_failed")
        failed = "That password is not right."
        return page(request, state, Viewer(request, state), "login.html", {"failed": failed}, 401)
    auth.login(request)
    log.info("admin_login")
    return RedirectResponse("/", status_code=303)


@router.post("/logout", response_model=None)
def logout(request: Request, csrf_token: Annotated[str, Form()] = "") -> Response:
    if auth.session_admin(request) is not None:
        auth.check_csrf(request, csrf_token or request.headers.get(auth.CSRF_HEADER))
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/session")
def current_session(request: Request) -> dict[str, object]:
    """Whether this browser is logged in, and its CSRF token for same-origin scripts."""
    if auth.session_admin(request) is None:
        return {"admin": False}
    return {"admin": True, "csrf_token": request.session.get("csrf")}


@router.get("/", response_model=None)
def builds_page(request: Request, state: State) -> Response:
    viewer = guard(request, state)
    if isinstance(viewer, RedirectResponse):
        return viewer
    with session_scope(state.engine) as s:
        rows = queries.build_rows(s)
    return page(request, state, viewer, "builds.html", {"builds": rows, "nav": "builds"})


@router.get("/builds/{build_id}", response_model=None)
def build_page(build_id: int, request: Request, state: State) -> Response:
    viewer = guard(request, state)
    if isinstance(viewer, RedirectResponse):
        return viewer
    with session_scope(state.engine) as s:
        build = s.get(Build, build_id)
        if build is None:
            return not_found(request, state, viewer, "such build")
        detail = views.build_detail(s, build)
        runs = queries.runs_of_build(s, build.id)
    accepted = sum(r.accepted for r in runs)
    totals = {
        "decisions": {d: sum(r.decisions[d] for r in runs) for d in queries.DECISIONS},
        "g2_passed": sum(r.g2_passed for r in runs),
        "g2_decided": sum(r.g2_decided for r in runs),
        "llm_usd": sum((r.llm_usd for r in runs), Decimal(0)),
        "device_minutes": round(sum(r.device_minutes for r in runs), 1),
        "attempts": sum(r.attempts for r in runs),
        "infra_attempts": sum(r.infra_attempts for r in runs),
        "accepted": accepted,
    }
    return page(
        request,
        state,
        viewer,
        "build.html",
        {"detail": detail, "runs": runs, "totals": totals, "nav": "builds"},
    )


@router.get("/runs/{run_id}", response_model=None)
def run_page(run_id: int, request: Request, state: State) -> Response:
    viewer = guard(request, state)
    if isinstance(viewer, RedirectResponse):
        return viewer
    with session_scope(state.engine) as s:
        run = s.get(GenerationRun, run_id)
        if run is None:
            return not_found(request, state, viewer, "such generation run")
        (summary,) = queries.run_summaries(s, [run])
        matrix = queries.kill_matrix(s, run_id)
        build = s.get_one(Build, run.build_id)
        cands = list(
            s.execute(
                select(Candidate)
                .where(Candidate.generation_run_id == run_id)
                .order_by(Candidate.id)
            ).scalars()
        )
        trace = queries.llm_trace(s, run_id)
    return page(
        request,
        state,
        viewer,
        "run.html",
        {
            "run": summary,
            "build": build,
            "matrix": matrix,
            "candidates": cands,
            "trace": trace,
            "nav": "builds",
        },
    )


def _progress(s: Session, validation: Validation) -> dict[str, Any]:
    snap = load_snapshot(s, validation)
    run_jobs: dict[int, dict[str, int]] = {}
    for job in s.execute(
        select(Job).where(Job.validation_id == validation.id, Job.type == JobType.RUN_TEST.value)
    ).scalars():
        counts = run_jobs.setdefault(int(job.payload["generation_run_id"]), {})
        counts[job.status] = counts.get(job.status, 0) + 1
    features = []
    for name in validation.features:
        f = snap.feature_states.get(name)
        runs = run_jobs.get(f.generation_run_id or -1, {}) if f else {}
        features.append(
            {
                "name": name,
                "generation_run_id": f.generation_run_id if f else None,
                "generate": f.generate.value if f and f.generate else "waiting",
                "score_static": f.score_static.value if f and f.score_static else "waiting",
                "runs": runs,
                "runs_done": sum(v for k, v in runs.items() if JobStatus(k).is_final),
                "runs_total": sum(runs.values()),
                "score_final": f.score_final.value if f and f.score_final else "waiting",
            }
        )
    return {
        "validation": views.validation_out(s, validation),
        "install": snap.install.value if snap.install else "waiting",
        "features": features,
    }


@router.get("/validations/{validation_id}", response_model=None)
def validation_page(validation_id: int, request: Request, state: State) -> Response:
    viewer = guard(request, state)
    if isinstance(viewer, RedirectResponse):
        return viewer
    with session_scope(state.engine) as s:
        validation = s.get(Validation, validation_id)
        if validation is None:
            return not_found(request, state, viewer, "such validation")
        progress = _progress(s, validation)
        detail = views.validation_detail(s, validation)
        build = s.get_one(Build, validation.build_id)
    return page(
        request,
        state,
        viewer,
        "validation.html",
        {"progress": progress, "detail": detail, "build": build, "nav": "builds"},
    )


@router.get("/validations/{validation_id}/progress", response_model=None)
def validation_progress(validation_id: int, request: Request, state: State) -> Response:
    viewer = guard(request, state)
    if isinstance(viewer, RedirectResponse):
        return viewer
    with session_scope(state.engine) as s:
        validation = s.get(Validation, validation_id)
        if validation is None:
            return not_found(request, state, viewer, "such validation")
        progress = _progress(s, validation)
    return page(request, state, viewer, "_progress.html", {"progress": progress})


@router.get("/candidates/{candidate_id}", response_model=None)
def candidate_page(candidate_id: int, request: Request, state: State) -> Response:
    viewer = guard(request, state)
    if isinstance(viewer, RedirectResponse):
        return viewer
    with session_scope(state.engine) as s:
        cand = s.get(Candidate, candidate_id)
        if cand is None:
            return not_found(request, state, viewer, "such candidate")
        detail = views.candidate_detail(s, cand, public=viewer.demo)
        trace = queries.llm_trace(s, cand.generation_run_id)
    return page(
        request,
        state,
        viewer,
        "candidate.html",
        {"c": detail, "trace": trace, "nav": "builds"},
    )


@router.get("/review", response_model=None)
def review_page(request: Request, state: State) -> Response:
    viewer = guard(request, state)
    if isinstance(viewer, RedirectResponse):
        return viewer
    with session_scope(state.engine) as s:
        waiting, decided = queries.review_items(s)
    return page(
        request,
        state,
        viewer,
        "review.html",
        {"waiting": waiting, "decided": decided, "nav": "review"},
    )


@router.post("/review/{candidate_id}", response_model=None)
def review_submit(
    candidate_id: int,
    request: Request,
    state: State,
    decision: Annotated[str, Form()],
    reason: Annotated[str, Form(max_length=500)],
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    require_admin_form(request, csrf_token)
    viewer = Viewer(request, state)
    error = None
    if decision not in ("accept", "reject"):
        error = "Choose accept or reject."
    elif len(reason.strip()) < 3:
        error = "Give a reason of at least 3 characters."
    with session_scope(state.engine) as s:
        cand = s.get(Candidate, candidate_id, with_for_update=True)
        if cand is None or cand.decision != "review":
            error = error or "This candidate is not waiting for review."
        elif error is None:
            cand.human_decision = decision
            cand.human_reason = reason.strip()
            cand.human_decided_at = state.now()
            log.info("human_review", candidate_id=candidate_id, decision=decision)
        waiting, decided = queries.review_items(s)
    item = next((i for i in waiting + decided if i.id == candidate_id), None)
    if request.headers.get("HX-Request") and item is not None:
        return page(request, state, viewer, "_review_item.html", {"item": item, "error": error})
    return RedirectResponse("/review", status_code=303)


@router.get("/system", response_model=None)
def system_page(request: Request, state: State) -> Response:
    viewer = guard(request, state)
    if isinstance(viewer, RedirectResponse):
        return viewer
    with session_scope(state.engine) as s:
        status = queries.system_status(s, viewer.now)
    return page(
        request, state, viewer, "system.html", {"system": status, "nav": "system", "token": None}
    )


@router.post("/system/jobs/{job_id}/retry", response_model=None)
def system_retry(
    job_id: int,
    request: Request,
    state: State,
    csrf_token: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/system",
) -> Response:
    require_admin_form(request, csrf_token)
    with session_scope(state.engine) as s:
        if queue.retry_dead(s, job_id, state.now()):
            log.info("dead_job_retried", job_id=job_id)
    target = next if next.startswith("/") and not next.startswith("//") else "/system"
    return RedirectResponse(target, status_code=303)


@router.post("/system/enrolment-token", response_model=None)
def system_enrolment_token(
    request: Request, state: State, csrf_token: Annotated[str, Form()] = ""
) -> Response:
    """A one-time agent enrolment token, shown once on the page that answers this form."""
    require_admin_form(request, csrf_token)
    viewer = Viewer(request, state)
    with session_scope(state.engine) as s:
        token = auth.create_enrolment_token(s, state.now(), timedelta(hours=24))
        status = queries.system_status(s, viewer.now)
    log.info("enrolment_token_created")
    return page(
        request, state, viewer, "system.html", {"system": status, "nav": "system", "token": token}
    )
