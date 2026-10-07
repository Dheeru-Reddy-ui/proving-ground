"""Unit tests for the pure `pg doctor` rules in pg_core.doctor."""

from pathlib import Path

import pytest

from pg_core.doctor import (
    AdbDevice,
    AppProbe,
    AppProbeError,
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
    mask_serial,
    parse_adb_devices,
    parse_reverse_list,
    pinned_version,
)

# Real `adb devices -l` output from the project phone, serial replaced.
REAL_DEVICES = (
    "List of devices attached\n"
    "ABCD1234XYZ            device product:m3qxins model:SM_S948B device:m3q transport_id:1\n"
    "\n"
)


def test_python_312_passes_any_patch() -> None:
    assert check_python((3, 12, 5)).status is Status.PASS


@pytest.mark.parametrize("version", [(3, 11, 9), (3, 13, 0), (2, 7, 18)])
def test_python_other_versions_fail_with_fix(version: tuple[int, int, int]) -> None:
    result = check_python(version)
    assert result.status is Status.FAIL
    assert result.fix


def test_adb_missing_fails() -> None:
    result = check_adb(None, "")
    assert result.status is Status.FAIL
    assert "platform-tools" in (result.fix or "")


def test_adb_present_reports_version_line() -> None:
    output = "Android Debug Bridge version 1.0.41\nVersion 37.0.1-15733141\nInstalled as x\n"
    result = check_adb("C:/adb.exe", output)
    assert result.status is Status.PASS
    assert "Version 37.0.1-15733141" in result.detail


def test_adb_present_with_unknown_version_still_passes() -> None:
    assert "version unknown" in check_adb("adb", "garbage").detail


def test_parse_real_devices_output() -> None:
    (device,) = parse_adb_devices(REAL_DEVICES)
    assert device == AdbDevice(serial="ABCD1234XYZ", state="device", model="SM_S948B")
    assert device.ready


def test_parse_devices_ignores_daemon_noise_and_blank_lines() -> None:
    output = (
        "* daemon not running; starting now at tcp:5037\n"
        "* daemon started successfully\n"
        "List of devices attached\n\n"
    )
    assert parse_adb_devices(output) == ()


def test_parse_devices_keeps_unauthorized_and_offline() -> None:
    output = "List of devices attached\nAAA unauthorized usb:1-1 transport_id:2\nBBB offline\n"
    devices = parse_adb_devices(output)
    assert [(d.serial, d.state, d.ready) for d in devices] == [
        ("AAA", "unauthorized", False),
        ("BBB", "offline", False),
    ]


def test_parse_devices_skips_malformed_lines() -> None:
    assert parse_adb_devices("List of devices attached\njustoneword\n") == ()


@pytest.mark.parametrize(
    ("serial", "shown"), [("RZGL12AFGGY", "RZGL..."), ("short", "short"), ("abcdef", "abcdef")]
)
def test_mask_serial(serial: str, shown: str) -> None:
    assert mask_serial(serial) == shown


def test_single_ready_device_is_selected_without_serial() -> None:
    result, device = check_device(parse_adb_devices(REAL_DEVICES), None)
    assert result.status is Status.PASS
    assert device is not None
    assert "SM_S948B" in result.detail
    assert "ABCD1234XYZ" not in result.detail  # serials are masked in output


def test_no_device_fails() -> None:
    result, device = check_device((), None)
    assert result.status is Status.FAIL
    assert device is None


def test_multiple_devices_without_serial_fail_and_ask_for_serial() -> None:
    devices = (AdbDevice(serial="AAA", state="device"), AdbDevice(serial="BBB", state="device"))
    result, device = check_device(devices, None)
    assert result.status is Status.FAIL
    assert "PG_ADB_SERIAL" in (result.fix or "")
    assert device is None


def test_wanted_serial_selects_that_device_among_several() -> None:
    devices = (AdbDevice(serial="AAA", state="device"), AdbDevice(serial="BBB", state="device"))
    result, device = check_device(devices, "BBB")
    assert result.status is Status.PASS
    assert device is not None
    assert device.serial == "BBB"


def test_wanted_serial_not_attached_fails() -> None:
    result, device = check_device((AdbDevice(serial="AAA", state="device"),), "ZZZZZZZZ")
    assert result.status is Status.FAIL
    assert device is None


def test_unauthorized_device_fails_with_prompt_hint() -> None:
    result, device = check_device((AdbDevice(serial="AAA", state="unauthorized"),), "AAA")
    assert result.status is Status.FAIL
    assert "Allow USB debugging" in (result.fix or "")
    assert device is None


