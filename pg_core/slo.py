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


VERDICT_TARGET_MIN = 120.0  # docs/SLO.md, service level 1


class SloReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    validations: int
    verdicts: int  # validations that reached a verdict (status succeeded)
    without_verdict: tuple[int, ...]  # ids of validations that ended failed: no verdict
    unfinished: int
    within_target: int  # verdicts within VERDICT_TARGET_MIN; no verdict never counts
    verdict_minutes: tuple[float, ...]  # request -> verdict, per validation with a verdict
    registration_minutes: tuple[float, ...]  # build registration -> verdict
    verdict_p50: float | None
    verdict_max: float | None
    window_start: datetime | None  # first validation requested
    window_end: datetime | None  # last verdict; None while a validation is still running
    probes: int  # probes inside the window
    probes_ok: int
    probes_outside_window: int
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
    """Service levels as docs/SLO.md defines them. A validation that ended `failed` has no
    verdict: it is left out of the time-to-verdict figures and counts as a miss against the
    target. Availability counts only the probes inside the window, from the first validation
    request to the last verdict."""
    with_verdict = [t for t in timings if t.status == "succeeded" and t.finished_at is not None]
    failed = tuple(t.validation_id for t in timings if t.status == "failed")
    verdict = tuple(minutes(t.requested_at, t.finished_at) for t in with_verdict if t.finished_at)
    registration = tuple(
        minutes(t.build_registered_at, t.finished_at) for t in with_verdict if t.finished_at
    )
    ends = [t.finished_at for t in timings if t.finished_at is not None]
    start = min((t.requested_at for t in timings), default=None)
    end = max(ends) if ends and len(ends) == len(timings) else None
    inside = [
        p for p in probes if (start is None or p.at >= start) and (end is None or p.at <= end)
    ]
    ok = sum(p.ok for p in inside)
    final = [j for j in device_jobs if j.status in ("succeeded", "failed", "dead")]
    good = sum(j.status == "succeeded" for j in final)
    return SloReport(
        validations=len(timings),
        verdicts=len(with_verdict),
        without_verdict=failed,
        unfinished=sum(t.status == "running" for t in timings),
        within_target=sum(m <= VERDICT_TARGET_MIN for m in verdict),
        verdict_minutes=verdict,
        registration_minutes=registration,
        verdict_p50=round(statistics.median(verdict), 1) if verdict else None,
        verdict_max=max(verdict) if verdict else None,
        window_start=start,
        window_end=end,
        probes=len(inside),
        probes_ok=ok,
        probes_outside_window=len(probes) - len(inside),
        availability=round(ok / len(inside), 4) if inside else None,
        device_jobs=len(final),
        device_jobs_ok=good,
        device_job_success=round(good / len(final), 4) if final else None,
        device_attempts=sum(j.attempts for j in final),
    )
