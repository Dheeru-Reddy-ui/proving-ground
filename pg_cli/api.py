"""`pg api ...`: run the control plane locally and manage its credentials.

Tokens are printed once and stored only as their sha256. These commands write to the database in
`DATABASE_URL` directly, so they run where that variable is set (the PC's `.env`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer
from sqlalchemy import Engine

from pg_cli.settings import Settings
from pg_db.session import make_engine, session_scope

app = typer.Typer(help="The control-plane API (Phase 2).", no_args_is_help=True)


def _engine() -> Engine:
    url = Settings().database_url
    if url is None:
        raise typer.BadParameter("DATABASE_URL is not set (see .env.example)")
    return make_engine(url.get_secret_value())


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port.")] = 8000,
) -> None:
    """Run the API (and, with PG_EMBEDDED_WORKER=true, the worker) on this machine."""
    import uvicorn

    uvicorn.run("pg_api.app:app_from_env", factory=True, host=host, port=port, access_log=False)


@app.command("hash-password")
def hash_password_cmd() -> None:
    """Print the argon2 hash of an admin password, for PG_ADMIN_PASSWORD_HASH."""
    from pg_api.auth import hash_password

    password = typer.prompt("Admin password", hide_input=True, confirmation_prompt=True)
    if len(password) < 12:
        raise typer.BadParameter("use at least 12 characters")
    typer.echo(hash_password(password))


@app.command("admin-token")
def admin_token_cmd(
    name: Annotated[str, typer.Option(help="What the token is for, e.g. cli-laptop.")],
) -> None:
    """Create an admin API token (for `pg build register` against the API). Shown once."""
    from pg_api.auth import create_admin_token

    with session_scope(_engine()) as s:
        token = create_admin_token(s, name)
    typer.echo(token)
    typer.echo("Store it as PG_API_TOKEN in .env; it is not shown again.", err=True)


@app.command("enrolment-token")
def enrolment_token_cmd(
    hours: Annotated[float, typer.Option(help="Hours until it expires.")] = 24.0,
) -> None:
    """Create a one-time enrolment token for `pg agent enroll`. Shown once."""
    from pg_api.auth import create_enrolment_token

    with session_scope(_engine()) as s:
        token = create_enrolment_token(s, datetime.now(UTC), timedelta(hours=hours))
    typer.echo(token)
    typer.echo(f"Valid once, for {hours:g} h.", err=True)


@app.command("rotate-agent-token")
def rotate_agent_token_cmd(
    agent: Annotated[str, typer.Option(help="The agent's name.")],
) -> None:
    """Revoke every token of an agent and print a new one (then `pg agent set-token`)."""
    from sqlalchemy import select

    from pg_api.auth import rotate_agent_token
    from pg_db.models import Agent

    with session_scope(_engine()) as s:
        row = s.execute(select(Agent).where(Agent.name == agent)).scalar_one_or_none()
        if row is None:
            raise typer.BadParameter(f"no agent named {agent}")
        token = rotate_agent_token(s, row, datetime.now(UTC))
    typer.echo(token)
    typer.echo(
        "The old token stops working now; store this one with `pg agent set-token`.", err=True
    )


@app.command("revoke-agent")
def revoke_agent_cmd(
    agent: Annotated[str, typer.Option(help="The agent's name.")],
) -> None:
    """Revoke an agent: its tokens stop working at once (e.g. a leaked token)."""
    from sqlalchemy import select

    from pg_db.models import Agent

    with session_scope(_engine()) as s:
        row = s.execute(select(Agent).where(Agent.name == agent)).scalar_one_or_none()
        if row is None:
            raise typer.BadParameter(f"no agent named {agent}")
        row.revoked_at = datetime.now(UTC)
    typer.echo(f"agent {agent} revoked")
