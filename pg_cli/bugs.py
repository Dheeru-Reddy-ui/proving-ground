"""`pg bugs`: validate the seeded-bug catalog and freeze its holdout split (M1.2).

Reads `benchmark/bugs.yaml`, `game/HOOKS.md` and `specs/` relative to the repository root.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from pg_core.catalog import (
    Catalog,
    CatalogError,
    holdout_digest,
    hook_flags,
    parse_catalog,
    parse_freeze,
    render_freeze,
    validate_catalog,
    verify_freeze,
)
from pg_core.pages import PAGES

BUGS_YAML = Path("benchmark/bugs.yaml")
HOOKS_MD = Path("game/HOOKS.md")
FREEZE_MD = Path("benchmark/HOLDOUT_FREEZE.md")
SPECS_DIR = Path("specs")

app = typer.Typer(help="Seeded-bug catalog: validate it and freeze the holdout split.")

RootOption = Annotated[Path, typer.Option(help="Repository root.")]


def spec_features(specs_dir: Path) -> frozenset[str]:
    """Feature names are the spec file names (`specs/store.md` -> `store`)."""
    return frozenset(p.stem for p in specs_dir.glob("*.md") if p.stem.lower() != "readme")


def load_catalog(root: Path, *, require_freeze: bool = False) -> Catalog:
    """Parse and validate the catalog; verify the holdout freeze when one exists.

    Raises CatalogError listing every problem.
    """
    catalog = parse_catalog((root / BUGS_YAML).read_text(encoding="utf-8"))
    problems = validate_catalog(
        catalog,
        hooks=hook_flags((root / HOOKS_MD).read_text(encoding="utf-8")),
        features=spec_features(root / SPECS_DIR),
        pages=PAGES,
    )
    freeze_path = root / FREEZE_MD
    if freeze_path.exists():
        problems += verify_freeze(catalog, parse_freeze(freeze_path.read_text(encoding="utf-8")))
    elif require_freeze:
        problems.append(f"{FREEZE_MD} does not exist; the holdout split is not frozen")
    if problems:
        raise CatalogError(problems)
    return catalog


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    if git is None:
        raise typer.BadParameter("git was not found on PATH")
    return subprocess.run(  # noqa: S603 - fixed argv, never a shell
        [git, "-C", str(root), *args], capture_output=True, text=True, timeout=30, check=False
    )


@app.command("check")
def check(root: RootOption = Path()) -> None:
    """Validate bugs.yaml against HOOKS.md, specs/ and the seeded split; verify the freeze."""
    try:
        catalog = load_catalog(root)
    except CatalogError as exc:
        for problem in exc.problems:
            typer.echo(f"FAIL {problem}")
        raise typer.Exit(1) from exc
    frozen = (root / FREEZE_MD).exists()
    typer.echo(
        f"OK {len(catalog.bugs)} bugs: {len(catalog.dev)} dev, {len(catalog.holdout)} holdout; "
        + ("holdout freeze verified" if frozen else "holdout not frozen yet")
    )


SYMPTOM_TESTS = Path("tests/device/test_seeded_bug_symptoms.py")
EVIDENCE_DIR = Path("docs/evidence/phase1")


def _pgflags_lines(artifact_dir: str) -> list[str]:
    lines: list[str] = []
    for log in Path(artifact_dir).rglob("game_log.jsonl"):
        for raw in log.read_text(encoding="utf-8").splitlines():
            message = str(json.loads(raw).get("message", ""))
            if message.startswith("PGFLAGS"):
                lines.append(message)
    return lines


def _flags_active(artifact_dir: str) -> str | None:
    results = Path(artifact_dir) / "pg_results.json"
    if not results.exists():
        return None
    entry: dict[str, Any] = next(iter(json.loads(results.read_text(encoding="utf-8")).values()), {})
    value = entry.get("flags_active")
    return None if value is None else str(value)


@app.command("verify")
def verify(
    bug: Annotated[list[str] | None, typer.Option(help="Only these bug IDs.")] = None,
    root: RootOption = Path(),
) -> None:
    """Confirm each seeded bug shows its symptom: its symptom test passes on the clean build
    and fails with the bug's flag on. Writes the evidence under docs/evidence/phase1/."""
    from pg_cli.run import RUNS_DIR, device_context_args, new_run_id
    from pg_cli.settings import Settings
    from pg_runner.runner import make_context, run_test

    catalog = load_catalog(root)
    tests = _symptom_tests(root / SYMPTOM_TESTS)
    args = device_context_args(Settings(), None)
    run_id = new_run_id("verify")
    results: dict[str, Any] = {}
    for entry in catalog.bugs:
        if bug and entry.id not in bug:
            continue
        name = tests.get(entry.id)
        if name is None:
            typer.echo(f"{entry.id}: no symptom test")
            continue
        row: dict[str, Any] = {"flag": entry.flag, "test": name}
        for label, flags in (("clean", []), ("flag_on", [entry.flag])):
            context = make_context(
                run_id=run_id,
                flags=flags,
                artifact_dir=RUNS_DIR / run_id / entry.id / label,
                **args,  # type: ignore[arg-type]
            )
            final = run_test(root / SYMPTOM_TESTS, context, select=name).final
            row[label] = {
                "outcome": final.outcome.value,
                "rule": final.rule,
                "detail": final.detail.strip().splitlines()[-1][:300] if final.detail else "",
                "game_errors": list(final.game_errors),
                "flags_active": _flags_active(final.artifact_dir),
                "pgflags_log": _pgflags_lines(final.artifact_dir),
            }
        clean_ok = row["clean"]["outcome"] == "passed"
        shown = row["flag_on"]["outcome"] in ("assertion", "pg_timeout", "game_error")
        toggled = row["flag_on"]["flags_active"] == entry.flag
        row["verified"] = clean_ok and shown and toggled
        results[entry.id] = row
        typer.echo(
            f"{entry.id}: clean {row['clean']['outcome']}, flag on {row['flag_on']['outcome']}"
            f" (Active()={row['flag_on']['flags_active']!r}) -> "
            + ("VERIFIED" if row["verified"] else "NOT VERIFIED")
        )
    tag = str(args["locator_tag"])
    path = root / EVIDENCE_DIR / f"bug_symptoms_{tag}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    previous: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    previous.setdefault("runs", []).append(run_id)
    previous.setdefault("bugs", {}).update(results)
    path.write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"{run_id}: evidence in {path}")


