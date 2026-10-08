"""`pg run-test`: run one test file on the phone through the sandboxed runner."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
import yaml

from pg_cli.settings import Settings
from pg_cli.spike import apk_sha256
from pg_core.spike import build_tag
from pg_runner.adb import Adb
from pg_runner.runner import DEFAULT_TIMEOUT_S, RunRecord, make_context, run_test

RUNS_DIR = Path("artifacts/runs")
ALLOWLIST = Path("pg_sdk/game_error_allowlist.yaml")


def game_error_allowlist(path: Path = ALLOWLIST) -> list[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(p) for p in raw.get("patterns") or []]


def new_run_id(prefix: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"


def device_context_args(settings: Settings, locator_tag: str | None) -> dict[str, object]:
    package = settings.pg_android_package
    if not package:
        raise typer.BadParameter("PG_ANDROID_PACKAGE is not set")
    adb = Adb()
    activity = adb.resolve_activity(package, settings.pg_adb_serial)
    if activity is None:
        raise typer.BadParameter(f"{package} is not installed on the device")
    return {
        "locator_tag": locator_tag or build_tag(apk_sha256(settings.pg_apk_path)),
        "package": package,
        "activity": activity,
        "adb_serial": settings.pg_adb_serial,
        "alttester_host": settings.pg_alttester_host,
        "alttester_port": settings.pg_alttester_port,
        "allowlist": game_error_allowlist(),
    }


def run_test_cmd(
    test_file: Annotated[Path, typer.Argument(help="A test file with one test function.")],
    flag: Annotated[list[str] | None, typer.Option(help="Bug flag to turn on.")] = None,
    runs: Annotated[int, typer.Option(help="Executions, each from a fresh reset.")] = 1,
    timeout: Annotated[float, typer.Option(help="Seconds per attempt.")] = DEFAULT_TIMEOUT_S,
    retries: Annotated[int, typer.Option(help="Retries of infra failures per execution.")] = 2,
    locator_tag: Annotated[str | None, typer.Option(help="Locator map tag.")] = None,
    test: Annotated[str | None, typer.Option(help="Run only this test function.")] = None,
) -> None:
    """Run a test file through the sandboxed runner and print each execution's outcome."""
    args = device_context_args(Settings(), locator_tag)
    run_id = new_run_id("run")
    records: list[RunRecord] = []
    for index in range(1, runs + 1):
        context = make_context(
            run_id=run_id,
            flags=flag or [],
            artifact_dir=RUNS_DIR / run_id / f"exec_{index}",
            **args,  # type: ignore[arg-type]
        )
        record = run_test(test_file, context, timeout_s=timeout, max_retries=retries, select=test)
        records.append(record)
        final = record.final
        typer.echo(
            f"{index}/{runs}: {final.outcome.value} ({final.rule}) body {final.duration_s:.1f}s, "
            f"wall {final.wall_s:.1f}s, attempts {len(record.attempts)}"
            + (f"\n    {final.detail.splitlines()[-1][:200]}" if final.detail else "")
        )
    summary = RUNS_DIR / run_id / "records.json"
    summary.write_text(
        "[\n" + ",\n".join(r.model_dump_json(indent=2) for r in records) + "\n]\n",
        encoding="utf-8",
    )
    typer.echo(f"{run_id}: records in {summary}")
