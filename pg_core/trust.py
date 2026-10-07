"""The trust decision (ADR-0004): ACCEPT, REVIEW or REJECT a candidate from its gate results.

- REJECT if G1, G2 or G5 fails, or G4 finds the candidate redundant.
- ACCEPT if it kills at least one dev bug (G3) and passes every gate.
- REVIEW otherwise: it passes but kills no dev bug, which may only mean the catalog does not
  cover its area, so a human decides.
- PENDING (no decision yet) while a gate is inconclusive because infra failures left too few
  valid runs.

`trust_score` (0-100) ranks candidates and orders the novelty check; it never changes a decision.
Within one generation run, candidates are taken in trust-score order and each ACCEPT joins the
suite G4 compares against, so near-identical siblings cannot all be accepted.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from pg_core.gates.base import GateResult, Reason, reason
from pg_core.gates.runs import Detection, SuiteEntry, check_novelty
from pg_core.gates.static import StaticReport

# trust_score weights; they sum to 100.
WEIGHT_KILLS = 40  # scaled by min(kills, 2) / 2
WEIGHT_DETERMINISTIC = 20  # G2 passed
WEIGHT_VISIBLE_ASSERTIONS = 15  # scaled by min(visible assertions, 3) / 3
WEIGHT_SPEED = 15  # scaled by 1 - median runtime / budget
WEIGHT_STABLE_KILLS = 10  # no unstable (1-of-2) kill


class Decision(StrEnum):
    ACCEPT = "accept"
    REVIEW = "review"
    REJECT = "reject"
    PENDING = "pending"


class Evidence(BaseModel):
    """Everything the gates found about one candidate."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    g1: GateResult
    static: StaticReport
    g2: GateResult | None = None
    g3: GateResult | None = None
    detection: Detection | None = None
    g5: GateResult | None = None


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    decision: Decision
    reasons: tuple[Reason, ...]
    trust_score: int = Field(ge=0, le=100)
    gates: dict[str, GateResult]


def trust_score(evidence: Evidence) -> int:
    """Ranking score from documented weights; 0 for a candidate that failed G1."""
    if not evidence.g1.passed:
        return 0
    kills = len(evidence.detection.kills) if evidence.detection else 0
    unstable = len(evidence.detection.unstable) if evidence.detection else 0
    score = WEIGHT_KILLS * min(kills, 2) / 2
    if evidence.g2 is not None and evidence.g2.passed:
        score += WEIGHT_DETERMINISTIC
    score += WEIGHT_VISIBLE_ASSERTIONS * min(evidence.static.visible_assertions, 3) / 3
    if evidence.g5 is not None and evidence.g5.passed:
        median = float(evidence.g5.metrics.get("median_s", 0.0))
        budget = float(evidence.g5.metrics.get("budget_s", 1.0)) or 1.0
        score += WEIGHT_SPEED * max(0.0, 1.0 - min(median / budget, 1.0))
    if evidence.detection is not None and unstable == 0:
        score += WEIGHT_STABLE_KILLS
    return round(score)


def decide(evidence: Evidence, g4: GateResult | None) -> Verdict:
    gates = {"G1": evidence.g1}
    for name, result in (("G2", evidence.g2), ("G3", evidence.g3), ("G4", g4), ("G5", evidence.g5)):
        if result is not None:
            gates[name] = result
    score = trust_score(evidence)

    def verdict(decision: Decision, reasons: Sequence[Reason]) -> Verdict:
        return Verdict(
            candidate_id=evidence.candidate_id,
            decision=decision,
            reasons=tuple(reasons),
            trust_score=score,
            gates=gates,
        )

    if not evidence.g1.passed:
        return verdict(Decision.REJECT, evidence.g1.reasons)
    hard_failures = [
        r
        for g in (evidence.g2, evidence.g5)
        if g is not None and not g.passed and not g.inconclusive
        for r in g.reasons
    ]
    if hard_failures:
        return verdict(Decision.REJECT, hard_failures)
    missing = [
        name
        for name, g in (("G2", evidence.g2), ("G3", evidence.g3), ("G5", evidence.g5))
        if g is None or g.inconclusive
    ]
    if missing:
        return verdict(
            Decision.PENDING,
            [reason("pending", f"waiting for valid runs: {', '.join(missing)}")],
        )
    if g4 is None:
        return verdict(Decision.PENDING, [reason("pending", "novelty not checked yet")])
    if not g4.passed:
        return verdict(Decision.REJECT, g4.reasons)
    if evidence.g3 is not None and evidence.g3.passed:  # G3 is present: checked above
        kills = ", ".join(evidence.detection.kills) if evidence.detection else ""
        return verdict(Decision.ACCEPT, [reason("kills_dev_bugs", f"kills {kills}")])
    return verdict(
        Decision.REVIEW,
        [
            *(evidence.g3.reasons if evidence.g3 is not None else ()),
            reason(
                "needs_human",
                "passes every gate but kills no dev bug; the catalog may not cover it",
            ),
        ],
    )


def decide_batch(candidates: Sequence[Evidence], suite: Sequence[SuiteEntry]) -> list[Verdict]:
    """Decide a generation run's candidates in trust-score order (ties by candidate id); each
    ACCEPT joins the suite that later candidates' novelty is checked against."""
    ordered = sorted(candidates, key=lambda e: (-trust_score(e), e.candidate_id))
    reference = list(suite)
    verdicts: list[Verdict] = []
    for evidence in ordered:
        ready = evidence.g1.passed and all(
            g is not None and not g.inconclusive for g in (evidence.g2, evidence.g3, evidence.g5)
        )
        kills = evidence.detection.kills if evidence.detection else ()
        g4 = check_novelty(kills, evidence.static.spec_ids, reference) if ready else None
        result = decide(evidence, g4)
        if result.decision is Decision.ACCEPT:
            reference.append(
                SuiteEntry(
                    name=evidence.candidate_id,
                    kills=frozenset(kills),
                    spec_ids=frozenset(evidence.static.spec_ids),
                )
            )
        verdicts.append(result)
    return verdicts
