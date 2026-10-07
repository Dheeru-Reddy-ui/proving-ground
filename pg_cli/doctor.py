"""`pg doctor`: check every link of the device chain and say how to fix whatever is broken.

Chain: Python -> adb -> phone -> installed game -> adb reverse -> AltTester Desktop -> app ->
pinned driver version. A check whose prerequisite failed is skipped rather than run.
"""

from __future__ import annotations

import importlib.metadata
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pg_cli.settings import Settings
from pg_core.doctor import (
    AdbDevice,
    AppProbe,
    CheckResult,
    DoctorReport,
    Status,
    check_adb,
    check_app,
    check_device,
    check_driver_version,
    check_package,
    check_python,
    check_reverse,
    check_server,
    failed,
    parse_adb_devices,
    parse_reverse_list,
    pinned_version,
    skipped,
)
from pg_runner.adb import Adb, AdbError

VERSIONS_MD = Path("docs/VERSIONS.md")
DRIVER_DIST = "AltTester-Driver"
DRIVER_ROW = "AltTester Python driver"
TCP_TIMEOUT_S = 3.0
APP_TIMEOUT_S = 20.0


class AdbLike(Protocol):
    @property
    def path(self) -> str | None: ...

    def version(self) -> str: ...

    def devices(self) -> str: ...

    def pm_path(self, package: str, serial: str | None) -> str: ...

    def reverse_list(self, serial: str | None) -> str: ...


@dataclass(frozen=True)
class DoctorDeps:
    adb: AdbLike
    probe_tcp: Callable[[str, int, float], str | None]
    probe_app: Callable[[str, int, float], AppProbe]
    driver_version: Callable[[], str | None]
    versions_md: Callable[[], str]
    python_version: tuple[int, int, int]


def tcp_probe(host: str, port: int, timeout_s: float) -> str | None:
    """Return None if host:port accepts a TCP connection, otherwise the error."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return None
    except OSError as exc:
        return str(exc) or type(exc).__name__


def installed_driver_version() -> str | None:
    try:
        return importlib.metadata.version(DRIVER_DIST)
    except importlib.metadata.PackageNotFoundError:
        return None


def read_versions_md() -> str:
    return VERSIONS_MD.read_text(encoding="utf-8") if VERSIONS_MD.exists() else ""


def default_deps() -> DoctorDeps:
    from pg_sdk._driver import probe_app

    major, minor, micro = sys.version_info[:3]
    return DoctorDeps(
        adb=Adb(),
        probe_tcp=tcp_probe,
        probe_app=probe_app,
        driver_version=installed_driver_version,
        versions_md=read_versions_md,
        python_version=(major, minor, micro),
    )


def _adb_failure(name: str, exc: AdbError) -> CheckResult:
    return failed(
        name, str(exc), "Check the USB connection; `adb devices -l` should list the phone"
    )


def run_doctor(settings: Settings, deps: DoctorDeps) -> DoctorReport:
    host, port = settings.pg_alttester_host, settings.pg_alttester_port
    results: list[CheckResult] = [check_python(deps.python_version)]

    try:
        adb_result = check_adb(deps.adb.path, deps.adb.version() if deps.adb.path else "")
    except AdbError as exc:
        adb_result = _adb_failure("adb", exc)
    results.append(adb_result)

    device: AdbDevice | None = None
    if adb_result.status is Status.PASS:
        try:
            device_result, device = check_device(
                parse_adb_devices(deps.adb.devices()), settings.pg_adb_serial
            )
        except AdbError as exc:
            device_result = _adb_failure("adb device", exc)
        results.append(device_result)
    else:
        results.append(skipped("adb device", "adb is not available"))

    if device is not None:
        try:
            package_result = check_package(
                settings.pg_android_package,
                deps.adb.pm_path(settings.pg_android_package, device.serial)
                if settings.pg_android_package
                else "",
            )
        except AdbError as exc:
            package_result = _adb_failure("game installed", exc)
        try:
            reverse_result = check_reverse(
                parse_reverse_list(deps.adb.reverse_list(device.serial)), port, device.serial
            )
        except AdbError as exc:
            reverse_result = _adb_failure("reverse forward", exc)
    else:
        package_result = skipped("game installed", "no usable adb device")
        reverse_result = skipped("reverse forward", "no usable adb device")
    results += [package_result, reverse_result]

    server_result = check_server(host, port, deps.probe_tcp(host, port, TCP_TIMEOUT_S))
    results.append(server_result)

    prerequisites = (package_result, reverse_result, server_result)
    blockers = [r.name for r in prerequisites if r.status is not Status.PASS]
    if blockers:
        results.append(skipped("app connects", f"needs {', '.join(blockers)}"))
    else:
        results.append(check_app(deps.probe_app(host, port, APP_TIMEOUT_S)))

    results.append(
        check_driver_version(deps.driver_version(), pinned_version(deps.versions_md(), DRIVER_ROW))
    )
    return DoctorReport(results=tuple(results))


def render(report: DoctorReport) -> str:
    width = max(len(result.name) for result in report.results)
    lines = [f"pg doctor: {len(report.results)} checks", ""]
    for result in report.results:
        lines.append(f"{result.status.value:<4}  {result.name:<{width}}  {result.detail}")
        if result.fix:
            lines.append(f"{'':<4}  {'':<{width}}  fix: {result.fix}")
    counts = {status: sum(r.status is status for r in report.results) for status in Status}
    summary = (
        f"{counts[Status.PASS]} passed, {counts[Status.FAIL]} failed, {counts[Status.SKIP]} skipped"
    )
    return "\n".join([*lines, "", summary])
