"""`pg smoke`: the Phase 0 reliability baseline (M0.7).

One iteration: `pm clear` + launch (dismissing Android's 16 KB warning, ADR-0008) -> Start
-> tap START -> main menu -> open store -> close store.
This is infrastructure, not part of any test suite. Results go to `artifacts/smoke/<build>/`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

import typer

from pg_cli.spike import ARTIFACTS, SpikeEnv, default_env
from pg_core.smoke import SmokeRun, summarize
from pg_runner.launch import fresh_launch
from pg_sdk._driver import AltTesterSession, LogLine

START_BUTTON = "/Canvas/StartButton"
STORE_BUTTON = "/UICamera/Loadout/StoreButton"
STORE_CLOSE = "/Canvas/Background/Button"
CONNECT_TIMEOUT_S = 60.0
STEP_TIMEOUT_S = 30.0
ERROR_LEVEL = 4  # level of a Debug.LogError notification, observed in the M0.4 log check


class StepFailed(RuntimeError):
    pass


def _visible(session: AltTesterSession, path: str, size: tuple[int, int]) -> bool:
    pos = session.position(path)
    return pos is not None and 0 <= pos[0] <= size[0] and 0 <= pos[1] <= size[1]


def _wait(env: SpikeEnv, what: str, condition: Callable[[], bool], timeout_s: float) -> None:
    deadline = env.clock() + timeout_s
    while not condition():
        if env.clock() >= deadline:
            raise StepFailed(f"timed out after {timeout_s:g}s waiting for {what}")
        env.sleep(0.5)


def run_once(env: SpikeEnv, index: int, activity: str) -> SmokeRun:
    package = env.package()
    steps: dict[str, float] = {}
    started = env.clock()
    mark = started
    current = "reset"
    logs: list[LogLine] = []

    def done(name: str) -> None:
        nonlocal mark
        now = env.clock()
        steps[name] = round(now - mark, 2)
        mark = now

    session: AltTesterSession | None = None
    dialog_dismissed = False
    try:
        launch = fresh_launch(
            env.adb, package, activity, env.serial, clock=env.clock, sleep=env.sleep
        )
        dialog_dismissed = launch.dialog_dismissed
        done("reset")
        current = "connect"
        session = env.open_session(CONNECT_TIMEOUT_S)
        session.add_log_listener(logs.append)
        size = session.screen_size()
        session.wait_for_scene("Start", STEP_TIMEOUT_S)
        done("connect")
        current = "start_to_main_menu"
        session.tap(START_BUTTON)
        session.wait_for_scene("Main", STEP_TIMEOUT_S)
        live = session
        _wait(env, "store button", lambda: _visible(live, STORE_BUTTON, size), STEP_TIMEOUT_S)
        done("start_to_main_menu")
        current = "open_store"
        session.tap(STORE_BUTTON)
        _wait(
            env,
            "store",
            lambda: "Shop" in live.loaded_scenes() and _visible(live, STORE_CLOSE, size),
            STEP_TIMEOUT_S,
        )
        done("open_store")
        current = "close_store"
        session.tap(STORE_CLOSE)
        _wait(
            env,
            "main menu after closing the store",
            lambda: "Shop" not in live.loaded_scenes() and _visible(live, STORE_BUTTON, size),
            STEP_TIMEOUT_S,
        )
        done("close_store")
        passed, error, failed_step = True, None, None
    except Exception as exc:  # any failure is recorded with its step; the next run resets
        passed, error, failed_step = False, f"{type(exc).__name__}: {exc}", current
        if session is not None:
            try:
                session.screenshot(env.out_dir / f"smoke_fail_{index:02d}.png")
            except Exception as shot_exc:
                error += f" (screenshot failed: {type(shot_exc).__name__})"
    finally:
        if session is not None:
            session.close()
    error_logs = sum(1 for line in logs if line.level == ERROR_LEVEL or "Exception" in line.message)
    return SmokeRun(
        index=index,
        passed=passed,
        seconds=round(env.clock() - started, 2),
        steps=steps,
        failed_step=failed_step,
        error=error,
        error_logs=error_logs,
        compat_dialog_dismissed=dialog_dismissed,
    )


def run_smoke(env: SpikeEnv, runs: int, echo: Callable[[str], Any] = print) -> dict[str, Any]:
    package = env.package()
    activity = env.adb.resolve_activity(package, env.serial)
    if activity is None:
        raise typer.BadParameter(f"could not resolve the launch activity of {package}")
    run_id = f"smoke-{_compact(env.now())}-{uuid.uuid4().hex[:6]}"
    results: list[SmokeRun] = []
    for index in range(1, runs + 1):
        result = run_once(env, index, activity)
        results.append(result)
        status = "PASS" if result.passed else f"FAIL at {result.failed_step}: {result.error}"
        echo(f"run {index:2d}/{runs}: {status} ({result.seconds:.1f}s)")
    summary = summarize(results)
    payload = {
        "run_id": run_id,
        "build": env.out_dir.name,
        "started_at": env.now(),
        "scenario": "pm clear + launch -> Start -> START -> main menu -> open store -> close store",
        "summary": summary.model_dump(),
        "runs": [r.model_dump() for r in results],
    }
    env.write_json(f"{run_id}.json", payload)
    return payload


def _compact(timestamp: str) -> str:
    return "".join(ch for ch in timestamp if ch.isalnum())


def smoke_cmd(
    runs: Annotated[int, typer.Option(help="Number of runs, each from a fresh reset.")] = 20,
) -> None:
    """Reliability baseline: reset, launch, main menu, open and close the store, N times."""
    base = default_env()
    env = SpikeEnv(
        settings=base.settings,
        adb=base.adb,
        open_session=base.open_session,
        out_dir=ARTIFACTS.parent / "smoke" / base.out_dir.name,
    )
    payload = run_smoke(env, runs, typer.echo)
    s = payload["summary"]
    typer.echo(
        f"\n{payload['run_id']}: {s['passed']}/{s['runs']} passed; "
        f"p50 {s['p50_seconds']}s, p95 {s['p95_seconds']}s; bar 19/20 met: {s['meets_bar']}"
    )
    raise typer.Exit(0 if s["meets_bar"] else 1)
