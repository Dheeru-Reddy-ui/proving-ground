"""The Phase 1 run report (`pg report`), built only from stored rows (CLAUDE.md rule 2).

Pure: the CLI loads the rows from Postgres and passes them in; this module counts and renders.
Every report names the run IDs it came from.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class RunInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    generation_run_id: int
    feature: str
    model: str
    prompt_version: str
    prompt_hash: str
    status: str
    n_requested: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    prove_run_group: str


class CandidateRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    generation_run_id: int
    name: str
    spec_ids: tuple[str, ...]
    decision: str
    human_decision: str | None
    trust_score: int | None
    reasons: tuple[dict[str, Any], ...]
    reached_g2: bool
    passed_g2: bool


class ExecutionRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_group: str
    purpose: str
    outcome: str
    wall_ms: int


class KillRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str  # "c12 test_name" or "human test_name"
    bug: str
    killed: bool
    unstable: bool


class ReportData(BaseModel):
    model_config = ConfigDict(frozen=True)

    build_sha256: str
    runs: tuple[RunInfo, ...]
    baseline_run_groups: tuple[str, ...]
    candidates: tuple[CandidateRow, ...]
    executions: tuple[ExecutionRow, ...]
    kills: tuple[KillRow, ...]
    dev_bugs: tuple[str, ...]
    generated_at: str


DECISIONS = ("accept", "review", "reject", "pending")


def summarize(data: ReportData) -> dict[str, Any]:
    by_decision = Counter(c.decision for c in data.candidates)
    reasons = Counter(
        str(r.get("code")) for c in data.candidates if c.decision == "reject" for r in c.reasons
    )
    reached = [c for c in data.candidates if c.reached_g2]
    passed = [c for c in reached if c.passed_g2]
    accepted = by_decision.get("accept", 0)
    approved = sum(
        1 for c in data.candidates if c.decision == "review" and c.human_decision == "accept"
    )
    cost = sum((r.cost_usd for r in data.runs), Decimal(0))
    prove_groups = {r.prove_run_group for r in data.runs}
    device_ms = sum(e.wall_ms for e in data.executions if e.run_group in prove_groups)
    baseline_ms = sum(e.wall_ms for e in data.executions if e.run_group in data.baseline_run_groups)
    infra = sum(1 for e in data.executions if e.outcome == "infra")
    return {
        "candidates": len(data.candidates),
        "by_decision": {d: by_decision.get(d, 0) for d in DECISIONS},
        "human_approved_reviews": approved,
        "rejection_reasons": dict(reasons.most_common()),
        "g2": {"reached": len(reached), "passed": len(passed)},
        "llm_cost_usd": str(cost),
        "tokens_in": sum(r.tokens_in for r in data.runs),
        "tokens_out": sum(r.tokens_out for r in data.runs),
        "device_minutes_prove": round(device_ms / 60000, 1),
        "device_minutes_baseline": round(baseline_ms / 60000, 1),
        "infra_attempts": infra,
        "executions": len(data.executions),
        "cost_per_accepted_usd": str(cost / accepted) if accepted else None,
        "device_minutes_per_accepted": round(device_ms / 60000 / accepted, 1) if accepted else None,
    }


def kill_matrix(data: ReportData) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Rows (subjects) and cells: K = kill (2/2), u = unstable (1/2), . = survived."""
    rows: dict[str, dict[str, str]] = {}
    for kill in data.kills:
        cell = "K" if kill.killed else ("u" if kill.unstable else ".")
        rows.setdefault(kill.subject, {})[kill.bug] = cell
    return sorted(rows, key=_subject_order), rows


def _subject_order(subject: str) -> tuple[int, int, str]:
    """Candidates by number (c9 before c10), then human tests by name."""
    head, _, name = subject.partition(" ")
    if head.startswith("c") and head[1:].isdigit():
        return (0, int(head[1:]), name)
    return (1, 0, subject)


