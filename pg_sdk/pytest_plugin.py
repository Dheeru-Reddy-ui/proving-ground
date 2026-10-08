"""pytest plugin that runs a test against the phone: `pytest -p pg_sdk.pytest_plugin`.

The runner passes everything through one file named by the env var `PG_RUN_CONTEXT` (build,
bug flags, run id, artifact dir, device). For each test the `game` fixture:

1. clears the app's data (`pm clear`), launches it and dismisses Android's 16 KB warning;
2. connects the driver, streams the game's log notifications and a logcat slice to files;
3. turns on the run's bug flags with `PGBugFlags.Configure` and checks them with `Active()`;
4. presses START and hands the test a `Game` on the main menu.

Afterwards it screenshots a failure, fails a passing test whose game logged an error that is
not allow-listed (`PGGameError`), disconnects, and records the outcome in
`<artifact_dir>/pg_results.json`. Tests never learn which flags are active.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from pg_runner.adb import Adb, AdbError, LogcatCapture
from pg_runner.launch import fresh_launch, relaunch
from pg_sdk._driver import AltTesterSession, LogLine, connect
from pg_sdk._locators import Locators
from pg_sdk._ui import ASSEMBLY, Driver, Ui
from pg_sdk.errors import PGGameError, PGInfraError, PGTimeout
from pg_sdk.game import Game, enter_main_menu

CONTEXT_ENV = "PG_RUN_CONTEXT"
RESULTS_FILE = "pg_results.json"
GAME_ERROR_LEVELS = frozenset({4, 5})  # AltLogLevel.Error (Unity Error and Exception), Fatal
STASH_SESSION = pytest.StashKey["_AppSession"]()


class RunContext(BaseModel):
    """What the runner tells the plugin. Holds no secrets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    locator_tag: str
    flags: tuple[str, ...] = ()
    artifact_dir: Path
    package: str
    activity: str
    adb_serial: str | None = None
    alttester_host: str = "127.0.0.1"
    alttester_port: int = 13000
    connect_timeout_s: float = 60.0
    fail_on_game_errors: bool = True
    # Regexes of game error messages known on the clean build; matches are recorded, not failed.
    game_error_allowlist: tuple[str, ...] = Field(default=())


def load_context() -> RunContext:
    path = os.environ.get(CONTEXT_ENV)
    if not path:
        raise pytest.UsageError(f"{CONTEXT_ENV} is not set; pg_sdk tests run through pg_runner")
    return RunContext.model_validate_json(Path(path).read_text(encoding="utf-8"))


