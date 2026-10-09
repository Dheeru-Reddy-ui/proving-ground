"""What the agent does with a job (M2.4).

INSTALL_BUILD: download the APK's parts through signed URLs, verify every part and the whole
file against the build's sha256, `adb install -r`, and verify the installed APK's sha256 on the
device.

RUN_TEST: fetch the test code by its sha256 and re-hash it; check that the server planned with
this agent's SDK manifest; re-run G1 locally (defence in depth, CLAUDE.md rule 5); make sure the
right build is installed; run the test through `pg_runner` (sandboxed subprocess, scrubbed env,
timeout); upload each attempt's artifacts; report every attempt. The server decides what an infra
result means (ADR-0006).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pg_agent.client import AgentApi, ApiError, ClaimedJob, LeaseLost, Unreachable
from pg_api import logs
from pg_core.gates.base import Outcome
from pg_core.gates.static import check_static, spec_ids_from_markdown
from pg_core.job_results import AttemptReport, RunTestResult
from pg_runner.adb import Adb, AdbError
from pg_runner.runner import Attempt, RunRecord, make_context, run_test

log = logs.get("pg_agent")
MANIFEST = Path("pg_sdk/manifest.json")
LOCATORS = Path("pg_sdk/locators")
SPECS = Path("specs")
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_ARTIFACTS_PER_ATTEMPT = 20
UPLOAD_WORKERS = 4  # the API client's connection pool is thread-safe (httpcore locks it)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class JobRefused(RuntimeError):
    """A job this agent must not run (wrong code hash, G1 refusal, SDK mismatch): not retried."""


class InstallError(RuntimeError):
    """The build could not be installed or verified on the device: retried."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_name(path: Path) -> str:
    name = _UNSAFE.sub("_", path.name)[:100]
    return name if name[:1].isalnum() else f"a{name}"[:100]


