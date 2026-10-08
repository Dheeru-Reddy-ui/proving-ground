"""The Phase 1 pipeline commands (M1.8): register a build, generate, prove, run the human
baseline, record human review decisions and sync accepted tests into `suites/accepted/`.

Proving is resumable: executions are stored per (run group, subject, flags, repeat, attempt),
and a repeated `pg prove` reuses every stored run instead of spending device time again.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import typer
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pg_cli.bugs import load_catalog
from pg_cli.run import device_context_args, new_run_id
from pg_cli.settings import Settings
from pg_cli.spike import apk_sha256
from pg_core.gates.base import Execution as GateExecution
from pg_core.gates.base import Outcome
from pg_core.gates.runs import (
    BugRef,
    SuiteEntry,
    check_cost,
    check_detection,
    check_determinism,
    detection_needs_more,
    determinism_needs_more,
    relevant_bugs,
)
from pg_core.gates.static import StaticReport, check_static, spec_ids_from_markdown
from pg_core.spike import build_tag
from pg_core.trust import Decision, Evidence, decide_batch
from pg_db import repo
from pg_db.models import Bug, Build, Candidate, GenerationRun, Kill, SuiteTest
from pg_db.session import make_engine, session_scope
from pg_generator.generate import call_log, generate
from pg_generator.llm import Rates, make_client
from pg_generator.prompt import parse_spec_file, render_prompt, select_specs
from pg_runner.adb import Adb
from pg_runner.runner import DEFAULT_TIMEOUT_S, RunRecord, make_context, run_test

ARTIFACTS = Path("artifacts")
SPECS_DIR = Path("specs")
ACCEPTED_DIR = Path("suites/accepted")
BASELINE_DIR = Path("suites/human_baseline")
LOCATORS_DIR = Path("pg_sdk/locators")
MANIFEST_PATH = Path("pg_sdk/manifest.json")
CLEAN_REPEATS = 3
BUG_REPEATS = 2
EXTRA_REPEATS = 2  # more repeats when one still ended in infra after its retries

build_app = typer.Typer(help="Builds under test.")
suite_app = typer.Typer(help="The regression suite.")


# --- shared helpers -----------------------------------------------------------------------


def engine() -> Engine:
    url = Settings().database_url
    if url is None:
        raise typer.BadParameter("DATABASE_URL is not set (see .env.example)")
    return make_engine(url.get_secret_value())


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def known_spec_ids() -> frozenset[str]:
    return spec_ids_from_markdown(p.read_text(encoding="utf-8") for p in SPECS_DIR.glob("*.md"))


def sync_specs_and_bugs(session: Session) -> None:
    rows = []
    for path in sorted(SPECS_DIR.glob("*.md")):
        if path.stem.lower() == "readme":
            continue
        text = path.read_text(encoding="utf-8")
        sha = repo.sha256_text(text)
        rows += [(s.spec_id, path.stem, s.text, sha) for s in parse_spec_file(text)]
    repo.sync_specs(session, rows)
    repo.sync_bugs(session, load_catalog(Path()))


def resolve_build(session: Session, prefix: str | None) -> Build:
    if prefix is None:
        build = repo.latest_build(session)
    else:
        build = session.execute(
            select(Build).where(Build.apk_sha256.startswith(prefix.lower()))
        ).scalar_one_or_none()
    if build is None:
        raise typer.BadParameter("no registered build; run `pg build register` first")
    return build


def check_device_build(build: Build) -> None:
    settings = Settings()
    installed = Adb().installed_apk_sha256(
        settings.pg_android_package or "", settings.pg_adb_serial
    )
    if installed != build.apk_sha256:
        raise typer.BadParameter(
            f"the phone has {str(installed)[:12]} installed, not build {build.apk_sha256[:12]}"
        )


def static_reports_per_test(code: str) -> list[tuple[str, str, StaticReport]]:
    """(test name, standalone source, G1 report) for each test function in a file."""
    tree = ast.parse(code)
    imports = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
    tests = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    result = []
    for func in tests:
        source = ast.unparse(ast.Module(body=[*imports, func], type_ignores=[]))
        _, report = check_static(source, manifest(), known_spec_ids())
        result.append((func.name, source, report))
    return result


def gate_execution(
    outcome: str, duration_ms: int, flags: Sequence[str], ident: int
) -> GateExecution:
    return GateExecution(
        outcome=Outcome(outcome),
        duration_s=duration_ms / 1000,
        flags=tuple(flags),
        execution_id=str(ident),
    )


def execute_until(
    eng: Engine,
    *,
    subject: dict[str, int],
    build_id: int,
    run_group: str,
    purpose: str,
    test_file: Path,
    select_name: str | None,
    flags: Sequence[str],
    context_args: dict[str, Any],
    artifact_root: Path,
    need_more: Callable[[Sequence[GateExecution]], bool],
    max_repeats: int,
    timeout_s: float,
    echo: Callable[[str], Any],
) -> list[GateExecution]:
    """Final outcome of each repeat for one (subject, flags): stored ones first, then new runs
    while `need_more` says another repeat could change the gate."""
    with session_scope(eng) as s:
        stored = repo.executions_for(s, run_group, **_subject_kw(subject))
    finals: dict[int, GateExecution] = {}
    for row in stored:
        if list(row.flags) != sorted(flags):
            continue
        previous = finals.get(row.repeat)
        if previous is None or int(previous.execution_id or 0) < row.id:
            finals[row.repeat] = gate_execution(row.outcome, row.duration_ms, row.flags, row.id)
    results = [finals[r] for r in sorted(finals)]
    repeat = max(finals, default=0)
    label = ",".join(flags) or "clean"
    while need_more(results) and repeat < max_repeats:
        repeat += 1
        context = make_context(
            run_id=run_group,
            flags=flags,
            artifact_dir=artifact_root / label / f"r{repeat}",
            **context_args,
        )
        started = datetime.now(UTC)
        record = run_test(test_file, context, timeout_s=timeout_s, select=select_name)
        finished = datetime.now(UTC)
        ident = _store_record(
            eng, record, subject, build_id, run_group, purpose, repeat, started, finished
        )
        final = record.final
        results.append(
            gate_execution(final.outcome.value, int(final.duration_s * 1000), flags, ident)
        )
        echo(f"    {label} r{repeat}: {final.outcome.value} ({final.rule}, {final.wall_s:.0f}s)")
    return results


def _subject_kw(subject: dict[str, int]) -> dict[str, int]:
    return {k: v for k, v in subject.items() if k in ("candidate_id", "suite_test_id")}


def _store_record(
    eng: Engine,
    record: RunRecord,
    subject: dict[str, int],
    build_id: int,
    run_group: str,
    purpose: str,
    repeat: int,
    started: datetime,
    finished: datetime,
) -> int:
    ident = 0
    with session_scope(eng) as s:
        for attempt in record.attempts:
            row = repo.record_execution(
                s,
                **_subject_kw(subject),
                build_id=build_id,
                run_group=run_group,
                flags=sorted(record.flags),
                purpose=purpose,
                repeat=repeat,
                attempt=attempt.attempt,
                outcome=attempt.outcome.value,
                failure_kind=attempt.rule,
                detail=attempt.detail or None,
                duration_ms=int(attempt.duration_s * 1000),
                wall_ms=int(attempt.wall_s * 1000),
                artifacts={
                    "dir": attempt.artifact_dir,
                    "game_errors": list(attempt.game_errors),
                    "pgtelem_lines": len(attempt.pgtelem),
                    "actions": list(attempt.actions),
                },
                started_at=started,
                finished_at=finished,
            )
            ident = row.id
    return ident


def prove_subject(
    eng: Engine,
    *,
    subject: dict[str, int],
    build_id: int,
    run_group: str,
    test_file: Path,
    select_name: str | None,
    report: StaticReport,
    dev_bugs: Sequence[BugRef],
    context_args: dict[str, Any],
    artifact_root: Path,
    timeout_s: float,
    echo: Callable[[str], Any],
    clean_only: bool = False,
) -> tuple[Any, Any, Any, Any]:
    """Run G2/G5 (clean) and, when G2 passes, G3 (relevant dev bugs) for one test."""
    common: dict[str, Any] = {
        "subject": subject,
        "build_id": build_id,
        "run_group": run_group,
        "test_file": test_file,
        "select_name": select_name,
        "context_args": context_args,
        "artifact_root": artifact_root,
        "timeout_s": timeout_s,
        "echo": echo,
    }
    clean = execute_until(
        eng,
        purpose="clean",
        flags=(),
        need_more=determinism_needs_more,
        max_repeats=CLEAN_REPEATS + EXTRA_REPEATS,
        **common,
    )
    g2 = check_determinism(clean)
    g5 = check_cost(clean)
    if not g2.passed or clean_only:
        return g2, None, None, g5
    relevant, skipped = relevant_bugs(report.pages_used, dev_bugs)
    for bug_id, why in skipped.items():
        echo(f"    skip {bug_id}: {why}")
    runs: dict[str, list[GateExecution]] = {}
    for bug in relevant:
        runs[bug.flag] = execute_until(
            eng,
            purpose="bug",
            flags=(bug.flag,),
            need_more=detection_needs_more,
            max_repeats=BUG_REPEATS + EXTRA_REPEATS,
            **common,
        )
    g3, detection = check_detection(report.pages_used, dev_bugs, runs)
    with session_scope(eng) as s:
        bug_ids = {b.code: b.id for b in s.execute(select(Bug)).scalars()}
        for bug in relevant:
            evidence = [int(e.execution_id or 0) for e in runs[bug.flag] if e.outcome.is_valid]
            if bug.id in detection.inconclusive:
                continue
            repo.record_kill(
                s,
                **_subject_kw(subject),
                run_group=run_group,
                bug_id=bug_ids[bug.id],
                killed=bug.id in detection.kills,
                unstable=bug.id in detection.unstable,
                evidence_execution_ids=evidence,
            )
    return g2, g3, detection, g5


def dev_bug_refs() -> list[BugRef]:
    return [BugRef(id=b.id, flag=b.flag, pages=b.pages) for b in load_catalog(Path()).dev]


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


# --- commands -----------------------------------------------------------------------------


@build_app.command("register")
def build_register(
    apk: Annotated[Path, typer.Option(help="The instrumented APK.")],
    label: Annotated[str, typer.Option(help="A short description of the build.")],
    locators_from: Annotated[
        str | None, typer.Option(help="Create this build's locator map extending that tag.")
    ] = None,
) -> None:
    """Register a build (idempotent on the APK's sha256) and make sure it has a locator map."""
    sha = apk_sha256(apk)
    if sha is None:
        raise typer.BadParameter(f"{apk} is not a file")
    tag = build_tag(sha)
    locators = LOCATORS_DIR / f"{tag}.yaml"
    if not locators.exists():
        if locators_from is None:
            raise typer.BadParameter(f"no locator map {locators}; pass --locators-from <tag>")
        locators.write_text(
            f"# Locator map for build {tag}: the same UI as {locators_from}.\n"
            f"build_tag: {tag}\nextends: {locators_from}\nlocators: {{}}\n",
            encoding="utf-8",
        )
        typer.echo(f"wrote {locators} (extends {locators_from})")
    with session_scope(engine()) as s:
        build, created = repo.register_build(s, sha, label, tag)
        sync_specs_and_bugs(s)
        typer.echo(
            f"build {build.id} {sha[:12]} ({'registered' if created else 'already registered'})"
        )


def accepted_tests() -> list[tuple[str, list[str]]]:
    tests = []
    for path in sorted(ACCEPTED_DIR.glob("test_*.py")):
        for name, _, report in static_reports_per_test(path.read_text(encoding="utf-8")):
            tests.append((name, list(report.spec_ids)))
    return tests


def generate_cmd(
    spec: Annotated[str, typer.Option(help="Feature spec file name, e.g. store.")],
    n: Annotated[int, typer.Option(help="Candidates to request.")] = 8,
    seed: Annotated[int | None, typer.Option(help="Seed passed to the model.")] = None,
    spec_id: Annotated[list[str] | None, typer.Option(help="Limit to these spec IDs.")] = None,
    include_unconfirmed: Annotated[bool, typer.Option(help="Include [confirm] specs.")] = False,
    build: Annotated[str | None, typer.Option(help="Build sha prefix (default: latest).")] = None,
) -> None:
    """Generate candidate tests for one feature and store them as a generation run."""
    settings = Settings()
    provider, model, api_key = (
        settings.pg_llm_provider,
        settings.pg_llm_model,
        settings.pg_llm_api_key,
    )
    if not provider or not model or api_key is None:
        raise typer.BadParameter("set PG_LLM_PROVIDER, PG_LLM_MODEL and PG_LLM_API_KEY in .env")
    spec_path = SPECS_DIR / f"{spec}.md"
    statements = select_specs(
        parse_spec_file(spec_path.read_text(encoding="utf-8")), spec_id, include_unconfirmed
    )
    prompt = render_prompt(
        feature=spec, specs=statements, manifest=manifest(), existing_tests=accepted_tests(), n=n
    )
    eng = engine()
    with session_scope(eng) as s:
        target = resolve_build(s, build)
        sync_specs_and_bugs(s)
        run = repo.create_generation_run(
            s,
            build_id=target.id,
            feature=spec,
            provider=provider.lower(),
            model=model,
            prompt_version=prompt.version,
            prompt_hash=prompt.sha256,
            temperature=settings.pg_llm_temperature,
            seed=seed,
            seed_supported=None,
            n_requested=n,
        )
        run_id = run.id
    out = ARTIFACTS / "generation" / str(run_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompt.md").write_text(
        f"[system]\n{prompt.system}\n\n[user]\n{prompt.user}\n", encoding="utf-8"
    )
    client = make_client(provider, api_key.get_secret_value(), model, settings.pg_llm_timeout_s)
    typer.echo(f"generation run {run_id}: asking {client.model} for {n} tests ({prompt.version})")
    result = generate(
        client,
        prompt,
        n=n,
        temperature=settings.pg_llm_temperature,
        seed=seed,
        max_output_tokens=settings.pg_llm_max_output_tokens,
        rates=Rates(
            input_usd_per_mtok=settings.pg_llm_input_usd_per_mtok,
            output_usd_per_mtok=settings.pg_llm_output_usd_per_mtok,
        ),
        budget_usd=settings.pg_max_cost_per_run_usd or Decimal("1.00"),
    )
    with session_scope(eng) as s:
        repo.finish_generation_run(
            s,
            run_id,
            status=result.status,
            finished_at=datetime.now(UTC),
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            llm_calls=call_log(result),
            error=result.error,
        )
        run_row = s.get_one(GenerationRun, run_id)
        run_row.seed_supported = client.seed_supported
        for test in result.tests:
            cand, _ = repo.add_candidate(
                s, run_id, test.name, test.intent, test.code, test.spec_ids
            )
            (out / f"c{cand.id}_{test.name}.py").write_text(test.code, encoding="utf-8")
    typer.echo(
        f"generation run {run_id}: {result.status}, {len(result.tests)} candidate(s), "
        f"{result.tokens_in}+{result.tokens_out} tokens, ${result.cost_usd}"
        + (f"\n  {result.error}" if result.error else "")
    )


def prove_cmd(
    run: Annotated[int, typer.Option(help="Generation run id.")],
    timeout: Annotated[float, typer.Option(help="Seconds per test attempt.")] = DEFAULT_TIMEOUT_S,
    static_only: Annotated[
        bool, typer.Option(help="Run only G1 (no device, no decision stored).")
    ] = False,
    clean_only: Annotated[
        bool, typer.Option(help="Stop after the clean runs (G2/G5); G3 stays pending.")
    ] = False,
) -> None:
    """Run G1-G5 on every candidate of a generation run and decide ACCEPT/REVIEW/REJECT."""
    eng = engine()
    run_group = f"prove-{run}"
    with session_scope(eng) as s:
        gen = s.get(GenerationRun, run)
        if gen is None:
            raise typer.BadParameter(f"no generation run {run}")
        target = s.get_one(Build, gen.build_id)
        sync_specs_and_bugs(s)
        candidates = [(c.id, c.name, c.code) for c in repo.candidates_of_run(s, run)]
        build_id, locator_tag = target.id, target.locator_tag
        if static_only:
            for cid, name, code in candidates:
                g1, report = check_static(code, manifest(), known_spec_ids())
                label = "pass" if g1.passed else "REJECT"
                typer.echo(f"c{cid} {name}: G1 {label} pages={','.join(report.pages_used)}")
                for problem in g1.reasons:
                    typer.echo(f"    {problem.code}: {problem.message}")
            return
        check_device_build(target)
    context_args = device_context_args(Settings(), locator_tag)
    dev_bugs = dev_bug_refs()
    artifact_root = ARTIFACTS / "prove" / run_group
    evidences: list[Evidence] = []
    for cid, name, code in candidates:
        g1, report = check_static(code, manifest(), known_spec_ids())
        typer.echo(f"c{cid} {name}: G1 {'pass' if g1.passed else 'REJECT ' + g1.reasons[0].code}")
        if not g1.passed:
            evidences.append(Evidence(candidate_id=str(cid), g1=g1, static=report))
            continue
        test_file = artifact_root / f"c{cid}" / f"test_c{cid}.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(code, encoding="utf-8")
        g2, g3, detection, g5 = prove_subject(
            eng,
            subject={"candidate_id": cid},
            build_id=build_id,
            run_group=run_group,
            test_file=test_file,
            select_name=None,
            report=report,
            dev_bugs=dev_bugs,
            context_args=context_args,
            artifact_root=artifact_root / f"c{cid}",
            timeout_s=timeout,
            echo=typer.echo,
            clean_only=clean_only,
        )
        evidences.append(
            Evidence(
                candidate_id=str(cid),
                g1=g1,
                static=report,
                g2=g2,
                g3=g3,
                detection=detection,
                g5=g5,
            )
        )
    with session_scope(eng) as s:
        verdicts = decide_batch(evidences, suite_entries(s, exclude_run=run))
        reports = {e.candidate_id: e.static for e in evidences}
        for verdict in verdicts:
            cand = s.get_one(Candidate, int(verdict.candidate_id))
            cand.decision = verdict.decision.value
            cand.trust_score = verdict.trust_score
            cand.reasons = [r.model_dump() for r in verdict.reasons]
            cand.gates = {k: g.model_dump(mode="json") for k, g in verdict.gates.items()}
            cand.pages_used = list(reports[verdict.candidate_id].pages_used)
    counts = {d.value: sum(v.decision is d for v in verdicts) for d in Decision}
    typer.echo(f"{run_group}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))


def baseline_cmd(
    timeout: Annotated[float, typer.Option(help="Seconds per test attempt.")] = DEFAULT_TIMEOUT_S,
    build: Annotated[str | None, typer.Option(help="Build sha prefix (default: latest).")] = None,
) -> None:
    """Run every test in suites/human_baseline/ through the same gates' runs (clean x3, then
    relevant dev bugs x2) and record its kills. Never edits the baseline files."""
    files = sorted(BASELINE_DIR.glob("test_*.py"))
    if not files:
        raise typer.BadParameter(f"no test files in {BASELINE_DIR}")
    eng = engine()
    run_group = new_run_id("baseline")
    with session_scope(eng) as s:
        target = resolve_build(s, build)
        sync_specs_and_bugs(s)
        build_id, locator_tag = target.id, target.locator_tag
        check_device_build(target)
    context_args = device_context_args(Settings(), locator_tag)
    dev_bugs = dev_bug_refs()
    artifact_root = ARTIFACTS / "baseline" / run_group
    for path in files:
        for name, source, report in static_reports_per_test(path.read_text(encoding="utf-8")):
            with session_scope(eng) as s:
                test = repo.upsert_suite_test(
                    s,
                    origin="human",
                    path=f"{path.as_posix()}::{name}",
                    name=name,
                    code=source,
                    spec_ids=report.spec_ids,
                )
                test_id = test.id
            typer.echo(f"human {path.name}::{name} (pages {', '.join(report.pages_used)})")
            g2, g3, _, _ = prove_subject(
                eng,
                subject={"suite_test_id": test_id},
                build_id=build_id,
                run_group=run_group,
                test_file=path,
                select_name=name,
                report=report,
                dev_bugs=dev_bugs,
                context_args=context_args,
                artifact_root=artifact_root / name,
                timeout_s=timeout,
                echo=typer.echo,
            )
            kills = g3.metrics["kills"] if g3 is not None else []
            typer.echo(f"  clean {'pass' if g2.passed else 'FAIL'}; kills {kills or 'none'}")
    typer.echo(f"{run_group}: done")


def _review(candidate_id: int, decision: str, reason: str) -> None:
    with session_scope(engine()) as s:
        cand = s.get(Candidate, candidate_id)
        if cand is None:
            raise typer.BadParameter(f"no candidate {candidate_id}")
        if cand.decision != "review":
            raise typer.BadParameter(f"candidate {candidate_id} is {cand.decision}, not review")
        cand.human_decision = decision
        cand.human_reason = reason
        cand.human_decided_at = datetime.now(UTC)
    typer.echo(f"candidate {candidate_id}: human {decision} ({reason})")


def accept_cmd(
    candidate_id: Annotated[int, typer.Argument()],
    reason: Annotated[str, typer.Option(help="Why the reviewer accepts it.")],
) -> None:
    """Accept a REVIEW candidate (human decision, stored with the reason)."""
    _review(candidate_id, "accept", reason)


def reject_cmd(
    candidate_id: Annotated[int, typer.Argument()],
    reason: Annotated[str, typer.Option(help="Why the reviewer rejects it.")],
) -> None:
    """Reject a REVIEW candidate (human decision, stored with the reason)."""
    _review(candidate_id, "reject", reason)


@suite_app.command("sync")
def suite_sync() -> None:
    """Write ACCEPTed (and human-approved REVIEW) candidates to suites/accepted/."""
    ACCEPTED_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    with session_scope(engine()) as s:
        rows = s.execute(select(Candidate).order_by(Candidate.id)).scalars()
        for cand in rows:
            approved = cand.decision == "review" and cand.human_decision == "accept"
            if cand.decision != "accept" and not approved:
                continue
            run = s.get_one(GenerationRun, cand.generation_run_id)
            path = ACCEPTED_DIR / f"{cand.name}_c{cand.id}.py"
            how = "human-approved review" if approved else "ACCEPT"
            header = (
                f"# Accepted by Proving Ground ({how}).\n"
                f"# candidate {cand.id}, generation run {run.id}, model {run.model}, "
                f"prompt {run.prompt_version}. Generated code: do not edit by hand.\n"
            )
            path.write_text(header + cand.code, encoding="utf-8")
            repo.upsert_suite_test(
                s,
                origin="generated",
                path=path.as_posix(),
                name=cand.name,
                code=cand.code,
                spec_ids=cand.spec_ids,
                accepted_from_candidate_id=cand.id,
            )
            written += 1
    typer.echo(f"{written} accepted test(s) in {ACCEPTED_DIR}")


def suite_tests_summary(session: Session) -> list[SuiteTest]:
    return repo.active_suite_tests(session)
