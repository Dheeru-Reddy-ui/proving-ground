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


def test_time_to_verdict_counts_only_validations_with_a_verdict() -> None:
    r = report(
        [
            timing(1, 70),
            timing(2, 90),
            timing(3, 130),
            timing(4, 1, "failed"),
            timing(5, None, "running"),
        ],
        [],
        [],
    )
    assert r.validations == 5
    assert r.verdicts == 3
    assert r.without_verdict == (4,)
    assert r.unfinished == 1
    assert r.verdict_minutes == (70.0, 90.0, 130.0)  # the failed one's minute is not a verdict
    assert r.registration_minutes == (100.0, 120.0, 160.0)
    assert r.verdict_p50 == 90.0
    assert r.verdict_max == 130.0


def test_a_validation_without_a_verdict_is_a_miss_against_the_target() -> None:
    r = report([timing(1, 65), timing(2, 120), timing(3, 1, "failed"), timing(4, 121)], [], [])
    assert r.within_target == 2  # 65 and 120 meet it; 121 does not; failed never does
    assert r.validations == 4


def test_availability_counts_only_probes_inside_the_window() -> None:
    probes = [Probe(at=T + timedelta(minutes=m), ok=m != 50) for m in (-10, 0, 50, 90, 91)]
    r = report([timing(1, 60), timing(2, 90)], probes, [])
    assert (r.window_start, r.window_end) == (T, T + timedelta(minutes=90))
    assert (r.probes, r.probes_ok, r.probes_outside_window) == (3, 2, 2)
    assert r.availability == round(2 / 3, 4)


def test_a_running_validation_leaves_the_window_open() -> None:
    probes = [Probe(at=T + timedelta(minutes=m), ok=True) for m in (5, 500)]
    r = report([timing(1, 60), timing(2, None, "running")], probes, [])
    assert r.window_end is None
    assert r.probes == 2


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
