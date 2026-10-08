"""Run one test file against one run context in a sandboxed subprocess (M1.6).

Generated code is untrusted (CLAUDE.md rule 5). It runs only after G1 passed, and here:
- in a fresh temp working directory holding nothing but the test file and the run context;
- in a scrubbed environment: an allow-list of OS variables, never API keys or DATABASE_URL;
- under a wall-clock timeout that kills the whole process tree.

Each attempt is classified by `pg_core.classify`; infra attempts are retried up to
`max_retries` times with exponential backoff and jitter, and every attempt is recorded.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import psutil
from defusedxml import ElementTree
from pydantic import BaseModel, ConfigDict, Field

from pg_core.classify import Classification, Observation, backoff_s, classify
from pg_core.gates.base import Outcome
from pg_sdk.pytest_plugin import CONTEXT_ENV, RESULTS_FILE, RunContext

DEFAULT_TIMEOUT_S = 300.0
MAX_RETRIES = 2
BACKOFF_BASE_S = 5.0
# Copied into the child when present; everything else (keys, DATABASE_URL, PG_*) is dropped.
ENV_ALLOWLIST = (
    "PATH",
    "SYSTEMROOT",  # Windows sockets fail without it
    "SYSTEMDRIVE",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",  # adb looks for its key under the user profile
    "HOME",
    "LOCALAPPDATA",
    "PATHEXT",
    "COMSPEC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)
PGTELEM_PREFIX = "PGTELEM "


class Attempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempt: int
    outcome: Outcome
    rule: str
    detail: str = ""
    duration_s: float  # the test body (pytest "call" phase); 0 when it never ran
    wall_s: float
    exit_code: int | None
    timed_out: bool
    artifact_dir: str
    game_errors: tuple[str, ...] = ()
    pgtelem: tuple[dict[str, Any], ...] = ()
    actions: tuple[str, ...] = ()


class RunRecord(BaseModel):
    """Every attempt of one test execution; the last one is the result."""

    model_config = ConfigDict(frozen=True)

    test_file: str
    flags: tuple[str, ...]
    attempts: tuple[Attempt, ...] = Field(min_length=1)

    @property
    def final(self) -> Attempt:
        return self.attempts[-1]


def scrubbed_env(source: Mapping[str, str], context_path: Path) -> dict[str, str]:
    env = {key: source[key] for key in ENV_ALLOWLIST if key in source}
    env[CONTEXT_ENV] = str(context_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def kill_tree(pid: int) -> None:
    """Kill a process and all its children (pytest, adb logcat, ...)."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    processes = [*parent.children(recursive=True), parent]
    for process in processes:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.kill()
    psutil.wait_procs(processes, timeout=10)


def junit_outcomes(path: Path) -> dict[str, str]:
    """Test name -> passed/failed/error/skipped from a pytest JUnit XML file."""
    if not path.exists():
        return {}
    outcomes: dict[str, str] = {}
    root = ElementTree.parse(str(path)).getroot()
    if root is None:
        return outcomes
    for case in root.iter("testcase"):
        name = case.get("name", "")
        if case.find("failure") is not None:
            outcomes[name] = "failed"
        elif case.find("error") is not None:
            outcomes[name] = "error"
        elif case.find("skipped") is not None:
            outcomes[name] = "skipped"
        else:
            outcomes[name] = "passed"
    return outcomes


def read_pgtelem(game_log: Path) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    if not game_log.exists():
        return lines
    for raw in game_log.read_text(encoding="utf-8").splitlines():
        try:
            message = str(json.loads(raw).get("message", ""))
        except json.JSONDecodeError:
            continue
        if message.startswith(PGTELEM_PREFIX):
            try:
                lines.append(json.loads(message[len(PGTELEM_PREFIX) :]))
            except json.JSONDecodeError:
                continue
    return lines


