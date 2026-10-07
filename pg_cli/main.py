"""The `pg` command-line interface."""

from importlib.metadata import version

import typer

app = typer.Typer(
    help="Proving Ground: a trust layer for AI-generated game tests.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Proving Ground: a trust layer for AI-generated game tests."""


@app.command("version")
def show_version() -> None:
    """Print the installed Proving Ground and AltTester driver versions."""
    typer.echo(f"proving-ground {version('proving-ground')}")
    typer.echo(f"AltTester-Driver {version('AltTester-Driver')}")
