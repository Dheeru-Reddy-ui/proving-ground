"""Thin, typed wrapper around the adb command line: fixed argument lists, no shell, timeouts."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

DEFAULT_TIMEOUT_S = 15.0


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

    def run(
        self, *args: str, serial: str | None = None, timeout_s: float | None = None
    ) -> AdbResult:
        if self.path is None:
            raise AdbError("adb was not found on PATH")
        argv = [self.path, *(["-s", serial] if serial else []), *args]
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
