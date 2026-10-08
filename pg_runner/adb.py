"""Thin, typed wrapper around the adb command line: fixed argument lists, no shell, timeouts."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO

DEFAULT_TIMEOUT_S = 15.0
LAUNCH_TIMEOUT_S = 60.0


class AdbError(RuntimeError):
    """adb could not be started or did not finish in time."""


@dataclass(frozen=True)
class AdbResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Adb:
    def __init__(self, executable: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.path = executable if executable is not None else shutil.which("adb")
        self.timeout_s = timeout_s

    def argv(self, *args: str, serial: str | None = None) -> list[str]:
        if self.path is None:
            raise AdbError("adb was not found on PATH")
        return [self.path, *(["-s", serial] if serial else []), *args]

    def run(
        self, *args: str, serial: str | None = None, timeout_s: float | None = None
    ) -> AdbResult:
        argv = self.argv(*args, serial=serial)
        limit = self.timeout_s if timeout_s is None else timeout_s
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, never a shell
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=limit,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"`adb {' '.join(args)}` timed out after {limit:g}s") from exc
        except OSError as exc:
            raise AdbError(f"could not run adb: {exc}") from exc
        return AdbResult(tuple(argv), completed.returncode, completed.stdout, completed.stderr)

    def version(self) -> str:
        return self.run("version").stdout

    def devices(self) -> str:
        return self.run("devices", "-l").stdout

    def pm_path(self, package: str, serial: str | None) -> str:
        return self.run("shell", "pm", "path", package, serial=serial).stdout

    def reverse_list(self, serial: str | None) -> str:
        return self.run("reverse", "--list", serial=serial).stdout

    def pidof(self, package: str, serial: str | None) -> int | None:
        return parse_pidof(self.run("shell", "pidof", package, serial=serial).stdout)

    def installed_apk_sha256(self, package: str, serial: str | None) -> str | None:
        """sha256 of the installed base APK, read on the device (`sha256sum`)."""
        paths = [
            line.removeprefix("package:").strip()
            for line in self.pm_path(package, serial).splitlines()
            if line.strip().endswith("base.apk")
        ]
        if not paths:
            return None
        output = self.run("shell", "sha256sum", paths[0], serial=serial, timeout_s=60).stdout
        digest = output.split()[0] if output.split() else ""
        return digest if len(digest) == 64 else None

    def force_stop(self, package: str, serial: str | None) -> AdbResult:
        """Stop the app without touching its data (a player closing the game)."""
        return self.run("shell", "am", "force-stop", package, serial=serial)

    def pm_clear(self, package: str, serial: str | None) -> AdbResult:
        """Delete the app's data (save file, PlayerPrefs) and stop it: a first-launch state."""
        return self.run("shell", "pm", "clear", package, serial=serial)

    def resolve_activity(self, package: str, serial: str | None) -> str | None:
        output = self.run(
            "shell", "cmd", "package", "resolve-activity", "--brief", package, serial=serial
        ).stdout
        return parse_resolve_activity(output)

    def am_start(self, component: str, serial: str | None) -> AdbResult:
        """Start an activity and wait (-W) until it reports that it has launched."""
        return self.run(
            "shell", "am", "start", "-W", "-n", component, serial=serial, timeout_s=LAUNCH_TIMEOUT_S
        )

    def ui_dump(self, serial: str | None) -> str:
        """The current screen's accessibility tree (`uiautomator dump`) as XML text."""
        self.run("shell", "uiautomator", "dump", UI_DUMP_PATH, serial=serial)
        return self.run("exec-out", "cat", UI_DUMP_PATH, serial=serial).stdout

    def input_tap(self, x: int, y: int, serial: str | None) -> AdbResult:
        return self.run("shell", "input", "tap", str(x), str(y), serial=serial)


UI_DUMP_PATH = "/sdcard/pg_ui.xml"


def parse_pidof(output: str) -> int | None:
    """`pidof` prints the process id(s); the game runs as a single process."""
    first = output.split()[0] if output.split() else ""
    return int(first) if first.isdigit() else None


def parse_resolve_activity(output: str) -> str | None:
    """The last line of `cmd package resolve-activity --brief` is `<package>/<activity>`."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1] if lines and "/" in lines[-1] else None


class LogcatCapture:
    """Stream `adb logcat` for one process into a file while a spike step runs."""

    def __init__(self, adb: Adb, pid: int, serial: str | None, out_file: Path) -> None:
        self._argv = adb.argv(
            "logcat", "-v", "threadtime", "-T", "1", f"--pid={pid}", serial=serial
        )
        self._out_file = out_file
        self._handle: IO[str] | None = None
        self._process: subprocess.Popen[str] | None = None

    def __enter__(self) -> LogcatCapture:
        self._out_file.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._out_file.open("w", encoding="utf-8")
        self._process = subprocess.Popen(  # noqa: S603 - fixed argv, never a shell
            self._argv, stdout=self._handle, stderr=subprocess.STDOUT, text=True
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        if self._handle is not None:
            self._handle.close()

    def lines(self) -> list[str]:
        return self._out_file.read_text(encoding="utf-8", errors="replace").splitlines()
