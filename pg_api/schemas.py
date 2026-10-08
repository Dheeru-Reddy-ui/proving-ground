"""Request and response bodies of the `/v1` API (Pydantic, validated at the boundary)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from pg_api.storage import UploadTarget
from pg_core.builds import Sha256
from pg_core.jobs import JobType

AgentName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
ArtifactName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")]
CapabilityValue = str | int | bool


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- agents and jobs ----------------------------------------------------------------------


class RegisterIn(Strict):
    enrolment_token: str = Field(min_length=10, max_length=200)
    name: AgentName
    capabilities: dict[str, CapabilityValue] = Field(default_factory=dict, max_length=20)


class RegisterOut(BaseModel):
    agent_id: int
    name: str
    token: str  # shown once; the server keeps only its hash


class HealthCheck(Strict):
    name: str = Field(max_length=64)
    ok: bool
    detail: str = Field(default="", max_length=500)


class StatusIn(Strict):
    healthy: bool
    checks: list[HealthCheck] = Field(default_factory=list, max_length=20)
    capabilities: dict[str, CapabilityValue] | None = Field(default=None, max_length=20)
    current_job_id: int | None = None


class ClaimIn(Strict):
    types: list[JobType] = Field(min_length=1, max_length=5)
    capabilities: dict[str, CapabilityValue] = Field(default_factory=dict, max_length=20)
    installed_build_sha: Sha256 | None = None


class ClaimedJob(BaseModel):
    job_id: int
    type: JobType
    payload: dict[str, Any]
    lease_token: str
    lease_expires_at: datetime
    attempts: int
    max_attempts: int


class LeaseIn(Strict):
    lease_token: str = Field(min_length=8, max_length=64)


class LeaseOut(BaseModel):
    lease_expires_at: datetime


class CompleteIn(LeaseIn):
    result: dict[str, Any]


class FailIn(LeaseIn):
    error: str = Field(min_length=1, max_length=4000)
    retryable: bool


class InstallResult(Strict):
    installed_sha256: Sha256


class JobAck(BaseModel):
    status: Literal["stored", "duplicate", "failed", "released"]
    job_status: str


class UploadUrlIn(LeaseIn):
    job_id: int
    attempt: int = Field(ge=1, le=10)
    name: ArtifactName


class ApkPartOut(BaseModel):
    index: int
    sha256: str
    size: int
    url: str


class ApkOut(BaseModel):
    sha256: str
    size: int
    parts: list[ApkPartOut]


class CodeOut(BaseModel):
    code_sha: str
    code: str


# --- builds -------------------------------------------------------------------------------


class ApkUploadPart(BaseModel):
    index: int
    key: str
    exists: bool
    upload: UploadTarget | None


class ApkUploadOut(BaseModel):
    parts: list[ApkUploadPart]


class BuildOut(BaseModel):
    id: int
    sha256: str
    label: str
    locator_tag: str
    source: str
    patch_notes: str
    has_apk: bool
    created_at: datetime


class BuildRegistered(BaseModel):
    build: BuildOut
    created: bool
    duplicate_delivery: bool = False


class ValidateIn(Strict):
    features: list[str] = Field(min_length=1, max_length=6)
    n: int = Field(default=8, ge=1)
    seed: int | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class ReviewIn(Strict):
    decision: Literal["accept", "reject"]
    reason: str = Field(min_length=3, max_length=500)
