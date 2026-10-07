"""Unit tests for the adb helpers used by the spike: pidof, pm clear, launch, logcat capture."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, ClassVar

import pytest

from pg_runner import adb as adb_mod
from pg_runner.adb import Adb, LogcatCapture, parse_pidof, parse_resolve_activity


@pytest.mark.parametrize(
    ("output", "pid"), [("12345\n", 12345), ("12345 678\n", 12345), ("", None), ("x\n", None)]
)
def test_parse_pidof(output: str, pid: int | None) -> None:
    assert parse_pidof(output) == pid


def test_parse_resolve_activity_uses_last_line() -> None:
    # Real output for the Settings app on the project phone.
    output = (
        "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n"
        "com.android.settings/.Settings\n"
    )
    assert parse_resolve_activity(output) == "com.android.settings/.Settings"
    assert parse_resolve_activity("No activity found\n") is None
    assert parse_resolve_activity("") is None


class Recorder:
    def __init__(self, stdout: str) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.stdout = stdout

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, self.stdout, "")


def test_device_helpers_build_expected_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder("4242\n")
    monkeypatch.setattr(adb_mod.subprocess, "run", rec)
    adb = Adb("adb.exe")
    assert adb.pidof("com.unity.trashdash", "SER") == 4242
    adb.pm_clear("com.unity.trashdash", "SER")
    adb.resolve_activity("com.unity.trashdash", "SER")
    adb.am_start("com.unity.trashdash/com.unity3d.player.UnityPlayerActivity", "SER")
    commands = [argv[3:] for argv, _ in rec.calls]
    assert commands == [
        ["shell", "pidof", "com.unity.trashdash"],
        ["shell", "pm", "clear", "com.unity.trashdash"],
        ["shell", "cmd", "package", "resolve-activity", "--brief", "com.unity.trashdash"],
        [
            "shell",
            "am",
            "start",
            "-W",
            "-n",
            "com.unity.trashdash/com.unity3d.player.UnityPlayerActivity",
        ],
    ]
    assert rec.calls[3][1]["timeout"] == adb_mod.LAUNCH_TIMEOUT_S


class FakePopen:
    instances: ClassVar[list[FakePopen]] = []

    def __init__(self, argv: list[str], stdout: Any, **kwargs: Any) -> None:
        self.argv = argv
        stdout.write("10-07 12:00:00.000  4242  4300 E Unity   : PG_SPIKE_LOGERROR abc\n")
        self.terminated = False
        FakePopen.instances.append(self)

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> int:
        return 0


def test_logcat_capture_streams_to_file_and_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakePopen.instances = []
    monkeypatch.setattr(adb_mod.subprocess, "Popen", FakePopen)
    out = tmp_path / "logs" / "logcat.txt"
    capture = LogcatCapture(Adb("adb.exe"), 4242, "SER", out)
    with capture:
        pass
    (proc,) = FakePopen.instances
    assert proc.argv == [
        "adb.exe",
        "-s",
        "SER",
        "logcat",
        "-v",
        "threadtime",
        "-T",
        "1",
        "--pid=4242",
    ]
    assert proc.terminated
    assert capture.lines() == ["10-07 12:00:00.000  4242  4300 E Unity   : PG_SPIKE_LOGERROR abc"]


def test_logcat_capture_kills_a_process_that_will_not_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Stubborn(FakePopen):
        killed = False

        def wait(self, timeout: float) -> int:
            if not self.killed:
                raise subprocess.TimeoutExpired("adb", timeout)
            return 0

        def kill(self) -> None:
            self.killed = True

    FakePopen.instances = []
    monkeypatch.setattr(adb_mod.subprocess, "Popen", Stubborn)
    with LogcatCapture(Adb("adb.exe"), 1, None, tmp_path / "l.txt"):
        pass
    assert FakePopen.instances[0].killed  # type: ignore[attr-defined]


def test_ui_dump_and_tap_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder("<hierarchy/>")
    monkeypatch.setattr(adb_mod.subprocess, "run", rec)
    adb = Adb("adb.exe")
    assert adb.ui_dump("SER") == "<hierarchy/>"
    adb.input_tap(328, 2947, "SER")
    assert [argv[3:] for argv, _ in rec.calls] == [
        ["shell", "uiautomator", "dump", adb_mod.UI_DUMP_PATH],
        ["exec-out", "cat", adb_mod.UI_DUMP_PATH],
        ["shell", "input", "tap", "328", "2947"],
    ]
