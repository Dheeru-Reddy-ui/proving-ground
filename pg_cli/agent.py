"""`pg agent ...`: enrol this PC as a device agent and run it (M2.4, ADR-0005)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import platformdirs
import typer

from pg_cli.settings import Settings

app = typer.Typer(help="The device agent on this PC (Phase 2).", no_args_is_help=True)


@app.command("enroll")
def enroll(
    api: Annotated[str, typer.Option(help="The control plane's URL, e.g. https://….onrender.com")],
    name: Annotated[str, typer.Option(help="This agent's name, e.g. dheeru-pc.")],
) -> None:
    """Trade a one-time enrolment token for this agent's credentials (stored for this user only)."""
    from pg_agent import config
    from pg_agent.client import AgentApi

    enrolment = typer.prompt("Enrolment token", hide_input=True)
    client = AgentApi(api, None)
    try:
        answer = client.register(enrolment, name, {"platform": "android", "max_concurrency": 1})
    finally:
        client.close()
    path = config.save(config.AgentConfig(api_url=api, name=answer["name"], token=answer["token"]))
    typer.echo(f"enrolled agent {answer['name']} (id {answer['agent_id']}); credentials in {path}")


@app.command("set-token")
def set_token() -> None:
    """Replace this agent's token after `pg api rotate-agent-token`."""
    from pg_agent import config

    current = config.load()
    token = typer.prompt("New agent token", hide_input=True)
    path = config.save(config.AgentConfig(api_url=current.api_url, name=current.name, token=token))
    typer.echo(f"token updated in {path}")


def health_check(settings: Settings) -> Any:
    from pg_agent.loop import Health
    from pg_cli.doctor import default_deps, run_doctor
    from pg_core.doctor import Status

    deps = default_deps()

    def check(full: bool) -> Health:
        report = run_doctor(settings, deps, connect_app=full)
        checks = [
            {
                "name": r.name,
                "ok": r.status is not Status.FAIL,
                "status": r.status.value,
                "detail": r.detail[:500],
            }
            for r in report.results
        ]
        return Health(healthy=report.exit_code == 0, checks=checks)

    return check


@app.command("status")
def status() -> None:
    """Show this agent's enrolment (never the token) and run the quick health check."""
    from pg_agent import config

    try:
        current = config.load()
        typer.echo(f"agent {current.name} -> {current.api_url} ({config.config_path()})")
    except FileNotFoundError as exc:
        typer.echo(str(exc))
    result = health_check(Settings())(False)
    for check in result.checks:
        typer.echo(f"  {check['status']:<4} {check['name']}: {check['detail']}")
    typer.echo("healthy" if result.healthy else "UNHEALTHY")


@app.command("run")
def run(
    poll: Annotated[float, typer.Option(help="Seconds between claims when idle.")] = 5.0,
) -> None:
    """Claim and run jobs until Ctrl+C (once: finish the current job; twice: stop now)."""
    from pg_agent import config
    from pg_agent.client import AgentApi
    from pg_agent.executor import Executor, InstallError
    from pg_agent.loop import AgentLoop
    from pg_api import logs
    from pg_cli.run import device_context_args

    logs.configure()
    settings = Settings()
    if not settings.pg_android_package:
        raise typer.BadParameter("PG_ANDROID_PACKAGE is not set")
    current = config.load()

    def context_args(locator_tag: str) -> dict[str, Any]:
        try:
            return dict(device_context_args(settings, locator_tag))
        except Exception as exc:  # the device chain is down: an infra failure, retried
            raise InstallError(f"cannot build a run context: {exc}") from exc

    api = AgentApi(current.api_url, current.token.get_secret_value())
    cache = platformdirs.user_cache_path("proving-ground", appauthor=False) / "apks"
    executor = Executor(
        api=api,
        package=settings.pg_android_package,
        serial=settings.pg_adb_serial,
        context_args=context_args,
        root=Path(),
        work_dir=Path("artifacts/agent"),
        cache_dir=cache,
    )
    AgentLoop(api=api, executor=executor, health=health_check(settings), poll_s=poll).run_forever()