@dataclass
class _AppSession:
    """One test's connection to the app, plus everything recorded about it."""

    ctx: RunContext
    out_dir: Path
    adb: Adb = field(default_factory=Adb)
    session: AltTesterSession | None = None
    logs: list[LogLine] = field(default_factory=list)
    flags_active: str | None = None
    flags_supported: bool = True
    launch: dict[str, Any] = field(default_factory=dict)
    ui: Ui | None = None
    _logcat: list[LogcatCapture] = field(default_factory=list)

    # --- lifecycle ------------------------------------------------------------------------

    def start(self) -> Game:
        try:
            recreated = self.adb.ensure_reverse(self.ctx.alttester_port, self.ctx.adb_serial)
        except AdbError as exc:
            raise PGInfraError(str(exc)) from exc
        report = fresh_launch(
            self.adb,
            self.ctx.package,
            self.ctx.activity,
            self.ctx.adb_serial,
            clock=time.monotonic,
            sleep=time.sleep,
        )
        self.launch = {
            "seconds": report.seconds,
            "dialog_dismissed": report.dialog_dismissed,
            "reverse_recreated": recreated,
        }
        self._connect()
        self._configure_flags()
        self.ui = Ui(
            driver=self._session(),
            locators=Locators.load(self.ctx.locator_tag),
            clock=time.monotonic,
            sleep=time.sleep,
        )
        enter_main_menu(self.ui, self.ctx.connect_timeout_s)
        return Game(self.ui, self)

    def restart(self) -> Driver:
        """AppControl: close and reopen the app with its data kept; a fresh connection."""
        self._disconnect()
        try:
            self.adb.ensure_reverse(self.ctx.alttester_port, self.ctx.adb_serial)
        except AdbError as exc:
            raise PGInfraError(str(exc)) from exc
        relaunch(
            self.adb,
            self.ctx.package,
            self.ctx.activity,
            self.ctx.adb_serial,
            clock=time.monotonic,
            sleep=time.sleep,
        )
        self._connect()
        return self._session()

    def close(self) -> None:
        self._disconnect()

    # --- recording ------------------------------------------------------------------------

    def game_errors(self) -> tuple[list[str], list[str]]:
        """(errors that fail the test, allow-listed errors) logged by the game so far."""
        patterns = [re.compile(p) for p in self.ctx.game_error_allowlist]
        failing: list[str] = []
        allowed: list[str] = []
        for line in self.logs:
            if _level(line.level) not in GAME_ERROR_LEVELS:
                continue
            text = line.message.strip()
            (allowed if any(p.search(text) for p in patterns) else failing).append(text)
        return failing, allowed

    def screenshot(self, name: str) -> str | None:
        if self.session is None:
            return None
        path = self.out_dir / name
        try:
            self.session.screenshot(path)
        except Exception:  # a failed screenshot must not hide the test's own failure
            return None
        return str(path)

    # --- helpers --------------------------------------------------------------------------

    def _session(self) -> AltTesterSession:
        if self.session is None:
            raise PGInfraError("no driver connection")
        return self.session

    def _connect(self) -> None:
        from alttester import exceptions as alt

        try:
            self.session = connect(
                self.ctx.alttester_host, self.ctx.alttester_port, self.ctx.connect_timeout_s
            )
        except (alt.ConnectionError, OSError) as exc:
            raise PGInfraError(
                f"could not connect to the app: {type(exc).__name__}: {exc}"
            ) from exc
        self.session.add_log_listener(self._on_log)
        self._start_logcat()

    def _disconnect(self) -> None:
        for capture in self._logcat:
            capture.__exit__(None, None, None)
        self._logcat.clear()
        if self.session is not None:
            with contextlib.suppress(Exception):  # best effort while tearing down
                self.session.remove_log_listener()
            try:
                self.session.close()
            finally:
                self.session = None

    def _on_log(self, line: LogLine) -> None:
        self.logs.append(line)
        with (self.out_dir / "game_log.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line.model_dump_json() + "\n")

    def _start_logcat(self) -> None:
        try:
            pid = self.adb.pidof(self.ctx.package, self.ctx.adb_serial)
        except AdbError:
            pid = None
        if pid is None:
            return
        index = len(list(self.out_dir.glob("logcat*.txt")))
        capture = LogcatCapture(
            self.adb, pid, self.ctx.adb_serial, self.out_dir / f"logcat_{index}.txt"
        )
        capture.__enter__()
        self._logcat.append(capture)

    def _configure_flags(self) -> None:
        session = self._session()
        wanted = ",".join(sorted(set(self.ctx.flags)))
        try:
            session.call_static_method("PGBugFlags", "Configure", ASSEMBLY, [wanted])
            self.flags_active = str(
                session.call_static_method("PGBugFlags", "Active", ASSEMBLY, [])
            )
        except PGInfraError:
            raise
        except Exception as exc:
            if wanted:
                raise PGInfraError(f"this build cannot switch bug flags: {exc}") from exc
            self.flags_supported = False  # a build without hooks; fine for a clean run
            return
        if self.flags_active != wanted:
            raise PGInfraError(
                f"bug flags not applied: wanted {wanted!r}, game has {self.flags_active!r}"
            )


def _level(level: int | str | None) -> int | None:
    if isinstance(level, int):
        return level
    if isinstance(level, str) and level.isdigit():
        return int(level)
    return None


# --- pytest hooks -------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "spec(*ids): spec statements this test checks")


@pytest.fixture
def game(request: pytest.FixtureRequest) -> Iterator[Game]:
    """The game on its main menu, fresh from a data reset, with the run's bug flags applied."""
    ctx = load_context()
    out_dir = ctx.artifact_dir / _safe_name(request.node.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    app = _AppSession(ctx=ctx, out_dir=out_dir)
    request.node.stash[STASH_SESSION] = app
    try:
        yield app.start()
    finally:
        app.close()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    app = item.stash.get(STASH_SESSION, None)
    if app is None:
        return report
    if report.when == "call":
        failing, _ = app.game_errors()
        if report.passed and failing and app.ctx.fail_on_game_errors:
            report.outcome = "failed"
            report.longrepr = (
                f"{PGGameError.__name__}: the game logged {len(failing)} error(s):\n"
                + ("\n".join(failing))
            )
        if report.failed:
            app.screenshot("failure.png")
    _record(item, report, call, app)
    return report


def _failure_kind(report: pytest.TestReport, call: pytest.CallInfo[None]) -> str | None:
    if report.passed or report.skipped:
        return None
    if report.when != "call":
        return "infra"  # the app never reached the main menu, or teardown broke the chain
    excinfo = call.excinfo
    if excinfo is None:
        return "game_error"  # passed, then failed by the game-error check above
    if excinfo.errisinstance(PGInfraError):
        return "infra"
    if excinfo.errisinstance(PGTimeout):
        return "pg_timeout"
    if excinfo.errisinstance(PGGameError):
        return "game_error"
    # A failed expectation is an assertion whether it came from `assert`, from
    # `pytest.raises` ("DID NOT RAISE") or from `pytest.fail` (ADR-0004, approved 2026-10-08).
    if excinfo.errisinstance((AssertionError, pytest.fail.Exception)):
        return "assertion"
    return "test_error"


def _record(
    item: pytest.Item, report: pytest.TestReport, call: pytest.CallInfo[None], app: _AppSession
) -> None:
    path = app.ctx.artifact_dir / RESULTS_FILE
    results: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    entry = results.setdefault(item.nodeid, {"phases": {}})
    failing, allowed = app.game_errors()
    entry["phases"][report.when] = {
        "outcome": report.outcome,
        "duration_s": round(report.duration, 3),
        "failure_kind": _failure_kind(report, call),
        "message": None if report.passed else str(report.longrepr)[-4000:],
    }
    entry.update(
        {
            "run_id": app.ctx.run_id,
            "spec_ids": [str(a) for m in item.iter_markers("spec") for a in m.args],
            "flags_supported": app.flags_supported,
            "flags_active": app.flags_active,  # recorded for the runner, never shown to tests
            "launch": app.launch,
            "game_errors": failing,
            "allowed_game_errors": allowed,
            "actions": list(app.ui.actions) if app.ui is not None else [],
        }
    )
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:120]
