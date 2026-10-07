"""Rules for `pg doctor`: turn raw observations of the device chain into PASS/FAIL results.

Adapters collect the observations (adb output, a socket probe, a driver call). This module only
interprets them, so every rule is unit-tested without a phone attached.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

REQUIRED_PYTHON = (3, 12)

_SEMVER = re.compile(r"\b(\d+\.\d+\.\d+)\b")

FIX_CONNECT_PHONE = (
    "Connect the phone with a USB data cable, turn on USB debugging and accept the "
    "'Allow USB debugging' prompt on the phone"
)


class Status(StrEnum):
    PASS = "PASS"  # noqa: S105 - a check status, not a password
    FAIL = "FAIL"
    SKIP = "SKIP"


class CheckResult(BaseModel):
    """The outcome of one check, with a fix hint whenever it failed."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: Status
    detail: str
    fix: str | None = None


class DoctorReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    results: tuple[CheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(result.status is not Status.FAIL for result in self.results)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


class AdbDevice(BaseModel):
    """One line of `adb devices -l`."""

    model_config = ConfigDict(frozen=True)

    serial: str
    state: str
    model: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "device"


class AppProbeError(StrEnum):
    NO_APP = "no_app"
    SLOT_BUSY = "slot_busy"
    TIMEOUT = "timeout"
    CONNECTION = "connection"


class AppProbe(BaseModel):
    """What happened when a driver tried to reach the instrumented app."""

    model_config = ConfigDict(frozen=True)

    scene: str | None = None
    loaded_scenes: tuple[str, ...] = ()
    error: AppProbeError | None = None
    message: str | None = None


def passed(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status=Status.PASS, detail=detail)


def failed(name: str, detail: str, fix: str) -> CheckResult:
    return CheckResult(name=name, status=Status.FAIL, detail=detail, fix=fix)


def skipped(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status=Status.SKIP, detail=f"skipped: {reason}")


def mask_serial(serial: str) -> str:
    """Shorten a device serial for output that may end up in public evidence files.

    ASCII only: Windows consoles that are not in UTF-8 mode garble characters such as an ellipsis.
    """
    return serial if len(serial) <= 6 else f"{serial[:4]}..."


def check_python(version: tuple[int, int, int]) -> CheckResult:
    shown = ".".join(str(part) for part in version)
    if version[:2] == REQUIRED_PYTHON:
        return passed("python", f"Python {shown}")
    return failed(
        "python",
        f"Python {shown}; this project needs {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}",
        "Run commands through `uv run`, which uses the Python pinned in .python-version",
    )


def check_adb(path: str | None, version_output: str) -> CheckResult:
    if path is None:
        return failed(
            "adb",
            "adb was not found on PATH",
            "Install Android platform-tools (winget install Google.PlatformTools), "
            "then open a new terminal",
        )
    version = next(
        (line.strip() for line in version_output.splitlines() if line.startswith("Version")),
        "version unknown",
    )
    return passed("adb", f"{path} ({version})")


def parse_adb_devices(output: str) -> tuple[AdbDevice, ...]:
    devices: list[AdbDevice] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith(("*", "List of devices")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), None)
        devices.append(AdbDevice(serial=parts[0], state=parts[1], model=model))
    return tuple(devices)


def _device_state(device: AdbDevice) -> CheckResult:
    shown = f"{device.model or 'unknown model'} ({mask_serial(device.serial)})"
    if device.ready:
        return passed("adb device", shown)
    if device.state == "unauthorized":
        return failed(
            "adb device",
            f"{shown} is unauthorized",
            "Unlock the phone and accept 'Allow USB debugging' "
            "(tick 'Always allow from this computer')",
        )
    return failed(
        "adb device",
        f"{shown} is {device.state}",
        "Reconnect the USB cable; if it persists run `adb kill-server` then `adb start-server`",
    )


def check_device(
    devices: tuple[AdbDevice, ...], wanted_serial: str | None
) -> tuple[CheckResult, AdbDevice | None]:
    """Pick the test device: the one named by PG_ADB_SERIAL, or the only one attached."""
    if wanted_serial:
        match = next((d for d in devices if d.serial == wanted_serial), None)
        if match is None:
            attached = ", ".join(mask_serial(d.serial) for d in devices) or "none"
            return (
                failed(
                    "adb device",
                    f"PG_ADB_SERIAL ({mask_serial(wanted_serial)}) is not attached "
                    f"(attached: {attached})",
                    FIX_CONNECT_PHONE,
                ),
                None,
            )
        result = _device_state(match)
        return result, match if match.ready else None
    if not devices:
        return failed("adb device", "no device attached", FIX_CONNECT_PHONE), None
    if len(devices) > 1:
        attached = ", ".join(mask_serial(d.serial) for d in devices)
        return (
            failed(
                "adb device",
                f"{len(devices)} devices attached ({attached})",
                "Set PG_ADB_SERIAL in .env to the test phone's serial (see `adb devices -l`)",
            ),
            None,
        )
    only = devices[0]
    return _device_state(only), only if only.ready else None


