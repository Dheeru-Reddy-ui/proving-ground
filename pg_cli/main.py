"""The `pg` command-line interface."""

from importlib.metadata import version
from typing import Annotated

import typer

from pg_cli.bugs import app as bugs_app
from pg_cli.smoke import smoke_cmd
from pg_cli.spike import app as spike_app

app = typer.Typer(
    help="Proving Ground: a trust layer for AI-generated game tests.",
    no_args_is_help=True,
)
app.add_typer(spike_app, name="spike")
app.add_typer(bugs_app, name="bugs")
app.command("smoke")(smoke_cmd)


@app.callback()
def main() -> None:
    """Proving Ground: a trust layer for AI-generated game tests."""


@app.command("version")
def show_version() -> None:
    """Print the installed Proving Ground and AltTester driver versions."""
    typer.echo(f"proving-ground {version('proving-ground')}")
    typer.echo(f"AltTester-Driver {version('AltTester-Driver')}")


@app.command("doctor")
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
) -> None:
    """Check the device chain (adb, phone, game, port forward, AltTester, app, driver)."""
    from pg_cli.doctor import default_deps, render, run_doctor
    from pg_cli.settings import Settings

    report = run_doctor(Settings(), default_deps())
    typer.echo(report.model_dump_json(indent=2) if json_output else render(report))
    raise typer.Exit(report.exit_code)