def _attempt(
    test_file: Path,
    context: RunContext,
    attempt_no: int,
    timeout_s: float,
    clock: Callable[[], float],
    select: str | None,
) -> Attempt:
    artifact_dir = context.artifact_dir
    artifact_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="pg_run_"))
    try:
        target = workdir / f"test_{test_file.stem.removeprefix('test_')}.py"
        shutil.copyfile(test_file, target)
        context_path = workdir / "run_context.json"
        context_path.write_text(context.model_dump_json(indent=2), encoding="utf-8")
        junit = artifact_dir / "junit.xml"
        argv = [
            sys.executable,
            "-m",
            "pytest",
            target.name if select is None else f"{target.name}::{select}",
            "-p",
            "pg_sdk.pytest_plugin",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-q",
            "--rootdir",
            str(workdir),
            f"--junitxml={junit}",
        ]
        started = clock()
        output_path = artifact_dir / "pytest_output.txt"
        with output_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, never a shell
                argv,
                cwd=workdir,
                env=scrubbed_env(os.environ, context_path),
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                exit_code: int | None = process.wait(timeout=timeout_s)
                timed_out = False
            except subprocess.TimeoutExpired:
                kill_tree(process.pid)
                process.wait(timeout=30)
                exit_code, timed_out = None, True
        wall_s = round(clock() - started, 3)
        output_tail = output_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    results_path = artifact_dir / RESULTS_FILE
    results: dict[str, Any] = (
        json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {}
    )
    entry = next(
        (v for k, v in results.items() if select is None or k.endswith(f"::{select}")), None
    )
    junit_map = junit_outcomes(artifact_dir / "junit.xml")
    verdict: Classification = classify(
        Observation(
            exit_code=exit_code,
            timed_out=timed_out,
            junit_outcome=junit_map.get(select) if select else next(iter(junit_map.values()), None),
            plugin_entry=entry,
            output_tail=output_tail,
        )
    )
    call = (entry or {}).get("phases", {}).get("call", {})
    test_dir = next((p for p in artifact_dir.iterdir() if p.is_dir()), artifact_dir)
    return Attempt(
        attempt=attempt_no,
        outcome=verdict.outcome,
        rule=verdict.rule,
        detail=verdict.detail,
        duration_s=float(call.get("duration_s", 0.0)),
        wall_s=wall_s,
        exit_code=exit_code,
        timed_out=timed_out,
        artifact_dir=str(artifact_dir),
        game_errors=tuple((entry or {}).get("game_errors", [])),
        pgtelem=tuple(read_pgtelem(test_dir / "game_log.jsonl")),
        actions=tuple((entry or {}).get("actions", [])),
    )


def run_test(
    test_file: Path,
    context: RunContext,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = MAX_RETRIES,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    select: str | None = None,
) -> RunRecord:
    """Execute one test once (the file's only test, or the function named `select`),
    retrying infra failures."""
    if not test_file.is_file():
        raise FileNotFoundError(test_file)
    rng = rng or random.Random()  # noqa: S311 - jitter, not security
    attempts: list[Attempt] = []
    for number in range(1, max_retries + 2):
        attempt_context = context.model_copy(
            update={"artifact_dir": context.artifact_dir / f"attempt_{number}"}
        )
        attempt = _attempt(test_file, attempt_context, number, timeout_s, clock, select)
        attempts.append(attempt)
        if attempt.outcome is not Outcome.INFRA or number > max_retries:
            break
        sleep(backoff_s(number, BACKOFF_BASE_S, rng.uniform(0, BACKOFF_BASE_S)))
    return RunRecord(test_file=str(test_file), flags=context.flags, attempts=tuple(attempts))


def make_context(
    *,
    run_id: str,
    locator_tag: str,
    flags: Sequence[str],
    artifact_dir: Path,
    package: str,
    activity: str,
    adb_serial: str | None,
    alttester_host: str,
    alttester_port: int,
    allowlist: Sequence[str] = (),
) -> RunContext:
    return RunContext(
        run_id=run_id,
        locator_tag=locator_tag,
        flags=tuple(sorted(set(flags))),
        artifact_dir=artifact_dir.resolve(),
        package=package,
        activity=activity,
        adb_serial=adb_serial,
        alttester_host=alttester_host,
        alttester_port=alttester_port,
        game_error_allowlist=tuple(allowlist),
    )
