"""Device-agent endpoints (ADR-0005): enrolment, status, the job lease cycle, test code, APK
download and artifact upload URLs. Every route except `/register` needs an agent token."""

from __future__ import annotations

import hmac
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from pg_api import auth, logs
from pg_api.errors import ApiError
from pg_api.schemas import (
    ApkOut,
    ApkPartOut,
    ClaimedJob,
    ClaimIn,
    CodeOut,
    CompleteIn,
    FailIn,
    InstallResult,
    JobAck,
    LeaseIn,
    LeaseOut,
    RegisterIn,
    RegisterOut,
    StatusIn,
    UploadUrlIn,
)
from pg_api.state import AppState, app_state
from pg_api.storage import UploadTarget
from pg_core.builds import ApkManifest
from pg_core.job_results import RunTestResult
from pg_core.jobs import DEVICE_JOB_TYPES, CompletionAction, JobStatus, JobType, LeaseCheck
from pg_db import jobs as queue
from pg_db import pipeline
from pg_db.models import Agent, Build, Candidate, Job
from pg_db.session import session_scope

router = APIRouter(prefix="/v1", tags=["agent"])
log = logs.get("pg_api.agents")
State = Annotated[AppState, Depends(app_state)]
DOWNLOAD_TTL_S = 3600


def _agent(s: Session, request: Request, state: AppState) -> tuple[auth.Principal, Agent]:
    principal = auth.require_token(s, request, "agent", state.now())
    agent = s.get_one(Agent, principal.agent_id)
    return principal, agent


def _own_job(s: Session, job_id: int, principal: auth.Principal) -> Job:
    """A device job that this agent leased (or leased last)."""
    job = s.get(Job, job_id)
    if job is None or JobType(job.type) not in DEVICE_JOB_TYPES:
        raise ApiError(404, "not_found", f"no device job {job_id}")
    if job.lease_owner is not None and job.lease_owner != principal.name:
        raise ApiError(403, "forbidden", "the job is leased by another agent")
    return job


@router.post("/agents/register", status_code=201)
def register(body: RegisterIn, state: State) -> RegisterOut:
    with session_scope(state.engine) as s:
        agent, token = auth.enrol_agent(
            s,
            enrolment_token=body.enrolment_token,
            name=body.name,
            capabilities=dict(body.capabilities),
            now=state.now(),
        )
        log.info("agent_enrolled", agent=agent.name)
        return RegisterOut(agent_id=agent.id, name=agent.name, token=token)


@router.post("/agents/status", status_code=204)
def status(body: StatusIn, request: Request, state: State) -> Response:
    """An agent reports its health, also when unhealthy and not claiming (ADR-0005)."""
    with session_scope(state.engine) as s:
        _, agent = _agent(s, request, state)
        agent.last_seen_at = state.now()
        agent.health = body.model_dump(mode="json", exclude={"capabilities"})
        if body.capabilities is not None:
            agent.capabilities = dict(body.capabilities)
    return Response(status_code=204)


@router.post("/jobs/claim", response_model=None)
def claim(body: ClaimIn, request: Request, state: State) -> ClaimedJob | Response:
    with session_scope(state.engine) as s:
        principal, agent = _agent(s, request, state)
        now = state.now()
        agent.last_seen_at = now
        agent.capabilities = dict(body.capabilities)
        types = [t for t in body.types if t in DEVICE_JOB_TYPES]
        if not types:
            raise ApiError(422, "invalid_request", "agents claim INSTALL_BUILD and RUN_TEST only")
        # Reap here as well as in the worker, so a crashed agent's job comes back even when no
        # worker is running.
        pipeline.expire_leases(s, now)
        concurrency = body.capabilities.get("max_concurrency", 1)
        max_concurrency = concurrency if isinstance(concurrency, int) and concurrency >= 1 else 1
        job = queue.claim(
            s,
            owner=principal.name,
            types=types,
            capabilities=body.capabilities,
            now=now,
            max_concurrency=max_concurrency,
            ttl_s=state.settings.pg_lease_ttl_s,
            prefer_build_sha=body.installed_build_sha,
        )
        if job is None:
            return Response(status_code=204)
        structlog.contextvars.bind_contextvars(job_id=job.id)
        log.info("job_leased", type=job.type, attempts=job.attempts, key=job.idempotency_key)
        if job.lease_token is None or job.lease_expires_at is None:
            raise ApiError(500, "internal_error", "a claimed job has no lease")
        return ClaimedJob(
            job_id=job.id,
            type=JobType(job.type),
            payload=job.payload,
            lease_token=job.lease_token,
            lease_expires_at=job.lease_expires_at,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
        )


@router.post("/jobs/{job_id}/heartbeat")
def heartbeat(job_id: int, body: LeaseIn, request: Request, state: State) -> LeaseOut:
    with session_scope(state.engine) as s:
        principal, agent = _agent(s, request, state)
        _own_job(s, job_id, principal)
        now = state.now()
        agent.last_seen_at = now
        check = queue.heartbeat(s, job_id, body.lease_token, now, state.settings.pg_lease_ttl_s)
        if check is not LeaseCheck.OK:
            raise ApiError(409, "lease_lost", "the lease is no longer yours; stop this job")
        expires = s.get_one(Job, job_id).lease_expires_at
        if expires is None:
            raise ApiError(409, "lease_lost", "the lease is no longer yours; stop this job")
        return LeaseOut(lease_expires_at=expires)


