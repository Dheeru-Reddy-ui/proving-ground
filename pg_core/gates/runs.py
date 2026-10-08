"""G2 determinism, G3 bug detection, G4 novelty and G5 cost: verdicts from recorded runs.

Infra runs never count for or against a test: they are retried by the runner, and a gate left
without enough valid runs is inconclusive (the candidate waits) rather than failed.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from pg_core.gates.base import Execution, GateResult, Outcome, reason

CLEAN_RUNS = 3  # G2: all must pass
BUG_RUNS = 2  # G3: a kill must reproduce in both
DEFAULT_RUNTIME_BUDGET_S = 120.0  # G5, approved 2026-10-07


# --- G2 -----------------------------------------------------------------------------------


def check_determinism(clean: Sequence[Execution], required: int = CLEAN_RUNS) -> GateResult:
    """G2: the test passes on the clean build in `required` of `required` valid runs."""
    valid = [e for e in clean if e.outcome.is_valid]
    failed = [e for e in valid if e.outcome is not Outcome.PASSED]
    metrics = {
        "valid_runs": len(valid),
        "passed_runs": len(valid) - len(failed),
        "infra_runs": len(clean) - len(valid),
        "outcomes": [e.outcome.value for e in clean],
    }
    if failed:
        kinds = ", ".join(sorted({e.outcome.value for e in failed}))
        return GateResult(
            gate="G2",
            passed=False,
            reasons=(
                reason(
                    "not_deterministic",
                    f"failed {len(failed)} of {len(valid)} clean runs ({kinds})",
                ),
            ),
            metrics=metrics,
        )
    if len(valid) < required:
        return GateResult(
            gate="G2",
            passed=False,
            inconclusive=True,
            reasons=(
                reason("too_few_clean_runs", f"{len(valid)} valid clean runs of {required} needed"),
            ),
            metrics=metrics,
        )
    return GateResult(gate="G2", passed=True, metrics=metrics)


def determinism_needs_more(clean: Sequence[Execution], required: int = CLEAN_RUNS) -> bool:
    """True while another clean run could still change G2 (no failure yet, too few runs)."""
    valid = [e for e in clean if e.outcome.is_valid]
    return all(e.outcome is Outcome.PASSED for e in valid) and len(valid) < required


# --- G3 -----------------------------------------------------------------------------------


class BugRef(BaseModel):
    """A dev bug as G3 needs it: its flag and the pages where it shows."""

    model_config = ConfigDict(frozen=True)

    id: str
    flag: str
    pages: tuple[str, ...]


class Detection(BaseModel):
    model_config = ConfigDict(frozen=True)

    relevant: tuple[str, ...]  # bug ids run against
    skipped: dict[str, str]  # bug id -> why it was not run
    kills: tuple[str, ...]  # 2 of 2 product failures
    unstable: tuple[str, ...]  # 1 of 2: does not count
    survived: tuple[str, ...]  # 0 of 2
    inconclusive: tuple[str, ...]  # fewer than 2 valid runs


def relevant_bugs(
    pages_used: Iterable[str], bugs: Sequence[BugRef]
) -> tuple[list[BugRef], dict[str, str]]:
    """Bugs whose pages the test touches. The filter only saves device time; skipped bugs are
    logged with the reason."""
    used = set(pages_used)
    relevant: list[BugRef] = []
    skipped: dict[str, str] = {}
    for bug in bugs:
        if used & set(bug.pages):
            relevant.append(bug)
        else:
            skipped[bug.id] = f"test touches none of {', '.join(bug.pages)}"
    return relevant, skipped


def check_detection(
    pages_used: Iterable[str],
    bugs: Sequence[BugRef],
    runs_by_flag: Mapping[str, Sequence[Execution]],
    required: int = BUG_RUNS,
) -> tuple[GateResult, Detection]:
    """G3: which dev bugs the test kills. A kill is a product-visible failure (assertion,
    PGTimeout or a logged game error) in `required` of `required` valid runs with the flag on."""
    relevant, skipped = relevant_bugs(pages_used, bugs)
    kills: list[str] = []
    unstable: list[str] = []
    survived: list[str] = []
    inconclusive: list[str] = []
    for bug in relevant:
        valid = [e for e in runs_by_flag.get(bug.flag, ()) if e.outcome.is_valid][:required]
        failures = sum(e.outcome.is_product_failure for e in valid)
        if len(valid) < required and failures == len(valid):
            inconclusive.append(bug.id)  # every valid run so far failed: a kill is still possible
        elif failures == required:
            kills.append(bug.id)
        elif failures > 0:
            unstable.append(bug.id)
        else:
            survived.append(bug.id)
    detection = Detection(
        relevant=tuple(b.id for b in relevant),
        skipped=skipped,
        kills=tuple(kills),
        unstable=tuple(unstable),
        survived=tuple(survived),
        inconclusive=tuple(inconclusive),
    )
    metrics = {
        "kills": list(detection.kills),
        "unstable": list(detection.unstable),
        "relevant": len(relevant),
        "skipped": len(skipped),
    }
    if inconclusive:
        return (
            GateResult(
                gate="G3",
                passed=False,
                inconclusive=True,
                reasons=(
                    reason(
                        "too_few_bug_runs",
                        f"bugs without {required} valid runs: {', '.join(inconclusive)}",
                    ),
                ),
                metrics=metrics,
            ),
            detection,
        )
    reasons = []
    if unstable:
        reasons.append(
            reason(
                "unstable_kill", f"failed in only some runs (not counted): {', '.join(unstable)}"
            )
        )
    if not kills:
        reasons.append(reason("no_kill", f"killed none of {len(relevant)} relevant dev bug(s)"))
    return GateResult(
        gate="G3", passed=bool(kills), reasons=tuple(reasons), metrics=metrics
    ), detection


def detection_needs_more(runs: Sequence[Execution], required: int = BUG_RUNS) -> bool:
    """True while another bug run could still make a kill: fewer than `required` valid runs and
    every valid run so far failed. Once one passes, a 2-of-2 kill is impossible, so the second
    run is skipped (ADR-0004); a pass-then-fail pair is then recorded as survived, not unstable."""
    valid = [e for e in runs if e.outcome.is_valid]
    return len(valid) < required and all(e.outcome.is_product_failure for e in valid)


# --- G4 -----------------------------------------------------------------------------------


class SuiteEntry(BaseModel):
    """A test already in the suite (human baseline, accepted, or accepted earlier this run)."""

    model_config = ConfigDict(frozen=True)

    name: str
    kills: frozenset[str]
    spec_ids: frozenset[str]


def check_novelty(
    kills: Iterable[str], spec_ids: Iterable[str], suite: Sequence[SuiteEntry]
) -> GateResult:
    """G4: the candidate adds a dev kill or a spec ID the suite does not already have."""
    known_kills = frozenset().union(*(e.kills for e in suite))
    known_specs = frozenset().union(*(e.spec_ids for e in suite))
    new_kills = sorted(set(kills) - known_kills)
    new_specs = sorted(set(spec_ids) - known_specs)
    metrics = {"new_kills": new_kills, "new_spec_ids": new_specs, "suite_size": len(suite)}
    if new_kills or new_specs:
        return GateResult(gate="G4", passed=True, metrics=metrics)
    return GateResult(
        gate="G4",
        passed=False,
        reasons=(
            reason(
                "redundant", f"adds no new kill and no new spec ID over {len(suite)} suite test(s)"
            ),
        ),
        metrics=metrics,
    )


# --- G5 -----------------------------------------------------------------------------------


def check_cost(
    clean: Sequence[Execution], budget_s: float = DEFAULT_RUNTIME_BUDGET_S
) -> GateResult:
    """G5: the median runtime of the passing clean runs is within the budget."""
    durations = [e.duration_s for e in clean if e.outcome is Outcome.PASSED]
    if not durations:
        return GateResult(
            gate="G5",
            passed=False,
            inconclusive=True,
            reasons=(reason("no_passing_runs", "no passing clean run to time"),),
        )
    median = statistics.median(durations)
    metrics = {"median_s": round(median, 3), "budget_s": budget_s}
    if median > budget_s:
        return GateResult(
            gate="G5",
            passed=False,
            reasons=(reason("too_slow", f"median {median:.1f}s exceeds the {budget_s:g}s budget"),),
            metrics=metrics,
        )
    return GateResult(gate="G5", passed=True, metrics=metrics)
