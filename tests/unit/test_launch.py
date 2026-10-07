"""Unit tests for fresh_launch: pm clear, start, dismiss the 16 KB warning when it shows."""

from __future__ import annotations

from pathlib import Path

from pg_runner.adb import AdbResult
from pg_runner.launch import DIALOG_TIMEOUT_S, fresh_launch

DIALOG = (Path(__file__).parent / "fixtures" / "android16_compat_dialog.xml").read_text(
    encoding="utf-8"
)
NO_DIALOG = '<?xml version="1.0" ?><hierarchy rotation="0"></hierarchy>'


class FakeAdb:
    def __init__(self, dumps: list[str]) -> None:
        self.dumps = dumps
        self.calls: list[str] = []
        self.taps: list[tuple[int, int]] = []

    def pm_clear(self, package: str, serial: str | None) -> AdbResult:
        self.calls.append("pm_clear")
        return AdbResult(("adb",), 0, "Success\n", "")

    def am_start(self, component: str, serial: str | None) -> AdbResult:
        self.calls.append("am_start")
        return AdbResult(("adb",), 0, "Status: ok\nTotalTime: 134\n", "")

    def ui_dump(self, serial: str | None) -> str:
        self.calls.append("ui_dump")
        return self.dumps.pop(0) if self.dumps else NO_DIALOG

    def input_tap(self, x: int, y: int, serial: str | None) -> AdbResult:
        self.taps.append((x, y))
        return AdbResult(("adb",), 0, "", "")


def clocked() -> tuple[list[float], object, object]:
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    return now, clock, sleep


def test_dialog_is_dismissed_with_ok_once() -> None:
    adb = FakeAdb([NO_DIALOG, NO_DIALOG, DIALOG])
    _, clock, sleep = clocked()
    report = fresh_launch(adb, "pkg", "pkg/.Main", "SER", clock=clock, sleep=sleep)  # type: ignore[arg-type]
    assert report.dialog_dismissed
    assert adb.taps == [(328, 2947)]
    assert adb.calls[:2] == ["pm_clear", "am_start"]
    assert report.pm_clear == "Success"
    assert report.seconds["dialog"] == 1.0  # two 0.5 s polls before it appeared


def test_no_dialog_waits_out_the_timeout_without_tapping() -> None:
    adb = FakeAdb([])
    _, clock, sleep = clocked()
    report = fresh_launch(adb, "pkg", "pkg/.Main", None, clock=clock, sleep=sleep)  # type: ignore[arg-type]
    assert not report.dialog_dismissed
    assert adb.taps == []
    assert report.seconds["dialog"] == DIALOG_TIMEOUT_S
