"""Smoke tests for the `pg` command-line entry point."""

from importlib.metadata import version

from typer.testing import CliRunner

from pg_cli.main import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.output


def test_version_reports_installed_packages() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"proving-ground {version('proving-ground')}" in result.output
    assert f"AltTester-Driver {version('AltTester-Driver')}" in result.output
