"""`pg report`: the run report, generated from stored rows only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select

from pg_cli.bugs import load_catalog
from pg_cli.pipeline import engine
from pg_core.report import (
    CandidateRow,
    ExecutionRow,
    KillRow,
    ReportData,
    RunInfo,
    render_json,
    render_markdown,
    subject_label,
)
from pg_db.models import Bug, Build, Candidate, Execution, GenerationRun, Kill, SuiteTest
from pg_db.session import session_scope

RESULTS_DIR = Path("docs/results/phase1")


def load_report(run_ids: list[int], baseline: list[str]) -> ReportData:
    with session_scope(engine()) as s:
        runs = [s.get(GenerationRun, r) for r in run_ids]
        if any(r is None for r in runs):
            raise typer.BadParameter("unknown generation run id")
        gens = [r for r in runs if r is not None]
        builds = {s.get_one(Build, g.build_id).apk_sha256 for g in gens}
        if len(builds) != 1:
            raise typer.BadParameter("the runs were generated for different builds")
        groups = [f"prove-{g.id}" for g in gens]
        if not baseline:
            baseline = sorted(
                {
                    e.run_group
                    for e in s.execute(
                        select(Execution).where(Execution.run_group.startswith("baseline-"))
                    ).scalars()
                }
            )[-1:]
        candidates = list(
            s.execute(
                select(Candidate)
                .where(Candidate.generation_run_id.in_(run_ids))
                .order_by(Candidate.id)
            ).scalars()
        )
        executions = list(
            s.execute(select(Execution).where(Execution.run_group.in_(groups + baseline))).scalars()
        )
        kills = list(s.execute(select(Kill).where(Kill.run_group.in_(groups + baseline))).scalars())
        codes = {b.id: b.code for b in s.execute(select(Bug)).scalars()}
        names = {c.id: c.name for c in candidates}
        suite_names = {t.id: t.name for t in s.execute(select(SuiteTest)).scalars()}
        dev = tuple(b.id for b in load_catalog(Path()).dev)
        return ReportData(
            build_sha256=builds.pop(),
            runs=tuple(
                RunInfo(
                    generation_run_id=g.id,
                    feature=g.feature,
                    model=g.model,
                    prompt_version=g.prompt_version,
                    prompt_hash=g.prompt_hash,
                    status=g.status,
                    n_requested=g.n_requested,
                    tokens_in=g.tokens_in,
                    tokens_out=g.tokens_out,
                    cost_usd=g.cost_usd,
                    prove_run_group=f"prove-{g.id}",
                )
                for g in gens
            ),
            baseline_run_groups=tuple(baseline),
            candidates=tuple(
                CandidateRow(
                    id=c.id,
                    generation_run_id=c.generation_run_id,
                    name=c.name,
                    spec_ids=tuple(c.spec_ids),
                    decision=c.decision,
                    human_decision=c.human_decision,
                    trust_score=c.trust_score,
                    reasons=tuple(c.reasons),
                    reached_g2="G2" in c.gates,
                    passed_g2=bool(c.gates.get("G2", {}).get("passed")),
                )
                for c in candidates
            ),
            executions=tuple(
                ExecutionRow(
                    run_group=e.run_group, purpose=e.purpose, outcome=e.outcome, wall_ms=e.wall_ms
                )
                for e in executions
            ),
            kills=tuple(
                KillRow(
                    subject=subject_label(
                        "candidate", k.candidate_id, names.get(k.candidate_id, "?")
                    )
                    if k.candidate_id is not None
                    else subject_label(
                        "human", k.suite_test_id or 0, suite_names.get(k.suite_test_id or 0, "?")
                    ),
                    bug=codes[k.bug_id],
                    killed=k.killed,
                    unstable=k.unstable,
                )
                for k in kills
                if codes[k.bug_id] in dev
            ),
            dev_bugs=dev,
            generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        )


def report_cmd(
    run: Annotated[list[int], typer.Option(help="Generation run id (repeatable).")],
    baseline: Annotated[
        list[str] | None, typer.Option(help="Baseline run group (default: the latest).")
    ] = None,
    output_format: Annotated[str, typer.Option("--format", help="md or json.")] = "md",
    write: Annotated[bool, typer.Option(help="Also write it under docs/results/phase1/.")] = False,
    readme: Annotated[
        bool, typer.Option(help="Also place the Markdown report in README.md between its markers.")
    ] = False,
) -> None:
    """Report candidates by decision, rejection reasons, G2 rate, the dev kill matrix, cost per
    accepted test and device time, for the given generation runs."""
    data = load_report(run, list(baseline or []))
    text = (
        render_markdown(data)
        if output_format == "md"
        else json.dumps(render_json(data), indent=2, default=str) + "\n"
    )
    typer.echo(text)
    if write:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stem = "report_runs_" + "_".join(str(r) for r in sorted(run))
        path = RESULTS_DIR / f"{stem}.{'md' if output_format == 'md' else 'json'}"
        path.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {path}")
    if readme:
        embed_in_readme(render_markdown(data), README)
        typer.echo(f"updated {README}")


README = Path("README.md")
START = "<!-- phase1-report:start (generated by `pg report --readme`; do not edit) -->"
END = "<!-- phase1-report:end -->"


def embed_in_readme(markdown: str, path: Path) -> None:
    """Replace the text between the README's report markers with the generated report, its
    headings demoted one level so they sit under the README's own section."""
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise typer.BadParameter(f"{path} has no report markers")
    body = "\n".join(
        ("#" + line) if line.startswith("#") else line for line in markdown.strip().splitlines()
    )
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    path.write_text(f"{before}{START}\n{body}\n{END}{after}", encoding="utf-8", newline="\n")
