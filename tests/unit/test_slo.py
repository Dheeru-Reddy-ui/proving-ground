"""Service-level computations from validations, probes and device jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pg_core.slo import DeviceJob, Probe, ValidationTiming, report

T = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def timing(vid: int, minutes: float | None, status: str = "succeeded") -> ValidationTiming:
    return ValidationTiming(
        validation_id=vid,
        features=("store", "run_and_gameover"),
        n=8,
        status=status,
        build_registered_at=T - timedelta(minutes=30),
        requested_at=T,
        finished_at=None if minutes is None else T + timedelta(minutes=minutes),
    )


def test_time_to_verdict_counts_only_finished_validations() -> None:
    r = report([timing(1, 70), timing(2, 90), timing(3, 110), timing(4, None, "running")], [], [])
    assert r.validations == 4
    assert r.finished == 3
    assert r.verdict_minutes == (70.0, 90.0, 110.0)
    assert r.registration_minutes == (100.0, 120.0, 140.0)
    assert r.verdict_p50 == 90.0
    assert r.verdict_max == 110.0


def test_availability_and_device_job_success() -> None:
    probes = [Probe(at=T + timedelta(seconds=30 * i), ok=i != 3) for i in range(10)]
    jobs = [DeviceJob(status="succeeded", attempts=1)] * 18 + [
        DeviceJob(status="dead", attempts=3),
        DeviceJob(status="queued", attempts=0),  # not finished: not counted
        DeviceJob(status="succeeded", attempts=2),
    ]
    r = report([], probes, jobs)
    assert (r.probes, r.probes_ok, r.availability) == (10, 9, 0.9)
    assert (r.device_jobs, r.device_jobs_ok) == (20, 19)
    assert r.device_job_success == 0.95
    assert r.device_attempts == 23


def test_nothing_measured_gives_no_numbers() -> None:
    r = report([], [], [])
    assert r.verdict_p50 is None
    assert r.availability is None
    assert r.device_job_success is None