def _ack(action: CompletionAction, job: Job) -> JobAck:
    if action is CompletionAction.STORE:
        if job.status == JobStatus.SUCCEEDED.value:
            return JobAck(status="stored", job_status=job.status)
        return JobAck(status="failed", job_status=job.status)  # infra: retried or dead
    if action is CompletionAction.NO_OP:
        return JobAck(status="duplicate", job_status=job.status)
    if action is CompletionAction.CONFLICT:
        log.warning("completion_conflict", job_id=job.id)
        raise ApiError(409, "conflict", "the job already has a different result")
    raise ApiError(409, "lease_lost", "the lease is no longer yours; the result was not stored")


@router.post("/jobs/{job_id}/complete")
def complete(job_id: int, body: CompleteIn, request: Request, state: State) -> JobAck:
    with session_scope(state.engine) as s:
        principal, agent = _agent(s, request, state)
        job = _own_job(s, job_id, principal)
        now = state.now()
        agent.last_seen_at = now
        structlog.contextvars.bind_contextvars(job_id=job_id)
        try:
            if job.type == JobType.RUN_TEST.value:
                result = RunTestResult.model_validate(body.result)
                prefix = f"artifacts/job-{job.id}/"
                foreign = [
                    k
                    for a in result.attempts
                    for k in a.artifacts.values()
                    if not k.startswith(prefix)
                ]
                if foreign:
                    raise ApiError(422, "invalid_result", "artifact keys must belong to this job")
                action = pipeline.complete_run_test(
                    s, job_id, body.lease_token, result, now, state.jitter()
                )
            else:
                installed = InstallResult.model_validate(body.result)
                if installed.installed_sha256 != job.payload.get("build_sha"):
                    raise ApiError(
                        422, "wrong_build", "the installed APK is not the build this job installs"
                    )
                action = pipeline.complete(s, job_id, body.lease_token, installed.model_dump(), now)
        except ValidationError as exc:
            raise ApiError(
                422, "invalid_result", f"invalid {job.type} result: {exc.error_count()} problem(s)"
            ) from None
        s.flush()  # never refresh here: it would discard the unflushed completion
        ack = _ack(action, job)
        log.info("job_completed", ack=ack.status, job_status=job.status)
        return ack


@router.post("/jobs/{job_id}/fail")
def fail(job_id: int, body: FailIn, request: Request, state: State) -> JobAck:
    with session_scope(state.engine) as s:
        principal, agent = _agent(s, request, state)
        _own_job(s, job_id, principal)
        now = state.now()
        agent.last_seen_at = now
        transition = pipeline.fail(
            s,
            job_id,
            body.lease_token,
            error=body.error,
            retryable=body.retryable,
            now=now,
            jitter=state.jitter(),
        )
        if transition is None:
            raise ApiError(409, "lease_lost", "the lease is no longer yours")
        log.info("job_failed", job_id=job_id, to=transition.status.value, retryable=body.retryable)
        return JobAck(status="failed", job_status=transition.status.value)


@router.post("/jobs/{job_id}/release")
def release(job_id: int, body: LeaseIn, request: Request, state: State) -> JobAck:
    with session_scope(state.engine) as s:
        principal, _ = _agent(s, request, state)
        _own_job(s, job_id, principal)
        job = queue.locked(s, job_id)
        if job is None or not queue.release(job, body.lease_token, state.now()):
            raise ApiError(409, "lease_lost", "the lease is no longer yours")
        return JobAck(status="released", job_status=job.status)


@router.get("/code/{code_sha}")
def code(code_sha: str, request: Request, state: State) -> CodeOut:
    """The source of a candidate by its sha256; the agent re-hashes it before running it."""
    with session_scope(state.engine) as s:
        auth.require_token(s, request, "agent", state.now())
        cand = s.execute(
            select(Candidate).where(Candidate.code_sha == code_sha).limit(1)
        ).scalar_one_or_none()
        if cand is None:
            raise ApiError(404, "not_found", "no candidate with that code")
        return CodeOut(code_sha=cand.code_sha, code=cand.code)


@router.get("/apk/{sha256}")
def apk(sha256: str, request: Request, state: State) -> ApkOut:
    """Signed download URLs for every part of a build's APK (ADR-0010)."""
    with session_scope(state.engine) as s:
        auth.require_token(s, request, "agent", state.now())
        build = s.execute(select(Build).where(Build.apk_sha256 == sha256)).scalar_one_or_none()
        if build is None or build.apk is None:
            raise ApiError(404, "not_found", "no uploaded APK for that build")
        manifest = ApkManifest.model_validate(build.apk)
    return ApkOut(
        sha256=manifest.sha256,
        size=manifest.size,
        parts=[
            ApkPartOut(
                index=p.index,
                sha256=p.sha256,
                size=p.size,
                url=state.storage.download_url(p.key, DOWNLOAD_TTL_S),
            )
            for p in manifest.parts
        ],
    )


@router.post("/artifacts/upload-url")
def upload_url(body: UploadUrlIn, request: Request, state: State) -> UploadTarget:
    """A signed upload URL for one artifact of a RUN_TEST attempt the agent holds."""
    with session_scope(state.engine) as s:
        principal, _ = _agent(s, request, state)
        job = _own_job(s, body.job_id, principal)
        if job.type != JobType.RUN_TEST.value:
            raise ApiError(422, "invalid_request", "artifacts belong to RUN_TEST jobs")
        current = hmac.compare_digest(job.lease_token or "", body.lease_token)
        if job.status != JobStatus.LEASED.value or not current:
            raise ApiError(409, "lease_lost", "the lease is no longer yours")
        key = f"artifacts/job-{job.id}/try-{job.attempts}/a{body.attempt}/{body.name}"
    return state.storage.upload_target(key)
