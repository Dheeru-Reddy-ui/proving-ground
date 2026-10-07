"""Errors a test can see from `pg_sdk`.

How the runner reads a failing test (G3 kills, failure classification):
- `AssertionError` from the test, `PGTimeout`, and a game error logged during the test
  (`PGGameError`) are product-visible failures; they count as kills on a bug build.
- `PGInfraError` means the device chain broke (driver disconnected, app not running); it is
  never a kill and the runner retries it.
- Any other exception is a fault in the test itself.
"""

from __future__ import annotations


class PGError(Exception):
    """Base class of every error raised by pg_sdk."""


class PGTimeout(PGError):
    """A post-condition did not hold in time (screen not shown, item not listed, ...)."""

    def __init__(self, what: str, timeout_s: float, screen: str) -> None:
        super().__init__(f"timed out after {timeout_s:g}s waiting for {what} (screen: {screen})")
        self.what = what
        self.timeout_s = timeout_s
        self.screen = screen


class PGGameError(PGError):
    """The game logged an error or exception while the test ran (raised by the pytest plugin)."""


class PGInfraError(PGError):
    """The device chain failed: driver connection, adb, or the app not running. Never a kill."""
