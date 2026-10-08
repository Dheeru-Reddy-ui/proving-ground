"""Builds, the release webhook, validations, candidates and human review.

Reads are open in public demo mode (`PG_PUBLIC_DEMO=true`) and otherwise need the admin; every
write needs the admin (session + CSRF, or an admin token), except the webhook, which is
authenticated by its HMAC signature.
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pg_api import auth, logs, views
from pg_api.errors import ApiError
from pg_api.schemas import (
    ApkUploadOut,
    ApkUploadPart,
    BuildOut,
    BuildRegistered,
    ReviewIn,
    ValidateIn,
)
from pg_api.state import AppState, app_state
from pg_core.builds import ApkManifest, BuildRegistration
from pg_core.webhook import WebhookCheck, verify
from pg_db import jobs as queue
from pg_db import pipeline, repo
from pg_db.models import Build, Candidate, Execution, Validation, WebhookDelivery
from pg_db.session import session_scope
from pg_db.sync import sync_specs_and_bugs

router = APIRouter(prefix="/v1", tags=["builds"])
log = logs.get("pg_api.builds")
State = Annotated[AppState, Depends(app_state)]
ARTIFACT_URL_TTL_S = 300


def viewer(s: Session, request: Request, state: AppState) -> bool:
    """True for the public (demo) view, False for the admin; 401 for anyone else."""
    if auth.bearer(request) is not None or auth.session_admin(request) is not None:
        auth.require_admin(s, request, state.now())
        return False
    if state.settings.pg_public_demo:
        return True
    raise ApiError(401, "unauthorized", "admin login or an admin token is required")


def _register(
    s: Session, reg: BuildRegistration, state: AppState, source: str
) -> tuple[Build, bool]:
    if not state.has_locators(reg.locator_tag):
        raise ApiError(422, "unknown_locator_tag", f"no locator map for tag {reg.locator_tag}")
    if reg.apk is not None:
        missing = [p.index for p in reg.apk.parts if not state.storage.exists(p.key)]
        if missing:
            raise ApiError(422, "apk_not_uploaded", f"APK parts not uploaded: {missing}")
    build, created = repo.register_build(
        s,
        reg.sha256,
        reg.label,
        reg.locator_tag,
        source=source,
        patch_notes=reg.patch_notes,
        apk=reg.apk.model_dump() if reg.apk is not None else None,
    )
    sync_specs_and_bugs(s, state.root)
    log.info("build_registered", build_id=build.id, created=created, source=source)
    return build, created


@router.post("/apk/upload-urls")
def apk_upload_urls(manifest: ApkManifest, request: Request, state: State) -> ApkUploadOut:
    """Signed upload URLs for the APK parts not stored yet (re-running the CLI is safe)."""
    with session_scope(state.engine) as s:
        auth.require_admin(s, request, state.now())
    parts = []
    for part in manifest.parts:
        exists = state.storage.exists(part.key)
        upload = None if exists else state.storage.upload_target(part.key)
        parts.append(ApkUploadPart(index=part.index, key=part.key, exists=exists, upload=upload))
    return ApkUploadOut(parts=parts)


@router.post("/builds")
def register_build(body: BuildRegistration, request: Request, state: State) -> BuildRegistered:
    with session_scope(state.engine) as s:
        auth.require_admin(s, request, state.now())
        build, created = _register(s, body, state, "cli")
        return BuildRegistered(build=views.build_out(build), created=created)


@router.post("/webhooks/github")
async def webhook(request: Request, state: State) -> BuildRegistered:
    """Build registration from our release workflow: HMAC-SHA256 over `timestamp.body`, a
    five-minute window, and one build per delivery id and per APK hash."""
    secret = state.settings.pg_webhook_secret
    if secret is None:
        raise ApiError(503, "webhook_disabled", "PG_WEBHOOK_SECRET is not configured")
    raw = await request.body()
    check = verify(
        secret=secret.get_secret_value(),
        timestamp=request.headers.get("x-pg-timestamp"),
        signature_header=request.headers.get("x-pg-signature"),
        body=raw,
        now_unix=state.now().timestamp(),
    )
    delivery = request.headers.get("x-pg-delivery", "")
    if check is not WebhookCheck.OK:
        log.warning("webhook_rejected", reason=check.value, delivery=delivery[:64])
        raise ApiError(401, "bad_signature", f"webhook rejected: {check.value}")
    if not 8 <= len(delivery) <= 64:
        raise ApiError(400, "bad_request", "X-PG-Delivery must be 8-64 characters")
    try:
        reg = BuildRegistration.model_validate_json(raw)
    except ValidationError as exc:
        raise ApiError(
            422, "invalid_request", f"invalid build.json: {exc.error_count()} problem(s)"
        ) from None
    return await run_in_threadpool(_webhook_register, state, reg, delivery, raw)


def _webhook_register(
    state: AppState, reg: BuildRegistration, delivery: str, raw: bytes
) -> BuildRegistered:
    with session_scope(state.engine) as s:
        inserted = s.execute(
            insert(WebhookDelivery)
            .values(delivery_id=delivery, body_sha256=hashlib.sha256(raw).hexdigest())
            .on_conflict_do_nothing(index_elements=["delivery_id"])
            .returning(WebhookDelivery.id)
        ).scalar_one_or_none()
        build, created = _register(s, reg, state, "webhook")
        row = s.execute(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery)
        ).scalar_one()
        if row.build_id is None:
            row.build_id = build.id
        if inserted is None:
            log.info("webhook_duplicate_delivery", delivery=delivery, build_id=build.id)
        return BuildRegistered(
            build=views.build_out(build), created=created, duplicate_delivery=inserted is None
        )


@router.post("/builds/{build_id}/validate", status_code=201)
def validate(
    build_id: int, body: ValidateIn, request: Request, state: State
) -> views.ValidationOut:
    unknown = sorted(set(body.features) - set(state.features()))
    if unknown:
        raise ApiError(422, "unknown_feature", f"no spec file for: {', '.join(unknown)}")
    if body.n > state.settings.pg_max_candidates:
        raise ApiError(422, "invalid_request", f"n is at most {state.settings.pg_max_candidates}")
    with session_scope(state.engine) as s:
        principal = auth.require_admin(s, request, state.now())
        build = s.get(Build, build_id)
        if build is None:
            raise ApiError(404, "not_found", f"no build {build_id}")
        validation, created = pipeline.create_validation(
            s,
            build_id=build.id,
            features=list(dict.fromkeys(body.features)),
            n=body.n,
            seed=body.seed,
            manifest_sha=state.manifest_sha(),
            run_timeout_s=state.settings.pg_run_timeout_s,
            requested_by=f"{principal.kind}:{principal.name}",
            now=state.now(),
            idempotency_key=body.idempotency_key,
        )
        log.info("validation_created", validation_id=validation.id, created=created)
        return views.validation_out(s, validation)


@router.get("/builds")
def list_builds(request: Request, state: State) -> list[BuildOut]:
    with session_scope(state.engine) as s:
        viewer(s, request, state)
        rows = s.execute(select(Build).order_by(Build.id.desc()).limit(100)).scalars()
        return [views.build_out(b) for b in rows]


@router.get("/builds/{build_id}")
def get_build(build_id: int, request: Request, state: State) -> views.BuildDetail:
    with session_scope(state.engine) as s:
        viewer(s, request, state)
        build = s.get(Build, build_id)
        if build is None:
            raise ApiError(404, "not_found", f"no build {build_id}")
        return views.build_detail(s, build)


@router.get("/validations/{validation_id}")
def get_validation(validation_id: int, request: Request, state: State) -> views.ValidationDetail:
    with session_scope(state.engine) as s:
        viewer(s, request, state)
        validation = s.get(Validation, validation_id)
        if validation is None:
            raise ApiError(404, "not_found", f"no validation {validation_id}")
        return views.validation_detail(s, validation)


@router.get("/candidates/{candidate_id}")
def get_candidate(candidate_id: int, request: Request, state: State) -> views.CandidateDetail:
    with session_scope(state.engine) as s:
        public = viewer(s, request, state)
        cand = s.get(Candidate, candidate_id)
        if cand is None:
            raise ApiError(404, "not_found", f"no candidate {candidate_id}")
        return views.candidate_detail(s, cand, public=public)


@router.post("/candidates/{candidate_id}/review")
def review(
    candidate_id: int, body: ReviewIn, request: Request, state: State
) -> views.CandidateDetail:
    """A human decision on a REVIEW candidate, stored with its reason (labelled data for the
    Phase 4 false-accept measurement)."""
    with session_scope(state.engine) as s:
        principal = auth.require_admin(s, request, state.now())
        cand = s.get(Candidate, candidate_id, with_for_update=True)
        if cand is None:
            raise ApiError(404, "not_found", f"no candidate {candidate_id}")
        if cand.decision != "review":
            raise ApiError(
                409, "conflict", f"candidate {candidate_id} is {cand.decision}, not review"
            )
        cand.human_decision = body.decision
        cand.human_reason = body.reason
        cand.human_decided_at = state.now()
        log.info(
            "human_review", candidate_id=candidate_id, decision=body.decision, by=principal.name
        )
        return views.candidate_detail(s, cand, public=False)


@router.post("/jobs/{job_id}/retry")
def retry_dead_job(job_id: int, request: Request, state: State) -> dict[str, str]:
    with session_scope(state.engine) as s:
        auth.require_admin(s, request, state.now())
        if not queue.retry_dead(s, job_id, state.now()):
            raise ApiError(409, "conflict", f"job {job_id} is not dead")
        log.info("dead_job_retried", job_id=job_id)
        return {"status": "queued"}


@router.get("/executions/{execution_id}/artifacts/{name}")
def artifact(execution_id: int, name: str, request: Request, state: State) -> RedirectResponse:
    """A short-lived link to one artifact. Admin only: never in the public demo."""
    with session_scope(state.engine) as s:
        auth.require_admin(s, request, state.now())
        execution = s.get(Execution, execution_id)
        keys = (execution.artifacts or {}).get("keys", {}) if execution else {}
        if name not in keys:
            raise ApiError(404, "not_found", "no such artifact")
        key = str(keys[name])
    return RedirectResponse(state.storage.download_url(key, ARTIFACT_URL_TTL_S), status_code=307)
