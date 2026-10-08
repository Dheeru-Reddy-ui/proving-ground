"""Service levels (M2.8, docs/SLO.md), computed from stored rows and probe logs. Pure: the CLI
reads the rows and passes them in."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ValidationTiming(BaseModel):
    model_config = ConfigDict(frozen=True)

    validation_id: int
    features: tuple[str, ...]
    n: int
    status: str
    build_registered_at: datetime
    requested_at: datetime
    finished_at: datetime | None


class Probe(BaseModel):
    """One availability probe of the API (`pg slo probe`)."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    ok: bool
    status: int | None = None
    ms: float | None = None


class DeviceJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str  # final job status
    attempts: int


class SloReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    validations: int
    finished: int
    verdict_minutes: tuple[float, ...]  # request -> verdict, per finished validation
    registration_minutes: tuple[float, ...]  # build registration -> verdict
    verdict_p50: float | None
    verdict_max: float | None
    probes: int
    probes_ok: int
    availability: float | None
    device_jobs: int
    device_jobs_ok: int
    device_job_success: float | None
    device_attempts: int


def minutes(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 60, 1)


def report(
    timings: Sequence[ValidationTiming],
    probes: Sequence[Probe],
    device_jobs: Sequence[DeviceJob],
) -> SloReport:
    finished = [t for t in timings if t.finished_at is not None and t.status != "running"]
    verdict = tuple(minutes(t.requested_at, t.finished_at) for t in finished if t.finished_at)
    registration = tuple(
        minutes(t.build_registered_at, t.finished_at) for t in finished if t.finished_at
    )
    ok = sum(p.ok for p in probes)
    final = [j for j in device_jobs if j.status in ("succeeded", "failed", "dead")]
    good = sum(j.status == "succeeded" for j in final)
    return SloReport(
        validations=len(timings),
        finished=len(finished),
        verdict_minutes=verdict,
        registration_minutes=registration,
        verdict_p50=round(statistics.median(verdict), 1) if verdict else None,
        verdict_max=max(verdict) if verdict else None,
        probes=len(probes),
        probes_ok=ok,
        availability=round(ok / len(probes), 4) if probes else None,
        device_jobs=len(final),
        device_jobs_ok=good,
        device_job_success=round(good / len(final), 4) if final else None,
        device_attempts=sum(j.attempts for j in final),
    )
