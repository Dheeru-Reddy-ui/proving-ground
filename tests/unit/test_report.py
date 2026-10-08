"""The run report counts only what the stored rows say."""

from __future__ import annotations

from decimal import Decimal

from pg_core.report import (
    CandidateRow,
    ExecutionRow,
    KillRow,
    ReportData,
    RunInfo,
    kill_matrix,
    render_json,
    render_markdown,
    subject_label,
    summarize,
)


def candidate(
    cid: int,
    decision: str,
    *,
    reached: bool = True,
    passed: bool = True,
    human: str | None = None,
    code: str = "x",
) -> CandidateRow:
    return CandidateRow(
        id=cid,
        generation_run_id=1,
        name=f"test_{cid}",
        spec_ids=("STORE-5",),
        decision=decision,
        human_decision=human,
        trust_score=50,
        reasons=({"code": code, "message": f"{code} | detail"},),
        reached_g2=reached,
        passed_g2=passed,
    )


def data(**overrides: object) -> ReportData:
    base = {
        "build_sha256": "f" * 64,
        "runs": (
            RunInfo(
                generation_run_id=1,
                feature="store",
                model="gemini-3.8-flash",
                prompt_version="generate_tests_v1",
                prompt_hash="a" * 64,
                status="succeeded",
                n_requested=8,
                tokens_in=10000,
                tokens_out=5000,
                cost_usd=Decimal("0.03"),
                prove_run_group="prove-1",
            ),
        ),
        "baseline_run_groups": ("baseline-x",),
        "candidates": (
            candidate(1, "accept"),
            candidate(2, "accept"),
            candidate(3, "review", human="accept"),
            candidate(4, "reject", reached=False, passed=False, code="unknown_sdk_member"),
            candidate(5, "reject", passed=False, code="not_deterministic"),
            candidate(6, "reject", reached=False, passed=False, code="unknown_sdk_member"),
        ),
        "executions": (
            ExecutionRow(run_group="prove-1", purpose="clean", outcome="passed", wall_ms=60000),
            ExecutionRow(run_group="prove-1", purpose="bug", outcome="infra", wall_ms=60000),
            ExecutionRow(run_group="baseline-x", purpose="clean", outcome="passed", wall_ms=30000),
            ExecutionRow(run_group="other", purpose="clean", outcome="passed", wall_ms=999999),
        ),
        "kills": (
            KillRow(subject="c1 test_1", bug="SB02", killed=True, unstable=False),
            KillRow(subject="c1 test_1", bug="SB05", killed=False, unstable=True),
            KillRow(subject="human test_h", bug="SB02", killed=False, unstable=False),
        ),
        "dev_bugs": ("SB02", "SB05", "SB16"),
        "generated_at": "2026-10-08 10:00 UTC",
    }
    base.update(overrides)
    return ReportData.model_validate(base)


def test_summary_counts() -> None:
    s = summarize(data())
    assert s["by_decision"] == {"accept": 2, "review": 1, "reject": 3, "pending": 0}
    assert s["human_approved_reviews"] == 1
    assert s["rejection_reasons"] == {"unknown_sdk_member": 2, "not_deterministic": 1}
    assert s["g2"] == {"reached": 4, "passed": 3}
    assert s["cost_per_accepted_usd"] == "0.015"
    assert s["device_minutes_prove"] == 2.0  # the unrelated run group is not counted
    assert s["device_minutes_per_accepted"] == 1.0
    assert s["device_minutes_baseline"] == 0.5
    assert s["infra_attempts"] == 1


def test_no_accept_means_no_per_accept_figures() -> None:
    s = summarize(data(candidates=(candidate(1, "reject", code="redundant"),)))
    assert s["cost_per_accepted_usd"] is None
    assert s["device_minutes_per_accepted"] is None
    assert "n/a (no ACCEPT)" in render_markdown(data(candidates=(candidate(1, "review"),)))


def test_kill_matrix_cells() -> None:
    subjects, cells = kill_matrix(data())
    assert subjects == ["c1 test_1", "human test_h"]
    assert cells["c1 test_1"] == {"SB02": "K", "SB05": "u"}
    assert cells["human test_h"] == {"SB02": "."}


def test_markdown_names_its_run_ids_and_escapes_pipes() -> None:
    text = render_markdown(data())
    assert "`prove-1`" in text
    assert "`baseline-x`" in text
    assert "| c1 test_1 | K | u |  |" in text
    assert "unknown_sdk_member / detail" in text
    assert "No rejections." in render_markdown(data(candidates=()))
    assert "No bug runs recorded." in render_markdown(data(kills=()))


def test_json_carries_the_same_numbers() -> None:
    payload = render_json(data())
    assert payload["generation_run_ids"] == [1]
    assert payload["prove_run_groups"] == ["prove-1"]
    assert payload["kill_matrix"]["rows"]["c1 test_1"]["SB02"] == "K"
    assert payload["summary"]["by_decision"]["accept"] == 2


def test_subject_labels() -> None:
    assert subject_label("candidate", 12, "test_a") == "c12 test_a"
    assert subject_label("human", 3, "test_b") == "human test_b"
