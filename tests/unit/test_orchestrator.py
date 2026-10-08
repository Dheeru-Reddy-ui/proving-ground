"""The build-validation planner (ADR-0006): job order, gate parity with `pg prove`, failures."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence

import pytest

from pg_core.gates.base import Execution, Outcome
from pg_core.gates.runs import (
    BugRef,
    check_determinism,
    detection_needs_more,
    determinism_needs_more,
)
from pg_core.jobs import JobSpec, JobStatus, JobType
from pg_core.orchestrator import (
    CLEAN,
    EXTRA_REPEATS,
    CandidateState,
    FeatureState,
    RunJob,
    ValidationSnapshot,
    ValidationStatus,
    plan_validation,
    run_key_of,
)

P, A, T, INF = Outcome.PASSED, Outcome.ASSERTION, Outcome.PG_TIMEOUT, Outcome.INFRA
OK, DEAD = JobStatus.SUCCEEDED, JobStatus.DEAD
STORE_BUG = BugRef(id="B1", flag="flag_store", pages=("store",))
STORE_BUG_2 = BugRef(id="B2", flag="flag_store_2", pages=("store", "main_menu"))
MISSIONS_BUG = BugRef(id="B3", flag="flag_missions", pages=("missions",))


def snapshot(**overrides: object) -> ValidationSnapshot:
    base: dict[str, object] = {
        "validation_id": 7,
        "build_sha": "ab" * 32,
        "locator_tag": "abababababab",
        "features": ("store",),
        "n": 4,
        "manifest_sha": "m" * 64,
        "run_timeout_s": 300.0,
        "dev_bugs": (STORE_BUG, STORE_BUG_2, MISSIONS_BUG),
    }
    base.update(overrides)
    return ValidationSnapshot.model_validate(base)


def candidate(cid: int = 1, *, g1: bool | None = True, **runs: Sequence[RunJob]) -> CandidateState:
    return CandidateState(
        id=cid,
        code_sha=f"{cid:064d}",
        g1_passed=g1,
        pages_used=("main_menu", "store"),
        runs={k: tuple(v) for k, v in runs.items()},
    )


def done(*outcomes: Outcome) -> list[RunJob]:
    return [
        RunJob(repeat=i, status=OK, outcome=o, duration_s=5.0) for i, o in enumerate(outcomes, 1)
    ]


def scored(*candidates: CandidateState, final: JobStatus | None = None) -> FeatureState:
    return FeatureState(
        feature="store",
        generate=OK,
        generation_run_id=12,
        score_static=OK,
        score_final=final,
        candidates=candidates,
    )


def ready(*candidates: CandidateState, final: JobStatus | None = None) -> ValidationSnapshot:
    return snapshot(install=OK, feature_states={"store": scored(*candidates, final=final)})


def keys(jobs: Sequence[JobSpec]) -> list[str]:
    return [j.idempotency_key for j in jobs]


# --- DAG order ----------------------------------------------------------------------------


def test_a_new_validation_installs_and_generates_every_feature() -> None:
    plan = plan_validation(snapshot(features=("store", "missions")))
    assert keys(plan.jobs) == [
        "validation:7:install",
        "validation:7:generate:store",
        "validation:7:generate:missions",
    ]
    assert plan.jobs[0].type is JobType.INSTALL_BUILD
    assert plan.jobs[0].requires == {"platform": "android"}
    assert plan.jobs[0].priority > plan.jobs[1].priority
    assert plan.jobs[1].payload == {"validation_id": 7, "feature": "store", "n": 4, "seed": None}
    assert plan.status is ValidationStatus.RUNNING


def test_generation_is_followed_by_static_scoring() -> None:
    state = FeatureState(feature="store", generate=OK, generation_run_id=12)
    plan = plan_validation(snapshot(install=JobStatus.LEASED, feature_states={"store": state}))
    assert keys(plan.jobs) == ["validation:7:score-static:12"]
    assert plan.jobs[0].payload["stage"] == "static"


def test_nothing_new_while_generation_runs() -> None:
    state = FeatureState(feature="store", generate=JobStatus.LEASED)
    plan = plan_validation(snapshot(install=OK, feature_states={"store": state}))
    assert plan.jobs == ()
    assert plan.status is ValidationStatus.RUNNING


def test_runs_wait_for_the_install() -> None:
    state = scored(candidate())
    plan = plan_validation(snapshot(install=JobStatus.QUEUED, feature_states={"store": state}))
    assert plan.jobs == ()


def test_only_g1_passes_get_clean_runs() -> None:
    plan = plan_validation(ready(candidate(1), candidate(2, g1=False), candidate(3, g1=None)))
    assert keys(plan.jobs) == ["run:prove-12:c1:clean:r1"]
    payload = plan.jobs[0].payload
    assert payload["flags"] == []
    assert payload["purpose"] == "clean"
    assert payload["run_group"] == "prove-12"
    assert payload["code_sha"] == f"{1:064d}"
    assert payload["manifest_sha"] == "m" * 64
    assert plan.jobs[0].requires == {"platform": "android"}


def test_one_clean_repeat_at_a_time() -> None:
    pending = [RunJob(repeat=1, status=JobStatus.LEASED)]
    assert plan_validation(ready(candidate(clean=pending))).jobs == ()
    assert keys(plan_validation(ready(candidate(clean=done(P)))).jobs) == [
        "run:prove-12:c1:clean:r2"
    ]


def test_relevant_dev_bugs_run_after_three_clean_passes() -> None:
    plan = plan_validation(ready(candidate(clean=done(P, P, P))))
    assert keys(plan.jobs) == ["run:prove-12:c1:flag_store:r1", "run:prove-12:c1:flag_store_2:r1"]
    assert plan.jobs[0].payload["flags"] == ["flag_store"]
    assert plan.jobs[0].payload["purpose"] == "bug"
    # flag_missions is skipped: the candidate never touches the missions page


def test_a_clean_failure_stops_the_candidate_and_scoring_follows() -> None:
    plan = plan_validation(ready(candidate(clean=done(P, A))))
    assert keys(plan.jobs) == ["validation:7:score-final:12"]


def test_a_bug_run_that_passes_is_not_repeated() -> None:
    c = candidate(clean=done(P, P, P), flag_store=done(P), flag_store_2=done(A, T))
    assert keys(plan_validation(ready(c)).jobs) == ["validation:7:score-final:12"]


def test_a_bug_run_that_fails_is_repeated_once() -> None:
    c = candidate(clean=done(P, P, P), flag_store=done(A), flag_store_2=done(P))
    assert keys(plan_validation(ready(c)).jobs) == ["run:prove-12:c1:flag_store:r2"]


def test_scoring_waits_for_every_candidate() -> None:
    plan = plan_validation(ready(candidate(1, clean=done(P, A)), candidate(2, clean=done(P))))
    assert keys(plan.jobs) == ["run:prove-12:c2:clean:r2"]


def test_a_dead_run_is_replaced_by_an_extra_repeat_up_to_the_limit() -> None:
    dead = RunJob(repeat=1, status=DEAD)
    plan = plan_validation(ready(candidate(clean=[dead])))
    assert keys(plan.jobs) == ["run:prove-12:c1:clean:r2"]
    every_repeat_dead = [RunJob(repeat=i, status=DEAD) for i in range(1, 3 + EXTRA_REPEATS + 1)]
    plan = plan_validation(ready(candidate(clean=every_repeat_dead)))
    assert keys(plan.jobs) == ["validation:7:score-final:12"]  # out of repeats: G2 inconclusive


def test_bug_runs_also_stop_at_the_repeat_limit() -> None:
    dead = [RunJob(repeat=i, status=DEAD) for i in range(1, 2 + EXTRA_REPEATS + 1)]
    c = candidate(clean=done(P, P, P), flag_store=dead, flag_store_2=done(P))
    assert keys(plan_validation(ready(c)).jobs) == ["validation:7:score-final:12"]


def test_planning_twice_gives_the_same_jobs() -> None:
    snap = ready(candidate(1, clean=done(P, P, P)), candidate(2))
    assert plan_validation(snap) == plan_validation(snap)


# --- validation status --------------------------------------------------------------------


def test_the_validation_succeeds_when_every_feature_is_scored() -> None:
    plan = plan_validation(ready(candidate(clean=done(P, A)), final=OK))
    assert plan.jobs == ()
    assert plan.status is ValidationStatus.SUCCEEDED
    assert plan.problems == ()


def test_scoring_in_progress_keeps_it_running() -> None:
    plan = plan_validation(ready(candidate(clean=done(P, A)), final=JobStatus.LEASED))
    assert plan.status is ValidationStatus.RUNNING


def test_a_failed_feature_is_reported_but_the_others_count() -> None:
    states = {
        "store": scored(candidate(clean=done(A)), final=OK),
        "missions": FeatureState(feature="missions", generate=DEAD),
    }
    plan = plan_validation(
        snapshot(install=OK, features=("store", "missions"), feature_states=states)
    )
    assert plan.status is ValidationStatus.SUCCEEDED
    assert plan.problems == ("missions: GENERATE dead",)


def test_no_scored_feature_means_failed() -> None:
    state = FeatureState(feature="store", generate=OK, generation_run_id=12, score_static=DEAD)
    plan = plan_validation(snapshot(install=OK, feature_states={"store": state}))
    assert plan.status is ValidationStatus.FAILED
    assert plan.problems == ("store: SCORE static dead",)


def test_a_generate_without_a_run_is_a_problem() -> None:
    state = FeatureState(feature="store", generate=OK)
    plan = plan_validation(snapshot(install=OK, feature_states={"store": state}))
    assert plan.status is ValidationStatus.FAILED
    assert "left no generation run" in plan.problems[0]


@pytest.mark.parametrize("status", [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.DEAD])
def test_final_scoring_states(status: JobStatus) -> None:
    plan = plan_validation(ready(candidate(clean=done(A)), final=status))
    expected = ValidationStatus.SUCCEEDED if status is OK else ValidationStatus.FAILED
    assert plan.status is expected


def test_a_failed_install_fails_the_validation_and_plans_no_runs() -> None:
    plan = plan_validation(snapshot(install=DEAD, feature_states={"store": scored(candidate())}))
    assert plan.status is ValidationStatus.FAILED
    assert plan.jobs == ()
    assert plan.problems[0].startswith("INSTALL_BUILD dead")


def test_a_failed_install_still_lets_generation_finish() -> None:
    plan = plan_validation(snapshot(install=JobStatus.FAILED))
    assert keys(plan.jobs) == ["validation:7:generate:store"]


def test_an_empty_generation_run_goes_straight_to_final_scoring() -> None:
    plan = plan_validation(ready())
    assert keys(plan.jobs) == ["validation:7:score-final:12"]


# --- parity with `pg prove` ---------------------------------------------------------------


def prove_loop(
    oracle: Callable[[str, int], Outcome],
    need_more: Callable[[Sequence[Execution]], bool],
    key: str,
    limit: int,
) -> list[Outcome]:
    """The loop `pg prove` runs for one (candidate, flags) (`execute_until`)."""
    results: list[Execution] = []
    repeat = 0
    while need_more(results) and repeat < limit:
        repeat += 1
        results.append(Execution(outcome=oracle(key, repeat), duration_s=1.0))
    return [r.outcome for r in results]


def drive(oracle: Callable[[str, int], Outcome]) -> dict[str, list[Outcome]]:
    """Run the planner to the end, completing each RUN_TEST job with the oracle's outcome
    (INFRA becomes a dead job, as the agent fails the job after infra on every attempt)."""
    runs: dict[str, list[RunJob]] = {}
    for _ in range(100):
        c = candidate(**runs)
        jobs = [j for j in plan_validation(ready(c)).jobs if j.type is JobType.RUN_TEST]
        if not jobs:
            break
        for job in jobs:
            flags = job.payload["flags"]
            key = flags[0] if flags else CLEAN
            repeat = int(job.payload["repeat"])
            outcome = oracle(key, repeat)
            run = (
                RunJob(repeat=repeat, status=DEAD)
                if outcome is INF
                else RunJob(repeat=repeat, status=OK, outcome=outcome)
            )
            runs.setdefault(key, []).append(run)
    return {
        key: [r.outcome if r.outcome is not None else INF for r in jobs]
        for key, jobs in runs.items()
    }


SEQUENCES = list(itertools.product([P, A, INF], repeat=3))


@pytest.mark.parametrize("clean", SEQUENCES)
@pytest.mark.parametrize("bug", [(P,), (A, A), (A, P), (INF, A, A), (INF, INF, INF, INF)])
def test_the_planner_makes_the_same_runs_as_pg_prove(
    clean: tuple[Outcome, ...], bug: tuple[Outcome, ...]
) -> None:
    script = {CLEAN: [*clean, P, P, P], "flag_store": [*bug, P, P], "flag_store_2": [P]}

    def oracle(key: str, repeat: int) -> Outcome:
        return script[key][repeat - 1]

    planned = drive(oracle)
    expected = {CLEAN: prove_loop(oracle, determinism_needs_more, CLEAN, 3 + EXTRA_REPEATS)}
    clean_runs = [Execution(outcome=o, duration_s=1.0) for o in expected[CLEAN]]
    if check_determinism(clean_runs).passed:
        for flag in ("flag_store", "flag_store_2"):
            expected[flag] = prove_loop(oracle, detection_needs_more, flag, 2 + EXTRA_REPEATS)
    assert planned == expected


def test_a_pending_bug_run_holds_its_bug_but_not_the_others() -> None:
    c = candidate(
        clean=done(P, P, P),
        flag_store=[RunJob(repeat=1, status=JobStatus.QUEUED)],
        flag_store_2=done(A),
    )
    assert keys(plan_validation(ready(c)).jobs) == ["run:prove-12:c1:flag_store_2:r2"]


def test_nothing_new_while_static_scoring_runs() -> None:
    state = FeatureState(
        feature="store", generate=OK, generation_run_id=12, score_static=JobStatus.LEASED
    )
    plan = plan_validation(snapshot(install=OK, feature_states={"store": state}))
    assert plan.jobs == ()
    assert plan.status is ValidationStatus.RUNNING


def test_bug_runs_wait_while_the_device_is_not_ready() -> None:
    state = scored(candidate(clean=done(P, P, P)))
    plan = plan_validation(snapshot(install=JobStatus.LEASED, feature_states={"store": state}))
    assert plan.jobs == ()


def test_run_key_of() -> None:
    assert run_key_of([]) == CLEAN
    assert run_key_of(["flag_store"]) == "flag_store"
