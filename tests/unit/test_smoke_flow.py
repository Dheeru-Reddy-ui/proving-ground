"""Unit tests for the smoke flow against a fake phone that walks Start -> menu -> store -> menu."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pg_cli import smoke
from pg_cli.settings import Settings
from pg_cli.spike import SpikeEnv
from pg_runner.adb import AdbResult
from pg_sdk._driver import LogLine

OFF_SCREEN = (720.0, -622.0)


@dataclass
class FakeGame:
    """Tracks which screen is up; taps move between screens like the real game."""

    screen: str = "Start"
    broken: str | None = None  # a path whose tap does nothing
    error_logs: int = 0
    taps: list[str] = field(default_factory=list)
    closed: int = 0
    screenshots: list[Path] = field(default_factory=list)


class FakeSession:
    def __init__(self, game: FakeGame) -> None:
        self.game = game
        self.listener: Any = None

    def add_log_listener(self, callback: Any) -> None:
        self.listener = callback

    def screen_size(self) -> tuple[int, int]:
        return 1440, 3120

    def wait_for_scene(self, name: str, timeout_s: float) -> None:
        scene = "Start" if self.game.screen == "Start" else "Main"
        if scene != name:
            raise RuntimeError(f"WaitTimeOutException: scene {scene}")

    def loaded_scenes(self) -> list[str]:
        if self.game.screen == "Start":
            return ["Start"]
        return ["Main", "Shop"] if self.game.screen == "Store" else ["Main"]

    def position(self, path: str) -> tuple[float, float] | None:
        visible = {
            "Menu": {smoke.STORE_BUTTON},
            "Store": {smoke.STORE_CLOSE},
        }.get(self.game.screen, set())
        if path in visible:
            return 500.0, 300.0
        return OFF_SCREEN if path == smoke.STORE_BUTTON else None

    def tap(self, path: str) -> None:
        self.game.taps.append(path)
        if path == self.game.broken:
            return
        moves = {
            ("Start", smoke.START_BUTTON): "Menu",
            ("Menu", smoke.STORE_BUTTON): "Store",
            ("Store", smoke.STORE_CLOSE): "Menu",
        }
        self.game.screen = moves.get((self.game.screen, path), self.game.screen)
        if self.listener and self.game.error_logs:
            for _ in range(self.game.error_logs):
                self.listener(LogLine(message="NullReferenceException", stack_trace="", level=4))
            self.game.error_logs = 0

    def screenshot(self, path: Path) -> None:
        self.game.screenshots.append(path)

    def close(self) -> None:
        self.game.closed += 1


class FakeAdb:
    def __init__(self, game: FakeGame) -> None:
        self.game = game
        self.clears = 0

    def resolve_activity(self, package: str, serial: str | None) -> str | None:
        return f"{package}/com.unity3d.player.UnityPlayerActivity"

    def pm_clear(self, package: str, serial: str | None) -> AdbResult:
        self.clears += 1
        self.game.screen = "Start"
        return AdbResult(("adb",), 0, "Success\n", "")

    def ui_dump(self, serial: str | None) -> str:
        return '<?xml version="1.0" ?><hierarchy rotation="0"></hierarchy>'

    def input_tap(self, x: int, y: int, serial: str | None) -> AdbResult:
        return AdbResult(("adb",), 0, "", "")

    def am_start(self, component: str, serial: str | None) -> AdbResult:
        return AdbResult(("adb",), 0, "Status: ok\n", "")


def make_env(tmp_path: Path, game: FakeGame) -> tuple[SpikeEnv, FakeAdb]:
    clock = [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    adb = FakeAdb(game)
    settings = Settings(_env_file=None, pg_android_package="com.DefaultCompany.TrashCat")  # type: ignore[call-arg]
    env = SpikeEnv(
        settings=settings,
        adb=adb,  # type: ignore[arg-type]
        open_session=lambda timeout_s: FakeSession(game),  # type: ignore[arg-type,return-value]
        out_dir=tmp_path / "smoke" / "cf86374974de",
        sleep=sleep,
        clock=lambda: clock[0],
        now=lambda: "2026-10-07T16:00:00+00:00",
    )
    return env, adb


def test_all_runs_pass_and_results_are_written(tmp_path: Path) -> None:
    game = FakeGame()
    env, adb = make_env(tmp_path, game)
    lines: list[str] = []
    payload = smoke.run_smoke(env, 3, lines.append)
    assert payload["summary"]["passed"] == 3
    assert adb.clears == 3  # every run starts from a reset
    assert game.closed == 3  # the driver slot is always released
    assert all(line.endswith("s)") and "PASS" in line for line in lines)
    written = json.loads((env.out_dir / f"{payload['run_id']}.json").read_text(encoding="utf-8"))
    assert written["runs"][0]["steps"].keys() == {
        "reset",
        "connect",
        "start_to_main_menu",
        "open_store",
        "close_store",
    }


def test_a_stuck_step_is_recorded_with_a_screenshot(tmp_path: Path) -> None:
    game = FakeGame(broken=smoke.STORE_BUTTON)
    env, _ = make_env(tmp_path, game)
    payload = smoke.run_smoke(env, 1, lambda _: None)
    (run,) = payload["runs"]
    assert run["passed"] is False
    assert run["failed_step"] == "open_store"
    assert "timed out" in run["error"]
    assert game.screenshots == [env.out_dir / "smoke_fail_01.png"]
    assert game.closed == 1


def test_error_logs_are_counted_but_do_not_fail_the_run(tmp_path: Path) -> None:
    game = FakeGame(error_logs=2)
    env, _ = make_env(tmp_path, game)
    (run,) = smoke.run_smoke(env, 1, lambda _: None)["runs"]
    assert run["passed"] is True
    assert run["error_logs"] == 2


def test_connection_failure_is_recorded_at_connect(tmp_path: Path) -> None:
    game = FakeGame()
    env, _ = make_env(tmp_path, game)

    def refuse(timeout_s: float) -> FakeSession:
        raise RuntimeError("NoAppConnected")

    env.open_session = refuse  # type: ignore[assignment]
    (run,) = smoke.run_smoke(env, 1, lambda _: None)["runs"]
    assert run["failed_step"] == "connect"
    assert run["error"] == "RuntimeError: NoAppConnected"


def test_unresolvable_activity_is_rejected(tmp_path: Path) -> None:
    env, adb = make_env(tmp_path, FakeGame())
    adb.resolve_activity = lambda package, serial: None  # type: ignore[method-assign]
    with pytest.raises(smoke.typer.BadParameter):
        smoke.run_smoke(env, 1)