def test_offline_single_device_fails() -> None:
    result, device = check_device((AdbDevice(serial="AAA", state="offline"),), None)
    assert result.status is Status.FAIL
    assert "offline" in result.detail
    assert device is None


def test_package_not_configured_fails() -> None:
    assert check_package(None, "").status is Status.FAIL


def test_package_installed_counts_split_apks() -> None:
    output = "package:/data/app/x/base.apk\npackage:/data/app/x/split_config.arm64_v8a.apk\n"
    result = check_package("com.unity.trashdash", output)
    assert result.status is Status.PASS
    assert "2 APK" in result.detail


def test_package_missing_points_to_build_checklist() -> None:
    result = check_package("com.unity.trashdash", "")
    assert result.status is Status.FAIL
    assert "BUILD_TRASHCAT.md" in (result.fix or "")


def test_parse_reverse_list() -> None:
    output = "UsbFfs tcp:13000 tcp:13000\nhost-19 tcp:5555 tcp:6666\nnoise\n"
    assert parse_reverse_list(output) == (("tcp:13000", "tcp:13000"), ("tcp:5555", "tcp:6666"))


def test_reverse_present_passes() -> None:
    rules = parse_reverse_list("UsbFfs tcp:13000 tcp:13000\n")
    assert check_reverse(rules, 13000, "AAA").status is Status.PASS


@pytest.mark.parametrize(
    "listing",
    [
        "",  # nothing forwarded
        "UsbFfs tcp:13000 tcp:13001\n",  # forwarded to the wrong PC port
        "UsbFfs tcp:13001 tcp:13000\n",  # app port differs
        "UsbFfs localabstract:x tcp:13000\n",  # not a tcp mapping
    ],
)
def test_reverse_missing_or_wrong_fails_with_command(listing: str) -> None:
    result = check_reverse(parse_reverse_list(listing), 13000, "AAA")
    assert result.status is Status.FAIL
    assert result.fix == "Run: adb -s AAA reverse tcp:13000 tcp:13000"


def test_reverse_hint_without_serial() -> None:
    assert check_reverse((), 13000, None).fix == "Run: adb reverse tcp:13000 tcp:13000"


def test_server_reachable_and_unreachable() -> None:
    assert check_server("127.0.0.1", 13000, None).status is Status.PASS
    down = check_server("127.0.0.1", 13000, "connection refused")
    assert down.status is Status.FAIL
    assert "built-in server ON" in (down.fix or "")


def test_app_connected_reports_scenes() -> None:
    result = check_app(AppProbe(scene="Main", loaded_scenes=("Main", "Shop")))
    assert result.status is Status.PASS
    assert result.detail == "current scene: Main (loaded: Main, Shop)"


def test_app_connected_without_loaded_scene_list() -> None:
    assert "none reported" in check_app(AppProbe(scene="Start")).detail


@pytest.mark.parametrize("error", list(AppProbeError))
def test_every_app_error_fails_with_its_own_fix(error: AppProbeError) -> None:
    result = check_app(AppProbe(error=error, message="boom"))
    assert result.status is Status.FAIL
    assert result.fix
    assert error.value in result.detail


def test_app_without_scene_is_not_a_pass() -> None:
    assert check_app(AppProbe()).status is Status.FAIL


def test_pinned_driver_version_read_from_real_versions_md() -> None:
    text = Path("docs/VERSIONS.md").read_text(encoding="utf-8")
    assert pinned_version(text, "AltTester Python driver") == "2.3.2"


@pytest.mark.parametrize(
    "text",
    ["", "| Something else | 1.2.3 |", "| AltTester Python driver | not pinned yet |"],
)
def test_pinned_version_missing(text: str) -> None:
    assert pinned_version(text, "AltTester Python driver") is None


def test_driver_version_match_passes() -> None:
    assert check_driver_version("2.3.2", "2.3.2").status is Status.PASS


@pytest.mark.parametrize(
    ("installed", "pinned"), [("2.3.3", "2.3.2"), (None, "2.3.2"), ("2.3.2", None)]
)
def test_driver_version_problems_fail(installed: str | None, pinned: str | None) -> None:
    result = check_driver_version(installed, pinned)
    assert result.status is Status.FAIL
    assert result.fix


def test_report_ok_ignores_skips_but_not_failures() -> None:
    passing = CheckResult(name="a", status=Status.PASS, detail="")
    skipping = CheckResult(name="b", status=Status.SKIP, detail="")
    failing = CheckResult(name="c", status=Status.FAIL, detail="", fix="x")
    assert DoctorReport(results=(passing, skipping)).exit_code == 0
    assert DoctorReport(results=(passing, failing)).exit_code == 1