@dataclass
class Executor:
    api: AgentApi
    package: str
    serial: str | None
    context_args: Callable[[str], dict[str, Any]]  # locator tag -> runner context arguments
    root: Path = Path()
    work_dir: Path = Path("artifacts/agent")
    cache_dir: Path = Path("artifacts/agent/apks")
    adb: Adb = field(default_factory=Adb)
    runner: Callable[..., RunRecord] = run_test
    now: Callable[[], datetime] = lambda: datetime.now(UTC)

    # --- builds ---------------------------------------------------------------------------

    def installed_build(self) -> str | None:
        try:
            return self.adb.installed_apk_sha256(self.package, self.serial)
        except AdbError:
            return None

    def fetch_apk(self, sha: str) -> Path:
        """The build's APK in the local cache, downloaded part by part when missing."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{sha}.apk"
        if target.is_file() and sha256_file(target) == sha:
            return target
        info = self.api.apk(sha)
        partial = target.with_suffix(".partial")
        part_file = target.with_suffix(".part")
        with partial.open("wb") as out:
            for part in sorted(info["parts"], key=lambda p: int(p["index"])):
                self.api.download(str(part["url"]), part_file)
                if sha256_file(part_file) != part["sha256"]:
                    raise InstallError(f"APK part {part['index']} does not match its sha256")
                with part_file.open("rb") as handle:
                    shutil.copyfileobj(handle, out)
        part_file.unlink(missing_ok=True)
        if sha256_file(partial) != sha:
            partial.unlink(missing_ok=True)
            raise JobRefused("the downloaded APK does not match the build's sha256")
        partial.replace(target)
        return target

    def ensure_build(self, sha: str) -> None:
        if self.installed_build() == sha:
            return
        apk = self.fetch_apk(sha)
        log.info("installing_build", sha=sha[:12])
        result = self.adb.install(apk, self.serial)
        if result.returncode != 0 or "Success" not in result.stdout:
            tail = (result.stdout + result.stderr).strip()[-300:]
            raise InstallError(f"adb install failed: {tail}")
        installed = self.installed_build()
        if installed != sha:
            raise InstallError(
                f"after install the device has {str(installed)[:12]}, not {sha[:12]}"
            )

    def check_locators(self, tag: str) -> None:
        if not (self.root / LOCATORS / f"{tag}.yaml").is_file():
            raise JobRefused(f"this agent has no locator map {tag}; update its checkout")

    # --- jobs -----------------------------------------------------------------------------

    def handle(self, job: ClaimedJob) -> Outcome | None:
        """Run one job and report it. Returns the RUN_TEST outcome (None for INSTALL_BUILD)."""
        try:
            if job.type == "INSTALL_BUILD":
                self.install_job(job)
                return None
            if job.type == "RUN_TEST":
                return self.run_job(job)
            raise JobRefused(f"this agent does not run {job.type} jobs")
        except LeaseLost:
            log.warning("lease_lost_result_dropped", job_id=job.job_id)
        except JobRefused as exc:
            log.warning("job_refused", job_id=job.job_id, reason=str(exc))
            self.report_failure(job, f"agent refused the job: {exc}", retryable=False)
        except (InstallError, AdbError, ApiError, Unreachable, OSError) as exc:
            log.warning("job_failed", job_id=job.job_id, error=str(exc))
            self.report_failure(job, f"{type(exc).__name__}: {exc}", retryable=True)
            return Outcome.INFRA
        return None

    def report_failure(self, job: ClaimedJob, error: str, *, retryable: bool) -> None:
        try:
            self.api.fail(job.job_id, job.lease_token, error, retryable=retryable)
        except (ApiError, Unreachable) as exc:  # the lease will expire and the job be retried
            log.warning("fail_not_reported", job_id=job.job_id, error=str(exc))

    def install_job(self, job: ClaimedJob) -> None:
        sha = str(job.payload["build_sha"])
        self.check_locators(str(job.payload["locator_tag"]))
        self.ensure_build(sha)
        self.api.complete(job.job_id, job.lease_token, {"installed_sha256": sha})
        log.info("build_installed", sha=sha[:12])

    def manifest_sha(self) -> str:
        return hashlib.sha256((self.root / MANIFEST).read_bytes()).hexdigest()

    def run_job(self, job: ClaimedJob) -> Outcome:
        p = job.payload
        if p["manifest_sha"] != self.manifest_sha():
            raise JobRefused("the server planned this job with another SDK manifest")
        code = self.api.code(str(p["code_sha"]))
        if hashlib.sha256(code.encode("utf-8")).hexdigest() != p["code_sha"]:
            raise JobRefused("the test code does not match its sha256")
        manifest = json.loads((self.root / MANIFEST).read_text(encoding="utf-8"))
        spec_ids = spec_ids_from_markdown(
            f.read_text(encoding="utf-8") for f in (self.root / SPECS).glob("*.md")
        )
        g1, _ = check_static(code, manifest, spec_ids)
        if not g1.passed:
            codes = ", ".join(r.code for r in g1.reasons)
            raise JobRefused(f"agent-side G1 refused the code ({codes})")
        self.check_locators(str(p["locator_tag"]))
        self.ensure_build(str(p["build_sha"]))
        run_dir = self.work_dir / f"job-{job.job_id}-try-{job.attempts}"
        run_dir.mkdir(parents=True, exist_ok=True)
        test_file = run_dir / f"test_c{p['candidate_id']}.py"
        test_file.write_text(code, encoding="utf-8")
        context = make_context(
            run_id=str(p["run_group"]),
            flags=list(p.get("flags") or []),
            artifact_dir=run_dir / "artifacts",
            **self.context_args(str(p["locator_tag"])),
        )
        started = self.now()
        record = self.runner(test_file, context, timeout_s=float(p["timeout_s"]))
        finished = self.now()
        reports = [self.report(job, a) for a in record.attempts]
        result = RunTestResult(attempts=tuple(reports), started_at=started, finished_at=finished)
        self.api.complete(job.job_id, job.lease_token, result.model_dump(mode="json"))
        final = record.final
        log.info("test_run", job_id=job.job_id, outcome=final.outcome.value, rule=final.rule)
        return final.outcome

    def report(self, job: ClaimedJob, attempt: Attempt) -> AttemptReport:
        return AttemptReport(
            attempt=attempt.attempt,
            outcome=attempt.outcome,
            rule=attempt.rule[:64],
            detail=attempt.detail[-4000:],
            duration_s=attempt.duration_s,
            wall_s=attempt.wall_s,
            exit_code=attempt.exit_code,
            timed_out=attempt.timed_out,
            game_errors=tuple(e[:2000] for e in attempt.game_errors[:50]),
            pgtelem_lines=len(attempt.pgtelem),
            actions=tuple(attempt.actions[:500]),
            artifacts=self.upload_artifacts(job, attempt),
        )

    def upload_artifacts(self, job: ClaimedJob, attempt: Attempt) -> dict[str, str]:
        """Upload an attempt's files (screenshots, logs, JUnit, results), a few at a time: each
        file costs a signing call to the API and a transfer to storage, and one after another
        they took several seconds per run. A failed upload loses that file, not the run."""
        directory = Path(attempt.artifact_dir)
        files = sorted(f for f in directory.rglob("*") if f.is_file()) if directory.is_dir() else []
        chosen: dict[str, Path] = {}
        for path in files:
            if len(chosen) >= MAX_ARTIFACTS_PER_ATTEMPT:
                break
            name = artifact_name(path)
            if name not in chosen and path.stat().st_size <= MAX_ARTIFACT_BYTES:
                chosen[name] = path

        def upload(name: str, path: Path) -> str | None:
            try:
                target = self.api.upload_url(job.job_id, job.lease_token, attempt.attempt, name)
                self.api.upload(target, path)
            except LeaseLost:
                raise
            except (ApiError, Unreachable, OSError) as exc:
                log.warning("artifact_not_uploaded", name=name, error=str(exc))
                return None
            return str(target["key"])

        with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
            futures = {name: pool.submit(upload, name, path) for name, path in chosen.items()}
        return {name: key for name, f in futures.items() if (key := f.result()) is not None}
