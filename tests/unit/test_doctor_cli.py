"""Unit tests for `pg doctor` orchestration, rendering and the CLI command (no device needed)."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pg_cli import doctor as doctor_mod
from pg_cli.doctor import DoctorDeps, render, run_doctor, tcp_probe
from pg_cli.main import app
from pg_cli.settings import Settings
from pg_core.doctor import AppProbe, AppProbeError, CheckResult, Status
from pg_runner.adb import AdbError

VERSIONS = "| AltTester Python driver | 2.3.2 (`AltTester-Driver` on PyPI) | uv.lock |\n"


@dataclass
class FakeAdb:
    path: str | None = "C:/platform-tools/adb.exe"
    devices_out: str = (
        "List of devices attached\nSERIAL123456 device product:p model:SM_S948B transport_id:1\n"
    )
    pm_out: str = "package:/data/app/x/base.apk\n"
    reverse_out: str = "UsbFfs tcp:13000 tcp:13000\n"
    fail_on: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail_on:
            raise AdbError(f"{name} failed")

    def version(self) -> str:
        self._maybe_fail("version")
        return "Android Debug Bridge version 1.0.41\nVersion 37.0.1-15733141\n"

    def devices(self) -> str:
        self._maybe_fail("devices")
        return self.devices_out

    def pm_path(self, package: str, serial: str | None) -> str:
        self._maybe_fail("pm_path")
        return self.pm_out

    def reverse_list(self, serial: str | None) -> str:
        self._maybe_fail("reverse_list")
        return self.reverse_out


def make_deps(
    adb: FakeAdb | None = None,
    tcp_error: str | None = None,
    probe: AppProbe | None = None,
    driver: str | None = "2.3.2",
) -> tuple[DoctorDeps, list[str]]:
    probes: list[str] = []

    def probe_app(host: str, port: int, timeout_s: float) -> AppProbe:
        probes.append(f"{host}:{port}")
        return probe or AppProbe(scene="Start", loaded_scenes=("Start",))

    deps = DoctorDeps(
        adb=adb or FakeAdb(),
        probe_tcp=lambda host, port, timeout_s: tcp_error,
        probe_app=probe_app,
        driver_version=lambda: driver,
        versions_md=lambda: VERSIONS,
        python_version=(3, 12, 5),
    )
    return deps, probes


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"pg_android_package": "com.unity.trashdash"}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def statuses(results: tuple[CheckResult, ...]) -> dict[str, Status]:
    return {r.name: r.status for r in results}


def test_everything_healthy_passes_all_eight_checks() -> None:
    deps, probes = make_deps()
    report = run_doctor(settings(), deps)
    assert [r.name for r in report.results] == [
        "python",
        "adb",
        "adb device",
        "game installed",
        "reverse forward",
        "AltTester Desktop",
        "app connects",
        "driver version",
    ]
    assert all(r.status is Status.PASS for r in report.results)
    assert report.exit_code == 0
    assert probes == ["127.0.0.1:13000"]


def test_missing_adb_skips_device_checks() -> None:
    deps, probes = make_deps(adb=FakeAdb(path=None))
    report = run_doctor(settings(), deps)
    got = statuses(report.results)
    assert got["adb"] is Status.FAIL
    assert got["adb device"] is Status.SKIP
    assert got["game installed"] is Status.SKIP
    assert got["app connects"] is Status.SKIP
    assert probes == []
    assert report.exit_code == 1


def test_game_not_installed_skips_the_app_probe() -> None:
    deps, probes = make_deps(adb=FakeAdb(pm_out=""))
    report = run_doctor(settings(), deps)
    got = statuses(report.results)
    assert got["game installed"] is Status.FAIL
    assert got["app connects"] is Status.SKIP
    assert probes == []


def test_desktop_down_skips_the_app_probe() -> None:
    deps, probes = make_deps(tcp_error="connection refused")
    report = run_doctor(settings(), deps)
    assert statuses(report.results)["AltTester Desktop"] is Status.FAIL
    assert probes == []


def test_app_probe_failure_is_reported() -> None:
    deps, _ = make_deps(probe=AppProbe(error=AppProbeError.SLOT_BUSY, message="4009"))
    report = run_doctor(settings(), deps)
    assert statuses(report.results)["app connects"] is Status.FAIL


@pytest.mark.parametrize("failing", ["version", "devices", "pm_path", "reverse_list"])
def test_adb_errors_become_failures_not_crashes(failing: str) -> None:
    deps, _ = make_deps(adb=FakeAdb(fail_on={failing}))
    report = run_doctor(settings(), deps)
    assert report.exit_code == 1
    assert any(f"{failing} failed" in r.detail for r in report.results)


def test_unset_package_fails_without_calling_pm() -> None:
    adb = FakeAdb()
    deps, _ = make_deps(adb=adb)
    report = run_doctor(settings(pg_android_package=None), deps)
    assert statuses(report.results)["game installed"] is Status.FAIL
    assert "pm_path" not in adb.calls


def test_driver_version_mismatch_fails() -> None:
    deps, _ = make_deps(driver="2.3.3")
    assert statuses(run_doctor(settings(), deps).results)["driver version"] is Status.FAIL


def test_render_shows_fix_lines_and_summary() -> None:
    deps, _ = make_deps(adb=FakeAdb(reverse_out=""))
    text = render(run_doctor(settings(), deps))
    assert "FAIL  reverse forward" in text
    assert "fix: Run: adb -s SERIAL123456 reverse tcp:13000 tcp:13000" in text
    assert text.splitlines()[-1] == "6 passed, 1 failed, 1 skipped"


def test_tcp_probe_against_real_sockets() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert tcp_probe("127.0.0.1", port, 1.0) is None
    assert tcp_probe("127.0.0.1", port, 1.0) is not None  # closed now


def test_installed_driver_version_matches_lockfile_pin() -> None:
    assert doctor_mod.installed_driver_version() == "2.3.2"


def test_cli_doctor_exit_code_and_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("PG_ANDROID_PACKAGE", "com.unity.trashdash")
    deps, _ = make_deps(tcp_error="refused")
    monkeypatch.setattr(doctor_mod, "default_deps", lambda: deps)
    runner = CliRunner()

    plain = runner.invoke(app, ["doctor"])
    assert plain.exit_code == 1
    assert "FAIL  AltTester Desktop" in plain.output

    as_json = runner.invoke(app, ["doctor", "--json"])
    assert as_json.exit_code == 1
    assert '"status": "FAIL"' in as_json.output
