"""G2-G5 and the trust decision, including adversarial and infra cases."""

from __future__ import annotations

from pg_core.gates.base import Execution, GateResult, Outcome
from pg_core.gates.runs import (
    BugRef,
    SuiteEntry,
    check_cost,
    check_detection,
    check_determinism,
    check_novelty,
    detection_needs_more,
    determinism_needs_more,
    relevant_bugs,
)
from pg_core.gates.static import StaticReport
from pg_core.trust import Decision, Evidence, decide, decide_batch, trust_score

P, A, T, G, E, INF = (
    Outcome.PASSED,
    Outcome.ASSERTION,
    Outcome.PG_TIMEOUT,
    Outcome.GAME_ERROR,
    Outcome.TEST_ERROR,
    Outcome.INFRA,
)


def runs(*outcomes: Outcome, duration: float = 10.0) -> list[Execution]:
    return [Execution(outcome=o, duration_s=duration) for o in outcomes]


# --- G2 -----------------------------------------------------------------------------------


def test_g2_passes_three_of_three() -> None:
    result = check_determinism(runs(P, P, P))
    assert result.passed
    assert result.metrics["passed_runs"] == 3


def test_g2_fails_on_any_failure_even_before_three_runs() -> None:
    result = check_determinism(runs(P, A))
    assert not result.passed
    assert not result.inconclusive
    assert result.reasons[0].code == "not_deterministic"
    assert not determinism_needs_more(runs(P, A))


def test_g2_treats_a_test_crash_as_a_failure() -> None:
    assert not check_determinism(runs(P, P, E)).passed


def test_g2_ignores_infra_and_waits_for_valid_runs() -> None:
    result = check_determinism(runs(P, INF, P))
    assert result.inconclusive
    assert result.metrics["infra_runs"] == 1
    assert determinism_needs_more(runs(P, INF, P))
    assert check_determinism(runs(P, INF, P, P)).passed


# --- G3 -----------------------------------------------------------------------------------

BUGS = [
    BugRef(id="SB02", flag="sb_a", pages=("store",)),
    BugRef(id="SB05", flag="sb_b", pages=("store",)),
    BugRef(id="SB11", flag="sb_c", pages=("missions",)),
    BugRef(id="SB16", flag="sb_d", pages=("store",)),
    BugRef(id="SB13", flag="sb_e", pages=("game_over",)),
]


def test_relevance_filter_logs_every_skipped_bug() -> None:
    relevant, skipped = relevant_bugs(["store", "player"], BUGS)
    assert [b.id for b in relevant] == ["SB02", "SB05", "SB16"]
    assert set(skipped) == {"SB11", "SB13"}
    assert "missions" in skipped["SB11"]


def test_g3_counts_only_two_of_two_product_failures() -> None:
    result, detection = check_detection(
        ["store"],
        BUGS[:2] + BUGS[3:4],
        {"sb_a": runs(A, T), "sb_b": runs(A, P), "sb_d": runs(G, G)},
    )
    assert result.passed
    assert detection.kills == ("SB02", "SB16")
    assert detection.unstable == ("SB05",)
    assert any(r.code == "unstable_kill" for r in result.reasons)


def test_g3_never_counts_infra_or_test_crashes_as_kills() -> None:
    bugs = [BugRef(id="SB02", flag="sb_a", pages=("store",))]
    result, detection = check_detection(["store"], bugs, {"sb_a": runs(E, E)})
    assert not result.passed
    assert detection.survived == ("SB02",)
    assert result.reasons[0].code == "no_kill"
    pending, _ = check_detection(["store"], bugs, {"sb_a": runs(INF, A)})
    assert pending.inconclusive
    assert detection_needs_more(runs(INF, A))
    done, detection = check_detection(["store"], bugs, {"sb_a": runs(INF, A, A)})
    assert done.passed
    assert detection.kills == ("SB02",)


def test_g3_with_no_relevant_bug_is_a_pass_through_to_review() -> None:
    result, detection = check_detection(["leaderboard"], BUGS, {})
    assert not result.passed
    assert not result.inconclusive
    assert detection.relevant == ()


# --- G4 and G5 ----------------------------------------------------------------------------

SUITE = [
    SuiteEntry(name="human_store", kills=frozenset({"SB02"}), spec_ids=frozenset({"STORE-5"})),
]