def check_package(package: str | None, pm_path_output: str) -> CheckResult:
    if not package:
        return failed(
            "game installed",
            "PG_ANDROID_PACKAGE is not set",
            "Set PG_ANDROID_PACKAGE in .env (see .env.example)",
        )
    apks = [line for line in pm_path_output.splitlines() if line.startswith("package:")]
    if apks:
        return passed("game installed", f"{package} ({len(apks)} APK file(s) on the device)")
    return failed(
        "game installed",
        f"{package} is not installed on the device",
        "Build and install the instrumented APK: game/BUILD_TRASHCAT.md steps 6 and 7",
    )


def parse_reverse_list(output: str) -> tuple[tuple[str, str], ...]:
    """Parse `adb reverse --list` lines such as `UsbFfs tcp:13000 tcp:13000`."""
    rules: list[tuple[str, str]] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-2].startswith("tcp:") and parts[-1].startswith("tcp:"):
            rules.append((parts[-2], parts[-1]))
    return tuple(rules)


def check_reverse(rules: tuple[tuple[str, str], ...], port: int, serial: str | None) -> CheckResult:
    wanted = (f"tcp:{port}", f"tcp:{port}")
    if wanted in rules:
        return passed("reverse forward", f"device tcp:{port} -> this PC tcp:{port}")
    target = f"-s {serial} " if serial else ""
    return failed(
        "reverse forward",
        f"no adb reverse forward for tcp:{port}",
        f"Run: adb {target}reverse tcp:{port} tcp:{port}",
    )


def check_server(host: str, port: int, error: str | None) -> CheckResult:
    if error is None:
        return passed("AltTester Desktop", f"server accepting connections on {host}:{port}")
    return failed(
        "AltTester Desktop",
        f"cannot reach {host}:{port} ({error})",
        f"Start AltTester Desktop and switch its built-in server ON (port {port})",
    )


_APP_FIXES = {
    AppProbeError.NO_APP: (
        "Launch TrashCat on the phone and check the reverse forward; the green AltTester "
        "popup disappears once the app is connected"
    ),
    AppProbeError.SLOT_BUSY: (
        "Another driver holds the connection (the free plan allows one): close AltTester "
        "Desktop's inspector or any other test session"
    ),
    AppProbeError.TIMEOUT: (
        "The app did not answer in time: keep it in the foreground with the screen on"
    ),
    AppProbeError.CONNECTION: (
        "Check that AltTester Desktop's server is ON and that the APK was built with the "
        "AltTester Editor (a development build)"
    ),
}


def check_app(probe: AppProbe) -> CheckResult:
    if probe.error is not None:
        return failed(
            "app connects",
            f"{probe.error.value}: {probe.message or 'no details'}",
            _APP_FIXES[probe.error],
        )
    if not probe.scene:
        return failed(
            "app connects",
            "connected, but the app reported no current scene",
            _APP_FIXES[AppProbeError.CONNECTION],
        )
    loaded = ", ".join(probe.loaded_scenes) or "none reported"
    return passed("app connects", f"current scene: {probe.scene} (loaded: {loaded})")


def pinned_version(versions_md: str, component: str) -> str | None:
    """Read a pinned version from the table row `| <component> | <version> ...` in VERSIONS.md."""
    prefix = f"| {component} |"
    for line in versions_md.splitlines():
        if line.startswith(prefix):
            match = _SEMVER.search(line[len(prefix) :])
            return match.group(1) if match else None
    return None


def check_driver_version(installed: str | None, pinned: str | None) -> CheckResult:
    if pinned is None:
        return failed(
            "driver version",
            "docs/VERSIONS.md has no pinned AltTester Python driver version",
            "Restore the 'AltTester Python driver' row in docs/VERSIONS.md",
        )
    if installed is None:
        return failed("driver version", "AltTester-Driver is not installed", "Run: uv sync")
    if installed == pinned:
        return passed("driver version", f"AltTester-Driver {installed} (pinned {pinned})")
    return failed(
        "driver version",
        f"installed {installed}, but docs/VERSIONS.md pins {pinned}",
        "Run `uv sync`. Change the pin only together with the SDK and Desktop versions",
    )
