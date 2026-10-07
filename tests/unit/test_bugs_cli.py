"""`pg bugs check` and `pg bugs freeze` on a throwaway copy of the catalog files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pg_cli.main import app

REPO = Path(__file__).resolve().parents[2]
runner = CliRunner()


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    for rel in ("benchmark/bugs.yaml", "game/HOOKS.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    shutil.copytree(REPO / "specs", tmp_path / "specs")
    return tmp_path


def git(root: Path, *args: str) -> None:
    exe = shutil.which("git")
    assert exe is not None
    subprocess.run(  # noqa: S603 - test helper with a fixed argv
        [exe, "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


def test_check_passes_on_the_repository_catalog(repo_copy: Path) -> None:
    result = runner.invoke(app, ["bugs", "check", "--root", str(repo_copy)])
    assert result.exit_code == 0, result.output
    assert "16 bugs: 10 dev, 6 holdout" in result.output


def test_check_lists_every_problem(repo_copy: Path) -> None:
    path = repo_copy / "benchmark/bugs.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("feature: missions", "feature: casino")
    )
    result = runner.invoke(app, ["bugs", "check", "--root", str(repo_copy)])
    assert result.exit_code == 1
    assert result.output.count("unknown feature casino") == 3


def test_freeze_needs_a_committed_catalog_then_writes_once(repo_copy: Path) -> None:
    git(repo_copy, "init", "-q")
    result = runner.invoke(app, ["bugs", "freeze", "--root", str(repo_copy)])
    assert result.exit_code == 1  # not committed

    git(repo_copy, "add", "-A")
    git(repo_copy, "commit", "-q", "-m", "catalog")
    result = runner.invoke(app, ["bugs", "freeze", "--root", str(repo_copy)])
    assert result.exit_code == 0, result.output
    assert "froze 6 holdout bugs" in result.output

    check = runner.invoke(app, ["bugs", "check", "--root", str(repo_copy)])
    assert "holdout freeze verified" in check.output
    again = runner.invoke(app, ["bugs", "freeze", "--root", str(repo_copy)])
    assert again.exit_code == 1


def test_check_fails_when_a_frozen_holdout_entry_changes(repo_copy: Path) -> None:
    git(repo_copy, "init", "-q")
    git(repo_copy, "add", "-A")
    git(repo_copy, "commit", "-q", "-m", "catalog")
    assert runner.invoke(app, ["bugs", "freeze", "--root", str(repo_copy)]).exit_code == 0
    path = repo_copy / "benchmark/bugs.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "pages: [settings, app]\n    symptom: After", "pages: [settings]\n    symptom: After"
        )
    )
    result = runner.invoke(app, ["bugs", "check", "--root", str(repo_copy)])
    assert result.exit_code == 1
    assert "differs from the frozen" in result.output


def test_freeze_refuses_uncommitted_changes(repo_copy: Path) -> None:
    git(repo_copy, "init", "-q")
    git(repo_copy, "add", "-A")
    git(repo_copy, "commit", "-q", "-m", "catalog")
    path = repo_copy / "benchmark/bugs.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n")
    result = runner.invoke(app, ["bugs", "freeze", "--root", str(repo_copy)])
    assert result.exit_code == 1
    assert "uncommitted" in result.output