def test_g4_novelty_by_kill_or_spec() -> None:
    assert check_novelty({"SB16"}, {"STORE-5"}, SUITE).passed
    assert check_novelty(set(), {"STORE-8"}, SUITE).passed
    redundant = check_novelty({"SB02"}, {"STORE-5"}, SUITE)
    assert not redundant.passed
    assert redundant.reasons[0].code == "redundant"


def test_g5_uses_the_median_of_passing_runs() -> None:
    assert check_cost(runs(P, P, P, duration=30)).passed
    slow = check_cost(runs(P, P, P, duration=130))
    assert not slow.passed
    assert slow.metrics["median_s"] == 130
    assert check_cost(runs(A, INF)).inconclusive


# --- decision -----------------------------------------------------------------------------


def g1(passed: bool = True) -> GateResult:
    return GateResult(gate="G1", passed=passed)


def evidence(
    cid: str,
    kills: tuple[str, ...] = ("SB16",),
    *,
    spec: str = "STORE-8",
    clean: tuple[Outcome, ...] = (P, P, P),
    static_ok: bool = True,
    duration: float = 20.0,
) -> Evidence:
    bugs = [BugRef(id=b, flag=b.lower(), pages=("store",)) for b in ("SB02", "SB16")]
    by_flag = {b.flag: runs(A, A) if b.id in kills else runs(P, P) for b in bugs}
    g3, detection = check_detection(["store"], bugs, by_flag)
    return Evidence(
        candidate_id=cid,
        g1=g1(static_ok),
        static=StaticReport(spec_ids=(spec,), pages_used=("store",), visible_assertions=2),
        g2=check_determinism(runs(*clean, duration=duration)),
        g3=g3,
        detection=detection,
        g5=check_cost(runs(*clean, duration=duration)),
    )


def test_decision_rules() -> None:
    novelty = check_novelty({"SB16"}, {"STORE-8"}, SUITE)
    assert decide(evidence("a"), novelty).decision is Decision.ACCEPT
    assert (
        decide(evidence("b", kills=()), check_novelty((), {"STORE-8"}, SUITE)).decision
        is Decision.REVIEW
    )
    assert decide(evidence("c", static_ok=False), None).decision is Decision.REJECT
    assert decide(evidence("d", clean=(P, A)), None).decision is Decision.REJECT
    assert decide(evidence("e", duration=500), novelty).decision is Decision.REJECT
    assert decide(evidence("f", clean=(P, INF)), None).decision is Decision.PENDING
    assert decide(evidence("g"), None).decision is Decision.PENDING
    redundant = check_novelty({"SB02"}, {"STORE-5"}, SUITE)
    assert (
        decide(evidence("h", kills=("SB02",), spec="STORE-5"), redundant).decision
        is Decision.REJECT
    )


def test_trust_score_ranks_but_never_decides() -> None:
    strong = evidence("strong", kills=("SB02", "SB16"))
    weak = evidence("weak", kills=())
    assert 0 <= trust_score(weak) < trust_score(strong) <= 100
    assert trust_score(evidence("bad", static_ok=False)) == 0


def test_batch_rejects_siblings_that_add_nothing_new() -> None:
    twins = [evidence("b-twin"), evidence("a-twin"), evidence("other", kills=(), spec="STORE-9")]
    verdicts = {v.candidate_id: v for v in decide_batch(twins, SUITE)}
    # Same score: "a-twin" goes first by id and is accepted; its twin is then redundant.
    assert verdicts["a-twin"].decision is Decision.ACCEPT
    assert verdicts["b-twin"].decision is Decision.REJECT
    assert verdicts["b-twin"].reasons[0].code == "redundant"
    assert verdicts["other"].decision is Decision.REVIEW


def test_batch_leaves_pending_candidates_out_of_novelty() -> None:
    verdicts = decide_batch([evidence("p", clean=(P, INF))], SUITE)
    assert verdicts[0].decision is Decision.PENDING
    assert "G4" not in verdicts[0].gates


def test_g3_stops_once_a_kill_is_impossible() -> None:
    assert not detection_needs_more(runs(P))  # a pass: 2/2 can no longer happen
    assert detection_needs_more(runs(A))  # one failure: the second run decides
    assert detection_needs_more(runs(INF))  # no valid run yet
    bugs = [BugRef(id="SB02", flag="sb_a", pages=("store",))]
    result, detection = check_detection(["store"], bugs, {"sb_a": runs(P)})
    assert not result.inconclusive
    assert detection.survived == ("SB02",)
    pending, _ = check_detection(["store"], bugs, {"sb_a": runs(A)})
    assert pending.inconclusive
