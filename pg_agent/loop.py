"""The agent loop (M2.4, ADR-0005): health check, claim, heartbeat, execute, report.

- Health is checked before every claim; `full=True` marks the checks at start-up, while
  unhealthy and after an infra failure. The CLI runs the same device checks for both and never
  probes the app, which is not running between jobs (ADR-0005). An unhealthy agent claims
  nothing and reports why.
- One job at a time (`max_concurrency: 1`).
- Ctrl+C once: finish the current job, then stop. Twice: stop now; the job is reported as failed
  (retryable) so it runs again.
- At start-up it kills test runners orphaned by a previous agent that died: they would hold the
  only driver slot.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import version
from types import FrameType
from typing import Any

import psutil

from pg_agent.client import AgentApi, ApiError, ClaimedJob, LeaseLost, Unreachable
from pg_agent.executor import Executor
from pg_api import logs
from pg_core.gates.base import Outcome
from pg_runner.runner import kill_tree

log = logs.get("pg_agent")
DEVICE_TYPES = ["INSTALL_BUILD", "RUN_TEST"]
HEARTBEAT_S = 30.0
UNHEALTHY_RECHECK_S = 60.0
RUNNER_MARK = "pg_sdk.pytest_plugin"


@dataclass(frozen=True)
class Health:
    healthy: bool
    checks: list[dict[str, Any]]


def orphan_runners() -> list[int]:
    """PIDs of test runners whose parent process is gone."""
    found = []
    for proc in psutil.process_iter(["pid", "ppid", "cmdline"]):
        cmdline = proc.info.get("cmdline") or []
        if RUNNER_MARK in cmdline and not psutil.pid_exists(proc.info.get("ppid") or 0):
            found.append(int(proc.info["pid"]))
    return found


def kill_orphans(find: Callable[[], list[int]] = orphan_runners) -> list[int]:
    pids = find()
    for pid in pids:
        log.warning("killing_orphan_runner", pid=pid)
        kill_tree(pid)
    return pids


class Heartbeat:
    """Keeps a job's lease alive from its own thread while the job runs."""

    def __init__(self, api: AgentApi, job: ClaimedJob, interval_s: float = HEARTBEAT_S) -> None:
        self.api = api
        self.job = job
        self.interval_s = interval_s
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name=f"heartbeat-{job.job_id}", daemon=True
        )

    def _run(self) -> None:
        while not self.stop.wait(self.interval_s):
            try:
                self.api.heartbeat(self.job.job_id, self.job.lease_token)
            except LeaseLost:
                self.lost.set()
                log.warning("lease_lost", job_id=self.job.job_id)
                return
            except (ApiError, Unreachable) as exc:
                log.warning("heartbeat_failed", job_id=self.job.job_id, error=str(exc))

    def __enter__(self) -> Heartbeat:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


@dataclass
class AgentLoop:
    api: AgentApi
    executor: Executor
    health: Callable[[bool], Health]  # full? -> result
    poll_s: float = 5.0
    sleep: Callable[[float], bool] | None = None
    heartbeat_s: float = HEARTBEAT_S
    healthy: bool = False
    stopping: threading.Event = field(default_factory=threading.Event)
    aborting: bool = False
    current: ClaimedJob | None = None
    installed: str | None = None

    def capabilities(self) -> dict[str, Any]:
        return {
            "platform": "android",
            "max_concurrency": 1,
            "agent_version": version("proving-ground"),
        }

    def check(self, full: bool) -> bool:
        result = self.health(full)
        try:
            self.api.status(
                healthy=result.healthy,
                checks=result.checks,
                capabilities=self.capabilities(),
                current_job_id=self.current.job_id if self.current else None,
            )
        except (ApiError, Unreachable) as exc:
            log.warning("status_not_reported", error=str(exc))
        if not result.healthy:
            failing = [c["name"] for c in result.checks if not c.get("ok")]
            log.warning("agent_unhealthy", full=full, failing=failing)
        return result.healthy

    def start(self) -> None:
        kill_orphans()
        self.healthy = self.check(full=True)
        self.installed = self.executor.installed_build() if self.healthy else None

    def run_once(self) -> bool:
        """Check health, claim and run at most one job. True when a job ran."""
        if not self.healthy:
            self.healthy = self.check(full=True)
            if not self.healthy:
                return False
        elif not self.check(full=False):
            self.healthy = False
            return False
        job = self.api.claim(DEVICE_TYPES, self.capabilities(), self.installed)
        if job is None:
            return False
        if self.stopping.is_set():
            self.api.release(job.job_id, job.lease_token)
            return False
        self.current = job
        log.info("job_claimed", job_id=job.job_id, type=job.type, attempts=job.attempts)
        try:
            with Heartbeat(self.api, job, self.heartbeat_s):
                outcome = self.executor.handle(job)
        except KeyboardInterrupt:
            self.executor.report_failure(job, "agent stopped by the operator", retryable=True)
            kill_orphans()
            raise
        finally:
            self.current = None
        self.installed = self.executor.installed_build()
        if outcome is Outcome.INFRA:
            self.healthy = False  # re-checked as a full check before the next claim
        return True

    def run_forever(self) -> None:
        sleep = self.sleep or self.stopping.wait
        previous = signal.getsignal(signal.SIGINT)

        def on_interrupt(_signum: int, _frame: FrameType | None) -> None:
            if self.stopping.is_set():
                raise KeyboardInterrupt
            log.info("agent_stopping", hint="press Ctrl+C again to stop now")
            self.stopping.set()

        signal.signal(signal.SIGINT, on_interrupt)
        try:
            self.start()
            while not self.stopping.is_set():
                try:
                    worked = self.run_once()
                except Unreachable as exc:
                    log.warning("api_unreachable", error=str(exc))
                    worked = False
                if not worked:
                    sleep(self.poll_s if self.healthy else UNHEALTHY_RECHECK_S)
        finally:
            signal.signal(signal.SIGINT, previous)
            self.api.close()
            log.info("agent_stopped")
