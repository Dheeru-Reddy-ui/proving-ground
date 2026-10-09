"""The device agent without a device or a server: client retries, credentials file, executor
paths (install, refuse, run, artifacts) and the health-gated loop."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from pg_agent import config
from pg_agent.client import AgentApi, ApiError, ClaimedJob, LeaseLost, Unreachable
from pg_agent.executor import Executor, artifact_name
from pg_agent.loop import AgentLoop, Health, kill_orphans
from pg_core.gates.base import Outcome
from pg_runner.adb import AdbResult
from pg_runner.runner import Attempt, RunRecord

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SHA = hashlib.sha256((ROOT / "pg_sdk/manifest.json").read_bytes()).hexdigest()
GOOD_CODE = "\n".join(
    line
    for line in (ROOT / "suites/accepted/test_buy_character_unlock_and_select_c13.py")
    .read_text(encoding="utf-8")
    .splitlines()
    if not line.startswith("# ")
)
APK = b"apk-bytes" * 1000
APK_SHA = hashlib.sha256(APK).hexdigest()


# --- client -------------------------------------------------------------------------------


def api_with(
    handler: Callable[[httpx.Request], httpx.Response], **kw: Any
) -> tuple[AgentApi, list[float]]:
    slept: list[float] = []
    api = AgentApi(
        "https://api.example",
        "pgt_secret",
        transport=httpx.MockTransport(handler),
        sleep=slept.append,
        **kw,
    )
    return api, slept


def test_cold_starts_and_5xx_are_retried_with_backoff() -> None:
    answers = iter([httpx.Response(503), httpx.Response(502), httpx.Response(204)])
    api, slept = api_with(lambda request: next(answers))
    assert api.claim(["RUN_TEST"], {}, None) is None
    assert len(slept) == 2
    assert 2.0 <= slept[0] < 4.0
    assert 4.0 <= slept[1] < 6.0


def test_connection_errors_are_retried_until_the_limit() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    api, slept = api_with(down, max_wait_s=20)
    with pytest.raises(Unreachable, match="ConnectError"):
        api.claim(["RUN_TEST"], {}, None)
    assert sum(slept) <= 20


def test_errors_are_typed_and_lease_conflicts_are_lease_lost() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(409, json={"error": {"code": "lease_lost", "message": "gone"}})
        return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "no"}})

    api, _ = api_with(handler)
    with pytest.raises(LeaseLost):
        api.heartbeat(1, "tok")
    with pytest.raises(ApiError) as caught:
        api.code("abc")
    assert (caught.value.status, caught.value.code) == (401, "unauthorized")


def test_the_token_goes_to_the_api_but_never_to_storage_urls(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return (
            httpx.Response(200, content=b"data", json=None)
            if request.method == "GET"
            else httpx.Response(200, json={})
        )

    api, _ = api_with(handler)
    api.release(3, "tok")
    target = tmp_path / "x.bin"
    target.write_bytes(b"x")
    api.upload(
        {"url": "https://storage.example/up?token=t", "method": "PUT", "form_field": "file"}, target
    )
    api.download("https://storage.example/down?token=t", tmp_path / "y.bin")
    assert seen[0].headers["authorization"] == "Bearer pgt_secret"
    assert "authorization" not in seen[1].headers
    assert "authorization" not in seen[2].headers
    assert b'name="file"' in seen[1].content


# --- credentials file ---------------------------------------------------------------------


def test_credentials_round_trip(tmp_path: Path) -> None:
    path = config.save(
        config.AgentConfig(api_url="https://x", name="pc-1", token=SecretStr("pgt_abc")),
        tmp_path / "agent.json",
    )
    loaded = config.load(path)
    assert (loaded.api_url, loaded.name, loaded.token.get_secret_value()) == (
        "https://x",
        "pc-1",
        "pgt_abc",
    )
    assert "pgt_abc" not in repr(loaded)
    with pytest.raises(FileNotFoundError, match="pg agent enroll"):
        config.load(tmp_path / "missing.json")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")
def test_credentials_are_readable_only_by_this_user(tmp_path: Path) -> None:
    path = config.save(
        config.AgentConfig(api_url="https://x", name="pc-1", token=SecretStr("pgt_abc")),
        tmp_path / "agent.json",
    )
    acl = subprocess.run(  # noqa: S603
        ["icacls", str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    entries = [
        line.split(str(path))[-1].strip()
        for line in acl.splitlines()
        if ":" in line and "(" in line
    ]
    assert entries == [f"{config.windows_user()}:(F)"]


# --- executor -----------------------------------------------------------------------------


class FakeApi:
    def __init__(self, code: str = GOOD_CODE, parts: list[bytes] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.code_text = code
        self.parts = parts if parts is not None else [APK[:4000], APK[4000:]]

    def code(self, sha: str) -> str:
        return self.code_text

    def apk(self, sha: str) -> dict[str, Any]:
        return {
            "sha256": sha,
            "size": len(APK),
            "parts": [
                {
                    "index": i,
                    "sha256": hashlib.sha256(p).hexdigest(),
                    "size": len(p),
                    "url": f"u{i}",
                }
                for i, p in enumerate(self.parts)
            ],
        }

    def download(self, url: str, dest: Path) -> None:
        dest.write_bytes(self.parts[int(url[1:])])

    def complete(self, job_id: int, token: str, result: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("complete", result))
        return {"status": "stored"}

    def fail(self, job_id: int, token: str, error: str, *, retryable: bool) -> dict[str, Any]:
        self.calls.append(("fail", (error, retryable)))
        return {}

    def upload_url(self, job_id: int, token: str, attempt: int, name: str) -> dict[str, Any]:
        return {"key": f"artifacts/job-{job_id}/try-1/a{attempt}/{name}", "url": "u"}

    def upload(self, target: dict[str, Any], path: Path) -> None:
        self.calls.append(("upload", target["key"]))


class FakeAdb:
    def __init__(self, installed: str | None = APK_SHA, install_ok: bool = True) -> None:
        self.installed = installed
        self.install_ok = install_ok
        self.installs: list[Path] = []

    def installed_apk_sha256(self, package: str, serial: str | None) -> str | None:
        return self.installed

    def install(self, apk: Path, serial: str | None) -> AdbResult:
        self.installs.append(apk)
        if self.install_ok:
            self.installed = hashlib.sha256(apk.read_bytes()).hexdigest()
            return AdbResult(("adb",), 0, "Performing Streamed Install\nSuccess\n", "")
        return AdbResult(
            ("adb",), 1, "", "adb: failed to install: INSTALL_FAILED_INSUFFICIENT_STORAGE"
        )


def fake_runner(outcomes: list[Outcome]) -> Callable[..., RunRecord]:
    def run(test_file: Path, context: Any, *, timeout_s: float) -> RunRecord:
        attempts = []
        for i, outcome in enumerate(outcomes, 1):
            directory = Path(context.artifact_dir) / f"attempt_{i}"
            (directory / "test_x").mkdir(parents=True)
            (directory / "pytest_output.txt").write_text("output")
            (directory / "test_x" / "failure.png").write_bytes(b"png")
            (directory / "test_x" / "huge.bin").write_bytes(b"0" * (6 * 1024 * 1024))
            attempts.append(
                Attempt(
                    attempt=i,
                    outcome=outcome,
                    rule=f"rule_{outcome.value}",
                    detail="d",
                    duration_s=3.0,
                    wall_s=9.0,
                    exit_code=0,
                    timed_out=False,
                    artifact_dir=str(directory),
                    actions=("open_store",),
                )
            )
        return RunRecord(
            test_file=str(test_file), flags=tuple(context.flags), attempts=tuple(attempts)
        )

    return run


def make_executor(
    tmp_path: Path, api: FakeApi, adb: FakeAdb, outcomes: list[Outcome] | None = None
) -> Executor:
    return Executor(
        api=api,  # type: ignore[arg-type]
        package="com.example",
        serial=None,
        context_args=lambda tag: {
            "locator_tag": tag,
            "package": "com.example",
            "activity": "a/.b",
            "adb_serial": None,
            "alttester_host": "127.0.0.1",
            "alttester_port": 13000,
            "allowlist": [],
        },
        root=ROOT,
        work_dir=tmp_path / "work",
        cache_dir=tmp_path / "cache",
        adb=adb,  # type: ignore[arg-type]
        runner=fake_runner(outcomes or [Outcome.PASSED]),
    )


def job(kind: str = "RUN_TEST", **payload: Any) -> ClaimedJob:
    base = {
        "build_sha": APK_SHA,
        "locator_tag": "87d396162a05",
        "code_sha": hashlib.sha256(GOOD_CODE.encode()).hexdigest(),
        "manifest_sha": MANIFEST_SHA,
        "run_group": "prove-1",
        "candidate_id": 4,
        "flags": [],
        "repeat": 1,
        "timeout_s": 300,
    }
    base.update(payload)
    return ClaimedJob(
        job_id=7,
        type=kind,
        payload=base,
        lease_token="tok",
        attempts=1,
        max_attempts=3,
    )


def test_install_downloads_verifies_and_installs(tmp_path: Path) -> None:
    api, adb = FakeApi(), FakeAdb(installed="0" * 64)
    executor = make_executor(tmp_path, api, adb)
    assert executor.handle(job("INSTALL_BUILD")) is None
    assert adb.installed == APK_SHA
    assert api.calls == [("complete", {"installed_sha256": APK_SHA})]
    executor.handle(job("INSTALL_BUILD"))
    assert len(adb.installs) == 1  # already installed: nothing to do


def test_a_corrupt_part_is_retried_and_a_wrong_apk_is_refused(tmp_path: Path) -> None:
    corrupt = FakeApi()
    corrupt.apk = lambda sha: {"parts": [{"index": 0, "sha256": "1" * 64, "url": "u0"}]}  # type: ignore[method-assign]
    executor = make_executor(tmp_path, corrupt, FakeAdb(installed=None))
    assert executor.handle(job("INSTALL_BUILD")) is Outcome.INFRA
    assert corrupt.calls[0][0] == "fail"
    assert corrupt.calls[0][1][1] is True
    wrong = FakeApi(parts=[b"another apk"])
    executor = make_executor(tmp_path / "2", wrong, FakeAdb(installed=None))
    executor.handle(job("INSTALL_BUILD"))
    assert wrong.calls == [
        (
            "fail",
            ("agent refused the job: the downloaded APK does not match the build's sha256", False),
        )
    ]


def test_a_failed_install_is_retried(tmp_path: Path) -> None:
    api = FakeApi()
    executor = make_executor(tmp_path, api, FakeAdb(installed=None, install_ok=False))
    assert executor.handle(job("INSTALL_BUILD")) is Outcome.INFRA
    error, retryable = api.calls[0][1]
    assert retryable
    assert "INSTALL_FAILED_INSUFFICIENT_STORAGE" in error


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"manifest_sha": "0" * 64}, "another SDK manifest"),
        ({"code_sha": "0" * 64}, "does not match its sha256"),
        ({"locator_tag": "000000000000"}, "no locator map"),
    ],
)
def test_jobs_the_agent_must_not_run_are_refused(
    tmp_path: Path, change: dict[str, Any], reason: str
) -> None:
    api = FakeApi()
    make_executor(tmp_path, api, FakeAdb()).handle(job(**change))
    ((kind, (error, retryable)),) = api.calls
    assert kind == "fail"
    assert not retryable
    assert reason in error


def test_agent_side_g1_refuses_hallucinated_code(tmp_path: Path) -> None:
    bad = GOOD_CODE.replace("game.store.characters.buy(", "game.store.characters.buy_all(")
    api = FakeApi(code=bad)
    executor = make_executor(tmp_path, api, FakeAdb())
    executor.handle(job(code_sha=hashlib.sha256(bad.encode()).hexdigest()))
    ((kind, (error, retryable)),) = api.calls
    assert kind == "fail"
    assert not retryable
    assert "agent-side G1 refused the code (unknown_sdk_member" in error


def test_a_run_reports_every_attempt_with_its_artifacts(tmp_path: Path) -> None:
    api = FakeApi()
    executor = make_executor(tmp_path, api, FakeAdb(), [Outcome.INFRA, Outcome.ASSERTION])
    assert executor.handle(job(flags=["flag_x"])) is Outcome.ASSERTION
    uploads = sorted(c[1] for c in api.calls if c[0] == "upload")
    assert uploads == [  # the 6 MB file is skipped
        "artifacts/job-7/try-1/a1/failure.png",
        "artifacts/job-7/try-1/a1/pytest_output.txt",
        "artifacts/job-7/try-1/a2/failure.png",
        "artifacts/job-7/try-1/a2/pytest_output.txt",
    ]
    (result,) = [c[1] for c in api.calls if c[0] == "complete"]
    assert [a["outcome"] for a in result["attempts"]] == ["infra", "assertion"]
    assert set(result["attempts"][1]["artifacts"]) == {"pytest_output.txt", "failure.png"}


class SlowUploadApi(FakeApi):
    """Uploads take a while; records how many overlap and fails or loses the lease on request."""

    def __init__(self, fail: str | None = None, lose_lease: bool = False) -> None:
        super().__init__()
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()
        self.fail_name = fail
        self.lose_lease = lose_lease

    def upload(self, target: dict[str, Any], path: Path) -> None:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.05)
            if self.lose_lease:
                raise LeaseLost(409, "lease_lost", "the lease expired")
            if self.fail_name and str(target["key"]).endswith(self.fail_name):
                raise Unreachable("storage down")
            super().upload(target, path)
        finally:
            with self.lock:
                self.active -= 1


def test_artifacts_upload_concurrently_and_a_failed_one_loses_only_that_file(
    tmp_path: Path,
) -> None:
    api = SlowUploadApi(fail="failure.png")
    executor = make_executor(tmp_path, api, FakeAdb(), [Outcome.PASSED])
    assert executor.handle(job()) is Outcome.PASSED
    assert api.peak > 1
    (result,) = [c[1] for c in api.calls if c[0] == "complete"]
    assert result["attempts"][0]["artifacts"] == {
        "pytest_output.txt": "artifacts/job-7/try-1/a1/pytest_output.txt"
    }


def test_a_lost_lease_during_an_upload_still_stops_the_job(tmp_path: Path) -> None:
    api = SlowUploadApi(lose_lease=True)
    executor = make_executor(tmp_path, api, FakeAdb(), [Outcome.PASSED])
    executor.handle(job())
    assert not [c for c in api.calls if c[0] == "complete"]


def test_artifact_names_are_safe() -> None:
    assert artifact_name(Path("logcat 0.txt")) == "logcat_0.txt"
    assert artifact_name(Path(".hidden")) == "a.hidden"


# --- loop ---------------------------------------------------------------------------------


class LoopApi:
    def __init__(self, jobs: list[ClaimedJob]) -> None:
        self.jobs = jobs
        self.statuses: list[bool] = []
        self.released: list[int] = []
        self.claims = 0

    def status(self, *, healthy: bool, **_: Any) -> None:
        self.statuses.append(healthy)

    def claim(
        self, types: list[str], capabilities: dict[str, Any], installed: str | None
    ) -> ClaimedJob | None:
        self.claims += 1
        assert capabilities["max_concurrency"] == 1
        return self.jobs.pop(0) if self.jobs else None

    def release(self, job_id: int, token: str) -> None:
        self.released.append(job_id)

    def heartbeat(self, job_id: int, token: str) -> None: ...

    def close(self) -> None: ...


class LoopExecutor:
    def __init__(self, outcome: Outcome | None) -> None:
        self.outcome = outcome
        self.ran: list[int] = []

    def handle(self, job: ClaimedJob) -> Outcome | None:
        self.ran.append(job.job_id)
        return self.outcome

    def installed_build(self) -> str | None:
        return APK_SHA


def loop_with(api: LoopApi, executor: LoopExecutor, health: list[bool]) -> AgentLoop:
    answers = iter(health)
    return AgentLoop(
        api=api,  # type: ignore[arg-type]
        executor=executor,  # type: ignore[arg-type]
        health=lambda full: Health(healthy=next(answers), checks=[{"name": "adb", "ok": True}]),
    )


def test_an_unhealthy_agent_claims_nothing_and_says_so() -> None:
    api = LoopApi([job()])
    loop = loop_with(api, LoopExecutor(Outcome.PASSED), [False, False])
    assert loop.run_once() is False
    assert api.claims == 0
    assert api.statuses == [False]


def test_a_healthy_agent_runs_a_job_and_infra_forces_the_full_doctor() -> None:
    api, executor = LoopApi([job()]), LoopExecutor(Outcome.INFRA)
    loop = loop_with(api, executor, [True, True])
    assert loop.run_once() is True  # full check (unknown health), then claim
    assert executor.ran == [7]
    assert loop.healthy is False  # next turn starts with the full doctor
    assert loop.run_once() is False  # healthy again, but no job left
    assert api.claims == 2


def test_a_stopping_agent_releases_what_it_claimed() -> None:
    api, executor = LoopApi([job()]), LoopExecutor(Outcome.PASSED)
    loop = loop_with(api, executor, [True])
    loop.stopping.set()
    assert loop.run_once() is False
    assert api.released == [7]
    assert executor.ran == []


def test_orphaned_runners_are_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []
    monkeypatch.setattr("pg_agent.loop.kill_tree", killed.append)
    assert kill_orphans(lambda: [101, 202]) == [101, 202]
    assert killed == [101, 202]


def test_claimed_job_payload_round_trip() -> None:
    assert json.loads(job().model_dump_json())["payload"]["run_group"] == "prove-1"


# --- the health check behind `pg agent run` ------------------------------------------------------

FORWARD = "UsbFfs tcp:13000 tcp:13000\n"


def _agent_health(reverse_out: str, restore: Callable[[Any], bool]) -> tuple[Health, list[str]]:
    from pg_cli.agent import health_check
    from pg_cli.settings import Settings
    from tests.unit.test_doctor_cli import FakeAdb, make_deps

    adb = FakeAdb(reverse_out=reverse_out)
    deps, probes = make_deps(adb)
    settings = Settings(_env_file=None, pg_android_package="com.unity.trashdash")  # type: ignore[call-arg]
    return health_check(settings, deps, lambda: restore(adb))(True), probes


def test_agent_health_never_probes_the_app() -> None:
    health, probes = _agent_health(FORWARD, lambda adb: False)
    assert health.healthy
    assert probes == []
    assert {c["name"]: c["status"] for c in health.checks}["app connects"] == "SKIP"


def test_agent_health_restores_a_dropped_reverse_forward_before_checking() -> None:
    def restore(adb: Any) -> bool:
        adb.reverse_out = FORWARD  # what `adb reverse tcp:13000 tcp:13000` does
        return True

    health, _ = _agent_health("", restore)
    assert health.healthy


def test_agent_health_reports_a_forward_that_cannot_be_restored() -> None:
    from pg_runner.adb import AdbError

    def restore(adb: Any) -> bool:
        raise AdbError("could not create adb reverse tcp:13000: no devices")

    health, _ = _agent_health("", restore)
    assert not health.healthy
    assert [c["name"] for c in health.checks if not c["ok"]] == ["reverse forward"]
