"""`pg bugs`: validate the seeded-bug catalog and freeze its holdout split (M1.2).

Reads `benchmark/bugs.yaml`, `game/HOOKS.md` and `specs/` relative to the repository root.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

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