def render_markdown(data: ReportData) -> str:
    s = summarize(data)
    lines = ["# Proving Ground run report", ""]
    lines.append(
        f"Generated {data.generated_at} by `pg report` from stored rows. "
        f"Build `{data.build_sha256[:12]}`."
    )
    lines.append("")
    lines.append("| Generation run | Feature | Model | Prompt | Status | Requested | Prove run |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in data.runs:
        lines.append(
            f"| {r.generation_run_id} | {r.feature} | {r.model} "
            f"| {r.prompt_version} (`{r.prompt_hash[:12]}`) | {r.status} | {r.n_requested} "
            f"| `{r.prove_run_group}` |"
        )
    baseline = ", ".join(f"`{g}`" for g in data.baseline_run_groups) or "none"
    lines += ["", f"Human baseline runs used for novelty and the kill matrix: {baseline}.", ""]

    lines += ["## Candidates by decision", "", "| Decision | Count |", "|---|---|"]
    for decision, count in s["by_decision"].items():
        lines.append(f"| {decision} | {count} |")
    lines.append(f"| review approved by a human | {s['human_approved_reviews']} |")
    lines += ["", "## Main rejection reasons", ""]
    if s["rejection_reasons"]:
        lines += ["| Reason | Rejections |", "|---|---|"]
        lines += [f"| `{code}` | {count} |" for code, count in s["rejection_reasons"].items()]
    else:
        lines.append("No rejections.")
    g2 = s["g2"]
    rate = f"{g2['passed']}/{g2['reached']}" if g2["reached"] else "n/a"
    lines += [
        "",
        "## Determinism (G2)",
        "",
        f"Candidates that passed 3/3 clean runs: **{rate}** of those that reached G2.",
        "",
    ]

    lines += [
        "## Dev kill matrix",
        "",
        "K = killed 2/2, u = failed 1/2 (unstable, not counted), . = survived, "
        "blank = not run (relevance filter).",
        "",
    ]
    subjects, cells = kill_matrix(data)
    if subjects:
        lines.append("| Test | " + " | ".join(data.dev_bugs) + " |")
        lines.append("|---|" + "---|" * len(data.dev_bugs))
        for subject in subjects:
            row = cells[subject]
            lines.append(
                f"| {subject} | " + " | ".join(row.get(b, "") for b in data.dev_bugs) + " |"
            )
    else:
        lines.append("No bug runs recorded.")

    lines += ["", "## Cost and device time", "", "| Measure | Value |", "|---|---|"]
    lines.append(f"| LLM tokens in / out | {s['tokens_in']} / {s['tokens_out']} |")
    lines.append(f"| LLM cost (configured rates) | ${s['llm_cost_usd']} |")
    per = s["cost_per_accepted_usd"]
    lines.append(
        f"| LLM cost per accepted test | {'$' + per if per is not None else 'n/a (no ACCEPT)'} |"
    )
    lines.append(f"| Device time, proving | {s['device_minutes_prove']} min |")
    dpa = s["device_minutes_per_accepted"]
    per_accepted = f"{dpa} min" if dpa is not None else "n/a (no ACCEPT)"
    lines.append(f"| Device time per accepted test | {per_accepted} |")
    lines.append(f"| Device time, human baseline | {s['device_minutes_baseline']} min |")
    lines.append(
        f"| Executions (attempts) / infra attempts | {s['executions']} / {s['infra_attempts']} |"
    )

    lines += [
        "",
        "## Candidates",
        "",
        "| Id | Name | Specs | Decision | Trust | First reason |",
        "|---|---|---|---|---|---|",
    ]
    for c in sorted(data.candidates, key=lambda c: c.id):
        first = c.reasons[0].get("message", "") if c.reasons else ""
        decision = c.decision + (f" (human: {c.human_decision})" if c.human_decision else "")
        trust = "" if c.trust_score is None else str(c.trust_score)
        reason_text = str(first).replace("|", "/")[:120]
        lines.append(
            f"| {c.id} | `{c.name}` | {', '.join(c.spec_ids)} | {decision} "
            f"| {trust} | {reason_text} |"
        )
    return "\n".join(lines) + "\n"


def render_json(data: ReportData) -> dict[str, Any]:
    subjects, cells = kill_matrix(data)
    return {
        "build_sha256": data.build_sha256,
        "generated_at": data.generated_at,
        "generation_run_ids": [r.generation_run_id for r in data.runs],
        "prove_run_groups": [r.prove_run_group for r in data.runs],
        "baseline_run_groups": list(data.baseline_run_groups),
        "summary": summarize(data),
        "kill_matrix": {"bugs": list(data.dev_bugs), "rows": {s: cells[s] for s in subjects}},
        "candidates": [c.model_dump(mode="json") for c in data.candidates],
    }


def subject_label(kind: str, ident: int, name: str) -> str:
    return f"{'c' + str(ident) if kind == 'candidate' else 'human'} {name}"


def ordered(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))
