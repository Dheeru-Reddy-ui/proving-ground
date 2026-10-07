"""Unit tests for the adb wrapper; subprocess is faked, no device needed."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from pg_runner import adb as adb_mod
from pg_runner.adb import Adb, AdbError


class Recorder:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.stdout = stdout
        self.returncode = returncode

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder(stdout="ok\n")
    monkeypatch.setattr(adb_mod.subprocess, "run", rec)
    return rec


def test_run_passes_serial_timeout_and_no_shell(recorder: Recorder) -> None:
    result = Adb("adb.exe", timeout_s=7).run("shell", "pm", "path", "x", serial="SER")
    argv, kwargs = recorder.calls[0]
    assert argv == ["adb.exe", "-s", "SER", "shell", "pm", "path", "x"]
    assert kwargs["timeout"] == 7
    assert "shell" not in kwargs  # never shell=True
    assert result.stdout == "ok\n"
    assert result.returncode == 0


def test_run_without_serial_and_with_per_call_timeout(recorder: Recorder) -> None:
    Adb("adb.exe").run("devices", timeout_s=2)
    argv, kwargs = recorder.calls[0]
    assert argv == ["adb.exe", "devices"]
    assert kwargs["timeout"] == 2


def test_helpers_build_expected_commands(recorder: Recorder) -> None:
    adb = Adb("adb.exe")
    adb.version()
    adb.devices()
    adb.pm_path("com.unity.trashdash", "SER")
    adb.reverse_list("SER")
    assert [argv[1:] for argv, _ in recorder.calls] == [
        ["version"],
        ["devices", "-l"],
        ["-s", "SER", "shell", "pm", "path", "com.unity.trashdash"],
        ["-s", "SER", "reverse", "--list"],
    ]


def test_missing_adb_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adb_mod.shutil, "which", lambda name: None)
    adb = Adb()
    assert adb.path is None
    with pytest.raises(AdbError, match="not found"):
        adb.run("devices")


def test_timeout_raises_adb_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(argv: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(adb_mod.subprocess, "run", boom)
    with pytest.raises(AdbError, match="timed out after 3s"):
        Adb("adb.exe", timeout_s=3).run("devices")


def test_os_error_raises_adb_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(argv: list[str], **kwargs: Any) -> None:
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(adb_mod.subprocess, "run", boom)
    with pytest.raises(AdbError, match="could not run adb"):
        Adb("adb.exe").run("devices")


def test_path_defaults_to_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adb_mod.shutil, "which", lambda name: f"/bin/{name}")
    assert Adb().path == "/bin/adb"
