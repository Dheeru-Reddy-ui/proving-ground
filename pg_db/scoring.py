"""Gate verdicts from stored executions (ADR-0004), for SCORE jobs and `pg prove`.

Everything is recomputed from rows: G1 from the candidate's code and the SDK manifest, G2/G5 from
its clean executions, G3 from its bug executions, G4 against the suite. The decision functions
are pure (`pg_core`), so scoring the same rows twice gives the same verdicts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from pg_core.gates.base import Execution as GateExecution
from pg_core.gates.base import Outcome
from pg_core.gates.runs import (
    BugRef,
    SuiteEntry,
    check_cost,
    check_detection,
    check_determinism,
    relevant_bugs,
)
from pg_core.gates.static import StaticReport, check_static
from pg_core.orchestrator import run_group
from pg_core.trust import Evidence, Verdict, decide_batch
from pg_db import repo
from pg_db.models import Bug, Candidate, Execution, Kill


class KillRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    bug_code: str
    killed: bool
    unstable: bool
    evidence_execution_ids: tuple[int, ...]


def final_runs(rows: Iterable[Execution]) -> dict[tuple[str, ...], list[GateExecution]]:
    """Per flag set, the final attempt of each repeat (the highest id), in repeat order."""
    finals: dict[tuple[str, ...], dict[int, Execution]] = {}
    for row in rows:
        by_repeat = finals.setdefault(tuple(sorted(row.flags)), {})
        previous = by_repeat.get(row.repeat)
        if previous is None or previous.id < row.id:
            by_repeat[row.repeat] = row
    return {
        flags: [
            GateExecution(
                outcome=Outcome(r.outcome),
                duration_s=r.duration_ms / 1000,
                flags=flags,
                execution_id=str(r.id),
            )
            for _, r in sorted(by_repeat.items())
        ]
        for flags, by_repeat in finals.items()
    }


def candidate_evidence(
    candidate_id: int,
    code: str,
    rows: Iterable[Execution],
    *,
    manifest: Mapping[str, Any],
    spec_ids: frozenset[str],
    dev_bugs: Sequence[BugRef],
) -> tuple[Evidence, StaticReport, list[KillRecord]]:
    """The gates' findings for one candidate, and the kill rows they imply."""
    g1, report = check_static(code, manifest, spec_ids)
    if not g1.passed:
        return Evidence(candidate_id=str(candidate_id), g1=g1, static=report), report, []
    runs = final_runs(rows)
    clean = runs.get((), [])
    g2, g5 = check_determinism(clean), check_cost(clean)
    if not g2.passed:
        evidence = Evidence(candidate_id=str(candidate_id), g1=g1, static=report, g2=g2, g5=g5)
        return evidence, report, []
    by_flag = {flags[0]: execs for flags, execs in runs.items() if len(flags) == 1}
    g3, detection = check_detection(report.pages_used, dev_bugs, by_flag)
    relevant, _ = relevant_bugs(report.pages_used, dev_bugs)
    kills = [
        KillRecord(
            bug_code=bug.id,
            killed=bug.id in detection.kills,
            unstable=bug.id in detection.unstable,
            evidence_execution_ids=tuple(
                int(e.execution_id or 0) for e in by_flag.get(bug.flag, []) if e.outcome.is_valid
            ),
        )
        for bug in relevant
        if bug.id not in detection.inconclusive
    ]
    evidence = Evidence(
        candidate_id=str(candidate_id),
        g1=g1,
        static=report,
        g2=g2,
        g3=g3,
        detection=detection,
        g5=g5,
    )
    return evidence, report, kills


def suite_entries(session: Session, exclude_run: int | None = None) -> list[SuiteEntry]:
    """Active suite tests with the dev bugs they killed in their latest proving run."""
    codes = {b.id: b.code for b in session.execute(select(Bug)).scalars()}
    entries = []
    for test in repo.active_suite_tests(session):
        if test.accepted_from_candidate_id is not None:
            cand = session.get(Candidate, test.accepted_from_candidate_id)
            if cand is not None and cand.generation_run_id == exclude_run:
                continue
            kills = session.execute(
                select(Kill).where(Kill.candidate_id == test.accepted_from_candidate_id)
            ).scalars()
        else:
            kills = session.execute(select(Kill).where(Kill.suite_test_id == test.id)).scalars()
        latest: dict[int, Kill] = {}
        for kill in kills:
            if kill.bug_id not in latest or kill.id > latest[kill.bug_id].id:
                latest[kill.bug_id] = kill
        killed = frozenset(codes[k.bug_id] for k in latest.values() if k.killed)
        entries.append(SuiteEntry(name=test.path, kills=killed, spec_ids=frozenset(test.spec_ids)))
    return entries


def score_generation_run(
    session: Session,
    generation_run_id: int,
    *,
    manifest: Mapping[str, Any],
    spec_ids: frozenset[str],
    dev_bugs: Sequence[BugRef],
    write: bool = True,
) -> list[Verdict]:
    """Decide every candidate of a run from its stored executions. With `write`, record the
    kills and store each decision, trust score, reasons, gates and pages."""
    group = run_group(generation_run_id)
    candidates = repo.candidates_of_run(session, generation_run_id)
    evidences: list[Evidence] = []
    reports: dict[str, StaticReport] = {}
    kills: dict[int, list[KillRecord]] = {}
    for cand in candidates:
        rows = repo.executions_for(session, group, candidate_id=cand.id)
        evidence, report, cand_kills = candidate_evidence(
            cand.id, cand.code, rows, manifest=manifest, spec_ids=spec_ids, dev_bugs=dev_bugs
        )
        evidences.append(evidence)
        reports[str(cand.id)] = report
        kills[cand.id] = cand_kills
    verdicts = decide_batch(evidences, suite_entries(session, exclude_run=generation_run_id))
    if not write:
        return verdicts
    bug_ids = {b.code: b.id for b in session.execute(select(Bug)).scalars()}
    for cand in candidates:
        for kill in kills[cand.id]:
            repo.record_kill(
                session,
                candidate_id=cand.id,
                run_group=group,
                bug_id=bug_ids[kill.bug_code],
                killed=kill.killed,
                unstable=kill.unstable,
                evidence_execution_ids=list(kill.evidence_execution_ids),
            )
    for verdict in verdicts:
        cand = session.get_one(Candidate, int(verdict.candidate_id))
        cand.decision = verdict.decision.value
        cand.trust_score = verdict.trust_score
        cand.reasons = [r.model_dump() for r in verdict.reasons]
        cand.gates = {k: g.model_dump(mode="json") for k, g in verdict.gates.items()}
        cand.pages_used = list(reports[verdict.candidate_id].pages_used)
    return verdicts