def _symptom_tests(path: Path) -> dict[str, str]:
    """Bug ID -> symptom test name (`test_sb01_...` verifies SB01)."""
    names: dict[str, str] = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_sb"):
            names[node.name[5:9].upper()] = node.name
    return names


@app.command("freeze")
def freeze(root: RootOption = Path()) -> None:
    """Record the holdout digest and the bugs.yaml commit in HOLDOUT_FREEZE.md (once only)."""
    if (root / FREEZE_MD).exists():
        typer.echo(f"{FREEZE_MD} already exists; changing a frozen split needs Dheeru's approval.")
        raise typer.Exit(1)
    try:
        catalog = load_catalog(root)
    except CatalogError as exc:
        for problem in exc.problems:
            typer.echo(f"FAIL {problem}")
        raise typer.Exit(1) from exc
    if _git(root, "diff", "--quiet", "HEAD", "--", str(BUGS_YAML)).returncode != 0:
        typer.echo(f"{BUGS_YAML} has uncommitted changes; commit it before freezing.")
        raise typer.Exit(1)
    commit = _git(root, "log", "-1", "--format=%H", "--", str(BUGS_YAML)).stdout.strip()
    if not commit:
        typer.echo(f"{BUGS_YAML} is not committed yet.")
        raise typer.Exit(1)
    digest = holdout_digest(catalog)
    today = datetime.now(UTC).date().isoformat()
    text = render_freeze(digest, commit, today, [bug.id for bug in catalog.holdout])
    (root / FREEZE_MD).write_text(text, encoding="utf-8")
    typer.echo(f"froze {len(catalog.holdout)} holdout bugs: sha256 {digest} at {commit[:12]}")
