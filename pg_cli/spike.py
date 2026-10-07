"""`pg spike ...`: Phase 0 connectivity and introspection checks against the real phone (M0.4).

Each command prints a short summary and writes its evidence under
`artifacts/spike/<build>/`, where <build> is the first 12 hex chars of the APK's sha256.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from pg_cli.settings import Settings
from pg_core.scene import build_scene_dump
from pg_core.spike import TimeScaleSample, build_tag, check_time_scale, diff_expected, lines_with
from pg_runner.adb import Adb, LogcatCapture
from pg_runner.launch import fresh_launch
from pg_sdk._driver import AltTesterSession, LogLine, connect

ARTIFACTS = Path("artifacts/spike")
CONNECT_TIMEOUT_S = 20.0
GAME_ASSEMBLY = "Assembly-CSharp"
UNITY_CORE = "UnityEngine.CoreModule"

# First-launch values: PlayerData.NewSave() plus the class's field defaults (game script
# Assets/Scripts/PlayerData.cs). `pm clear` deletes save.bin, so a reset must land here.
FIRST_LAUNCH: dict[str, Any] = {
    "coins": 0,
    "premium": 0,
    "characters": ["Trash Cat"],
    "themes": ["Day"],
    "ftueLevel": 0,
    "licenceAccepted": False,
    "tutorialDone": False,
}

# GameManager.PopState() logs this with Debug.LogError when only one state is on the stack
# (game script Assets/Scripts/GameManager/GameManager.cs).
POPSTATE_ERROR = "Can't pop states, only one in stack."

app = typer.Typer(
    help="Phase 0 spike: connect, dump, screenshot, logs, reset and time scale (needs the phone).",
    no_args_is_help=True,
)


@dataclass
class SpikeEnv:
    settings: Settings
    adb: Adb
    open_session: Callable[[float], AltTesterSession]
    out_dir: Path
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], str] = field(default=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    @property
    def serial(self) -> str | None:
        return self.settings.pg_adb_serial

    def package(self) -> str:
        if not self.settings.pg_android_package:
            raise typer.BadParameter("PG_ANDROID_PACKAGE is not set (see .env.example)")
        return self.settings.pg_android_package

    @contextmanager
    def session(self, timeout_s: float = CONNECT_TIMEOUT_S) -> Iterator[AltTesterSession]:
        session = self.open_session(timeout_s)
        try:
            yield session
        finally:
            session.close()

    def write_json(self, name: str, payload: Any) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / name
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8", newline="\n")
        return path


def apk_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_env() -> SpikeEnv:
    settings = Settings()

    def open_session(timeout_s: float) -> AltTesterSession:
        return connect(settings.pg_alttester_host, settings.pg_alttester_port, timeout_s)

    out_dir = ARTIFACTS / build_tag(apk_sha256(settings.pg_apk_path))
    return SpikeEnv(settings=settings, adb=Adb(), open_session=open_session, out_dir=out_dir)


def _stamp(now: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", now)


# ----------------------------------------------------------------------------- connect


def run_connect(env: SpikeEnv) -> dict[str, Any]:
    with env.session() as session:
        return {
            "scene": session.current_scene(),
            "loaded_scenes": session.loaded_scenes(),
            "time_scale": session.time_scale(),
        }


# ----------------------------------------------------------------------------- dump


def run_dump(env: SpikeEnv, label: str | None, with_components: bool) -> dict[str, Any]:
    with env.session() as session:
        scene = session.current_scene()
        raw, components = session.dump_elements(with_components=with_components)
        stem = f"scene_{scene}" + (f"_{label}" if label else "")
        screenshot = env.out_dir / f"{stem}.png"
        session.screenshot(screenshot)
    dump = build_scene_dump(
        scene=scene,
        label=label,
        build=env.out_dir.name,
        captured_at=env.now(),
        raw=raw,
        components=components,
    )
    path = env.out_dir / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    return {
        "scene": scene,
        "elements": dump.element_count,
        "vanished_during_dump": dump.vanished_during_dump,
        "dump": str(path),
        "screenshot": str(screenshot),
    }


# ----------------------------------------------------------------------------- logs


def run_logs(env: SpikeEnv, wait_s: float, trigger: str) -> dict[str, Any]:
    """Trigger a Debug.LogError in the game and look for it in both log channels."""
    package = env.package()
    pid = env.adb.pidof(package, env.serial)
    if pid is None:
        raise typer.BadParameter(f"{package} is not running on the phone; launch it first")
    parameters: list[str]
    if trigger == "debug":
        marker = f"PG_SPIKE_LOGERROR {uuid.uuid4().hex[:12]}"
        type_name, method, assembly, parameters = (
            "UnityEngine.Debug",
            "LogError",
            UNITY_CORE,
            [marker],
        )
    elif trigger == "popstate":
        marker = POPSTATE_ERROR
        type_name, method, assembly, parameters = (
            "GameManager",
            "instance.PopState",
            GAME_ASSEMBLY,
            [],
        )
    else:
        raise typer.BadParameter("trigger must be 'debug' or 'popstate'")

    received: list[LogLine] = []
    trigger_error: str | None = None
    logcat = LogcatCapture(env.adb, pid, env.serial, env.out_dir / "logs_logcat.txt")
    with logcat, env.session() as session:
        session.add_log_listener(received.append)
        env.sleep(1.0)  # let both streams start before the trigger
        try:
            session.call_static_method(type_name, method, assembly, parameters=parameters)
        except Exception as exc:  # spike: record whatever the driver raised
            trigger_error = f"{type(exc).__name__}: {exc}"
        env.sleep(wait_s)
        session.remove_log_listener()

    notification_hits = [line for line in received if marker in line.message]
    logcat_hits = lines_with(marker, logcat.lines())
    result = {
        "trigger": trigger,
        "call": f"{type_name}.{method} ({assembly})",
        "marker": marker,
        "trigger_error": trigger_error,
        "pid": pid,
        "notifications_received": len(received),
        "notification_hits": [line.model_dump() for line in notification_hits],
        "logcat_hits": logcat_hits,
        "in_both_channels": bool(notification_hits) and bool(logcat_hits),
    }
    env.write_json(f"logs_check_{_stamp(env.now())}.json", result)
    return result


# ----------------------------------------------------------------------------- reset


def _total_time_ms(am_start_output: str) -> int | None:
    match = re.search(r"TotalTime:\s*(\d+)", am_start_output)
    return int(match.group(1)) if match else None


def run_reset(env: SpikeEnv, expect_scene: str, timeout_s: float) -> dict[str, Any]:
    """`pm clear`, relaunch (dismissing Android's 16 KB warning), wait for the first screen,
    and compare the save with first launch."""
    package = env.package()
    activity = env.adb.resolve_activity(package, env.serial)
    if activity is None:
        raise typer.BadParameter(f"could not resolve the launch activity of {package}")

    started = env.clock()
    launch = fresh_launch(env.adb, package, activity, env.serial, clock=env.clock, sleep=env.sleep)
    after_launch = env.clock()

    player_data: dict[str, Any] = {}
    read_errors: dict[str, str] = {}
    with env.session(timeout_s) as session:
        after_connect = env.clock()
        session.wait_for_scene(expect_scene, timeout_s)
        after_scene = env.clock()
        for key in FIRST_LAUNCH:
            try:
                player_data[key] = session.get_static_property(
                    "PlayerData", f"instance.{key}", GAME_ASSEMBLY
                )
            except Exception as exc:  # spike: record the driver's answer as-is
                read_errors[key] = f"{type(exc).__name__}: {exc}"

    diffs = diff_expected(player_data, FIRST_LAUNCH)
    result = {
        "package": package,
        "activity": activity,
        "pm_clear": launch.pm_clear,
        "am_start_total_time_ms": _total_time_ms(launch.am_start),
        "compat_dialog_dismissed": launch.dialog_dismissed,
        "seconds": {
            **launch.seconds,
            "driver_connected": round(after_connect - after_launch, 2),
            "first_scene": round(after_scene - after_connect, 2),
            "total": round(after_scene - started, 2),
        },
        "expected_scene": expect_scene,
        "player_data": player_data,
        "read_errors": read_errors,
        "diffs_from_first_launch": diffs,
        "first_launch_state": not diffs and not read_errors,
    }
    env.write_json(f"reset_{_stamp(env.now())}.json", result)
    return result


# ----------------------------------------------------------------------------- time scale


def _read_float(session: AltTesterSession, component: str, path: str, assembly: str) -> float:
    return float(session.get_static_property(component, path, assembly))


def _sample(
    env: SpikeEnv, session: AltTesterSession, scale: float, seconds: float, metric: str | None
) -> TimeScaleSample:
    component, _, path = (metric or "").partition(":")
    game0 = _read_float(session, "UnityEngine.Time", "time", UNITY_CORE)
    metric0 = _read_float(session, component, path, GAME_ASSEMBLY) if metric else None
    wall0 = env.clock()
    env.sleep(seconds)
    game1 = _read_float(session, "UnityEngine.Time", "time", UNITY_CORE)
    metric1 = _read_float(session, component, path, GAME_ASSEMBLY) if metric else None
    wall1 = env.clock()
    delta = metric1 - metric0 if metric0 is not None and metric1 is not None else None
    return TimeScaleSample(
        scale=scale, wall_seconds=wall1 - wall0, game_seconds=game1 - game0, metric_delta=delta
    )


def run_timescale(
    env: SpikeEnv, scale: float, seconds: float, metric: str | None
) -> dict[str, Any]:
    """Measure game time (and an optional gameplay metric) at 1x and at `scale`, then restore."""
    with env.session() as session:
        original = session.time_scale()
        try:
            session.set_time_scale(1.0)
            base = _sample(env, session, 1.0, seconds, metric)
            session.set_time_scale(scale)
            scaled = _sample(env, session, scale, seconds, metric)
        finally:
            session.set_time_scale(original)
    ok, detail = check_time_scale(base, scaled)
    metric_ratio = (
        scaled.metric_delta / base.metric_delta
        if base.metric_delta and scaled.metric_delta is not None
        else None
    )
    result = {
        "requested_scale": scale,
        "restored_scale": original,
        "base": base.model_dump(),
        "scaled": scaled.model_dump(),
        "game_time_ok": ok,
        "game_time_detail": detail,
        "metric": metric,
        "metric_ratio": metric_ratio,
    }
    env.write_json(f"timescale_{_stamp(env.now())}.json", result)
    return result


# ----------------------------------------------------------------------------- CLI


def _echo(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.command("connect")
def connect_cmd() -> None:
    """Connect and print the current scene, loaded scenes and time scale."""
    _echo(run_connect(default_env()))


@app.command("dump")
def dump_cmd(
    label: Annotated[
        str | None, typer.Option(help="Suffix for the file name, e.g. 'shop_characters'.")
    ] = None,
    components: Annotated[
        bool,
        typer.Option("--components/--no-components", help="Also list each object's components."),
    ] = True,
) -> None:
    """Dump every object in the current scene to JSON and take a screenshot."""
    _echo(run_dump(default_env(), label, components))


@app.command("screenshot")
def screenshot_cmd(
    label: Annotated[str, typer.Option(help="File name stem.")] = "screen",
) -> None:
    """Save a screenshot of the current screen."""
    env = default_env()
    path = env.out_dir / f"{label}.png"
    with env.session() as session:
        session.screenshot(path)
    _echo({"screenshot": str(path)})


@app.command("logs")
def logs_cmd(
    wait: Annotated[float, typer.Option(help="Seconds to keep listening after the trigger.")] = 5.0,
    trigger: Annotated[
        str, typer.Option(help="'debug' = UnityEngine.Debug.LogError; 'popstate' = game's own.")
    ] = "debug",
) -> None:
    """Check that a Debug.LogError from the game reaches both AltTester and logcat."""
    result = run_logs(default_env(), wait, trigger)
    _echo(result)
    raise typer.Exit(0 if result["in_both_channels"] else 1)


@app.command("reset")
def reset_cmd(
    expect_scene: Annotated[str, typer.Option(help="Scene of the first screen.")] = "Start",
    timeout: Annotated[float, typer.Option(help="Seconds to wait for the app.")] = 90.0,
) -> None:
    """`pm clear` + relaunch; time it and verify the save is back to first-launch values."""
    result = run_reset(default_env(), expect_scene, timeout)
    _echo(result)
    raise typer.Exit(0 if result["first_launch_state"] else 1)


@app.command("timescale")
def timescale_cmd(
    scale: Annotated[float, typer.Option(help="Time scale to test.")] = 2.0,
    seconds: Annotated[float, typer.Option(help="Wall seconds per measurement window.")] = 5.0,
    metric: Annotated[
        str | None,
        typer.Option(
            help="Gameplay metric as Component:path, e.g. TrackManager:instance.worldDistance"
        ),
    ] = None,
) -> None:
    """Check that SetTimeScale speeds up game time (and optionally a gameplay metric)."""
    result = run_timescale(default_env(), scale, seconds, metric)
    _echo(result)
    raise typer.Exit(0 if result["game_time_ok"] else 1)
