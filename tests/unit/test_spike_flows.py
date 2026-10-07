"""Unit tests for the spike flows (reset, logs, dump, time scale) with a fake phone and driver."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pytest
import typer

from pg_cli import spike
from pg_cli.settings import Settings
from pg_cli.spike import FIRST_LAUNCH, POPSTATE_ERROR, SpikeEnv
from pg_runner.adb import AdbResult
from pg_sdk._driver import LogLine


@dataclass
class FakeSession:
    player_data: dict[str, Any] = field(default_factory=lambda: dict(FIRST_LAUNCH))
    scene: str = "Start"
    emit_log: bool = True
    clock: list[float] = field(default_factory=lambda: [0.0])
    scale: float = 1.0
    calls: list[str] = field(default_factory=list)
    closed: bool = False
    listener: Any = None

    def current_scene(self) -> str:
        return self.scene

    def loaded_scenes(self) -> list[str]:
        return [self.scene]

    def wait_for_scene(self, name: str, timeout_s: float) -> None:
        self.calls.append(f"wait:{name}")

    def dump_elements(self, *, with_components: bool) -> tuple[list[dict[str, Any]], Any]:
        element = {
            "name": "Canvas",
            "id": 1,
            "x": 1,
            "y": 2,
            "mobileY": 3,
            "enabled": True,
            "transformId": 10,
            "transformParentId": 0,
            "idCamera": None,
        }
        return [element], ({1: ["Canvas"]} if with_components else None)

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")

    def add_log_listener(self, callback: Any) -> None:
        self.listener = callback

    def remove_log_listener(self) -> None:
        self.calls.append("remove_listener")

    def call_static_method(
        self, type_name: str, method: str, assembly: str, parameters: Any
    ) -> Any:
        self.calls.append(f"call:{type_name}.{method}")
        message = parameters[0] if parameters else POPSTATE_ERROR
        if self.emit_log and self.listener is not None:
            self.listener(LogLine(message=message, stack_trace="", level=0))
        return None

    def get_static_property(self, component: str, path: str, assembly: str) -> Any:
        if component == "PlayerData":
            key = path.removeprefix("instance.")
            if key not in self.player_data:
                raise RuntimeError(f"PropertyNotFoundException: {key}")
            return self.player_data[key]
        if component == "UnityEngine.Time":
            return self.clock[0] * self.scale
        return self.clock[0] * self.scale * 7.5  # a gameplay metric moving at 7.5 units/s

    def time_scale(self) -> float:
        return self.scale

    def set_time_scale(self, scale: float) -> None:
        self.calls.append(f"scale:{scale}")
        self.scale = scale

    def close(self) -> None:
        self.closed = True


class FakeAdb:
    def __init__(self, pid: int | None = 4242, logcat_lines: list[str] | None = None) -> None:
        self.pid = pid
        self.logcat_lines = logcat_lines or []

    def pidof(self, package: str, serial: str | None) -> int | None:
        return self.pid

    def resolve_activity(self, package: str, serial: str | None) -> str | None:
        return f"{package}/com.unity3d.player.UnityPlayerActivity"

    def pm_clear(self, package: str, serial: str | None) -> AdbResult:
        return AdbResult(("adb",), 0, "Success\n", "")

    def ui_dump(self, serial: str | None) -> str:
        return '<?xml version="1.0" ?><hierarchy rotation="0"></hierarchy>'

    def input_tap(self, x: int, y: int, serial: str | None) -> AdbResult:
        return AdbResult(("adb",), 0, "", "")

    def am_start(self, component: str, serial: str | None) -> AdbResult:
        return AdbResult(("adb",), 0, "Status: ok\nTotalTime: 812\n", "")


class FakeLogcat:
    lines_to_return: ClassVar[list[str]] = []

    def __init__(self, adb: Any, pid: int, serial: str | None, out_file: Path) -> None:
        self.args = (pid, serial, out_file)

    def __enter__(self) -> FakeLogcat:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def lines(self) -> list[str]:
        return FakeLogcat.lines_to_return


def make_env(tmp_path: Path, session: FakeSession, adb: FakeAdb | None = None) -> SpikeEnv:
    def sleep(seconds: float) -> None:
        session.clock[0] += seconds

    settings = Settings(_env_file=None, pg_android_package="com.unity.trashdash")  # type: ignore[call-arg]
    return SpikeEnv(
        settings=settings,
        adb=adb or FakeAdb(),  # type: ignore[arg-type]
        open_session=lambda timeout_s: session,  # type: ignore[arg-type,return-value]
        out_dir=tmp_path / "abc123def456",
        sleep=sleep,
        clock=lambda: session.clock[0],
        now=lambda: "2026-10-07T12:00:00+00:00",
    )


def test_reset_reaches_first_launch_state(tmp_path: Path) -> None:
    session = FakeSession()
    result = spike.run_reset(make_env(tmp_path, session), "Start", 60)
    assert result["first_launch_state"] is True
    assert result["am_start_total_time_ms"] == 812
    assert result["activity"] == "com.unity.trashdash/com.unity3d.player.UnityPlayerActivity"
    assert session.calls == ["wait:Start"]
    assert session.closed
    written = list((tmp_path / "abc123def456").glob("reset_*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text(encoding="utf-8"))["pm_clear"] == "Success"


def test_reset_reports_leftover_state_and_read_errors(tmp_path: Path) -> None:
    leftover = dict(FIRST_LAUNCH, coins=1500)
    del leftover["tutorialDone"]
    result = spike.run_reset(make_env(tmp_path, FakeSession(player_data=leftover)), "Start", 60)
    assert result["first_launch_state"] is False
    assert "coins: expected 0, got 1500" in result["diffs_from_first_launch"]
    assert "tutorialDone" in result["read_errors"]


def test_reset_needs_package(tmp_path: Path) -> None:
    env = make_env(tmp_path, FakeSession())
    env.settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(typer.BadParameter):
        spike.run_reset(env, "Start", 60)


@pytest.mark.parametrize("trigger", ["debug", "popstate"])
def test_logs_found_in_both_channels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, trigger: str
) -> None:
    monkeypatch.setattr(spike, "LogcatCapture", FakeLogcat)
    session = FakeSession()
    env = make_env(tmp_path, session)
    monkeypatch.setattr(spike.uuid, "uuid4", lambda: type("U", (), {"hex": "feedfacecafe0000"})())
    marker = "PG_SPIKE_LOGERROR feedfacecafe" if trigger == "debug" else POPSTATE_ERROR
    FakeLogcat.lines_to_return = [f"10-07 12:00:01.000 4242 4300 E Unity   : {marker}"]
    result = spike.run_logs(env, 2.0, trigger)
    assert result["in_both_channels"] is True
    assert result["trigger_error"] is None
    assert len(result["notification_hits"]) == 1
    assert session.calls[-1] == "remove_listener"


def test_logs_missing_from_logcat_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(spike, "LogcatCapture", FakeLogcat)
    FakeLogcat.lines_to_return = ["unrelated line"]
    result = spike.run_logs(make_env(tmp_path, FakeSession()), 1.0, "debug")
    assert result["in_both_channels"] is False


def test_logs_records_trigger_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(spike, "LogcatCapture", FakeLogcat)
    FakeLogcat.lines_to_return = []

    class Failing(FakeSession):
        def call_static_method(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("MethodNotFoundException")

    result = spike.run_logs(make_env(tmp_path, Failing()), 1.0, "debug")
    assert result["trigger_error"] == "RuntimeError: MethodNotFoundException"
    assert result["in_both_channels"] is False


def test_logs_needs_a_running_game(tmp_path: Path) -> None:
    with pytest.raises(typer.BadParameter, match="not running"):
        spike.run_logs(make_env(tmp_path, FakeSession(), FakeAdb(pid=None)), 1.0, "debug")


def test_logs_rejects_unknown_trigger(tmp_path: Path) -> None:
    with pytest.raises(typer.BadParameter):
        spike.run_logs(make_env(tmp_path, FakeSession()), 1.0, "nope")


def test_timescale_measures_and_restores(tmp_path: Path) -> None:
    session = FakeSession(scale=1.0)
    result = spike.run_timescale(
        make_env(tmp_path, session), 2.0, 5.0, "TrackManager:instance.worldDistance"
    )
    assert result["game_time_ok"] is True
    assert result["metric_ratio"] == pytest.approx(2.0)
    assert session.calls[-1] == "scale:1.0"  # restored


def test_timescale_without_metric(tmp_path: Path) -> None:
    result = spike.run_timescale(make_env(tmp_path, FakeSession()), 2.0, 5.0, None)
    assert result["metric_ratio"] is None
    assert result["game_time_ok"] is True


def test_dump_and_connect(tmp_path: Path) -> None:
    env = make_env(tmp_path, FakeSession(scene="Main"))
    result = spike.run_dump(env, "loadout", with_components=True)
    assert result["elements"] == 1
    dump = json.loads(Path(result["dump"]).read_text(encoding="utf-8"))
    assert dump["scene"] == "Main"
    assert dump["elements"][0]["path"] == "/Canvas"
    assert Path(result["screenshot"]).name == "scene_Main_loadout.png"
    assert spike.run_connect(env)["scene"] == "Main"


def test_apk_sha256(tmp_path: Path) -> None:
    apk = tmp_path / "game.apk"
    apk.write_bytes(b"abc")
    assert spike.apk_sha256(apk) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert spike.apk_sha256(None) is None
    assert spike.apk_sha256(tmp_path / "missing.apk") is None
