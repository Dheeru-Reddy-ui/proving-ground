"""Fresh launch of the game: `pm clear`, start it, and dismiss Android's 16 KB warning.

Android 16 shows the app-compatibility warning after every `pm clear` (clearing data also
resets "Don't show again"), and Unity does not start until it is dismissed. See ADR-0008.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pg_core.android_ui import compat_dialog_ok
from pg_runner.adb import AdbResult

DIALOG_TIMEOUT_S = 6.0
POLL_S = 0.5


class LaunchAdb(Protocol):
    def pm_clear(self, package: str, serial: str | None) -> AdbResult: ...

    def am_start(self, component: str, serial: str | None) -> AdbResult: ...

    def ui_dump(self, serial: str | None) -> str: ...

    def input_tap(self, x: int, y: int, serial: str | None) -> AdbResult: ...


@dataclass(frozen=True)
class LaunchReport:
    pm_clear: str
    am_start: str
    dialog_dismissed: bool
    seconds: dict[str, float]  # pm_clear, launch, dialog


def fresh_launch(
    adb: LaunchAdb,
    package: str,
    activity: str,
    serial: str | None,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    dialog_timeout_s: float = DIALOG_TIMEOUT_S,
) -> LaunchReport:
    """Clear the app's data, launch it, and tap OK on the compatibility warning if it shows."""
    started = clock()
    cleared = adb.pm_clear(package, serial)
    after_clear = clock()
    launched = adb.am_start(activity, serial)
    after_launch = clock()
    dismissed = _dismiss_compat_dialog(adb, serial, clock, sleep, after_launch + dialog_timeout_s)
    after_dialog = clock()

    return LaunchReport(
        pm_clear=cleared.stdout.strip(),
        am_start=launched.stdout,
        dialog_dismissed=dismissed,
        seconds={
            "pm_clear": round(after_clear - started, 2),
            "launch": round(after_launch - after_clear, 2),
            "dialog": round(after_dialog - after_launch, 2),
        },
    )


class RelaunchAdb(LaunchAdb, Protocol):
    def force_stop(self, package: str, serial: str | None) -> AdbResult: ...


RELAUNCH_DIALOG_TIMEOUT_S = 3.0


def relaunch(
    adb: RelaunchAdb,
    package: str,
    activity: str,
    serial: str | None,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    dialog_timeout_s: float = RELAUNCH_DIALOG_TIMEOUT_S,
) -> bool:
    """Stop the app and start it again with its data kept; returns whether the compatibility
    warning had to be dismissed (observed: it does not show after a plain force-stop)."""
    adb.force_stop(package, serial)
    adb.am_start(activity, serial)
    return _dismiss_compat_dialog(adb, serial, clock, sleep, clock() + dialog_timeout_s)


def _dismiss_compat_dialog(
    adb: LaunchAdb,
    serial: str | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    deadline: float,
) -> bool:
    while clock() < deadline:
        point = compat_dialog_ok(adb.ui_dump(serial))
        if point is not None:
            adb.input_tap(point.x, point.y, serial)
            return True
        sleep(POLL_S)
    return False
