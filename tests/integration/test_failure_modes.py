"""M2.7 failure modes that need real processes or real logs.

- An agent process killed in the middle of a job: its lease expires, another agent runs the job,
  and exactly one completion is stored.
- Forged and stale webhooks: 401, and the rejection is logged with its reason.

(The same webhook twice, the LLM error storm and lease expiry inside one process are in
test_api.py, test_worker.py and test_pipeline.py; a database outage is a scripted drill,
ops/drills/db_outage.py; an unplugged phone is a live drill, docs/evidence/phase2.)
"""

from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psutil
import pytest
import uvicorn
from sqlalchemy import Engine, delete, select

from pg_agent.client import AgentApi
from pg_agent.executor import Executor
from pg_agent.loop import AgentLoop, Health
from pg_api.app import create_app
from pg_api.auth import create_enrolment_token
from pg_api.settings import ApiSettings
from pg_core.gates.base import Outcome
from pg_core.jobs import JobSpec, JobType
from pg_db import jobs as queue
from pg_db import repo
from pg_db.models import (
    Agent,
    ApiToken,
    Build,
    Candidate,
    EnrolmentToken,
    Execution,
    GenerationRun,
    Job,
    WebhookDelivery,
)
from pg_db.session import session_scope
from pg_runner.runner import Attempt, RunRecord
from tests.integration.fakes import GOOD, ROOT

SHA = "a1" * 32
LEASE_TTL_S = 10


@pytest.fixture
def server(database_url: str, tmp_path: Path) -> Iterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    settings = ApiSettings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=database_url,
        pg_session_secret="f" * 40,
        pg_secure_cookies=False,
        pg_local_storage_dir=tmp_path / "storage",
        pg_public_base_url=base,
        pg_lease_ttl_s=LEASE_TTL_S,
        pg_webhook_secret="failure-mode-secret",
    )
    srv = uvicorn.Server(uvicorn.Config(create_app(settings, root=ROOT), log_level="warning"))
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield base
    srv.should_exit = True
    thread.join(timeout=10)


def one_run_test_job(engine: Engine) -> int:
    """A build, a candidate and one queued RUN_TEST job for it."""
    with session_scope(engine) as s:
        for table in (
            Execution,
            Job,
            Candidate,
            GenerationRun,
            WebhookDelivery,
            ApiToken,
            EnrolmentToken,
            Agent,
            Build,
        ):
            s.execute(delete(table))
        build, _ = repo.register_build(s, SHA, "failure modes", "87d396162a05")
        run = repo.create_generation_run(
            s,
            build_id=build.id,
            feature="store",
            provider="fake",
            model="fake",
            prompt_version="v2",
            prompt_hash="h",
            n_requested=1,
        )
        cand, _ = repo.add_candidate(
            s, run.id, "test_buy_character_unlock_and_select", "", GOOD, ["STORE-5"]
        )
        manifest_sha = hashlib.sha256((ROOT / "pg_sdk/manifest.json").read_bytes()).hexdigest()
        queue.enqueue(
            s,
            [
                JobSpec(
                    type=JobType.RUN_TEST,
                    idempotency_key=f"run:prove-{run.id}:c{cand.id}:clean:r1",
                    payload={
                        "run_group": f"prove-{run.id}",
                        "candidate_id": cand.id,
                        "code_sha": cand.code_sha,
                        "flags": [],
                        "purpose": "clean",
                        "repeat": 1,
                        "build_sha": SHA,
                        "locator_tag": "87d396162a05",
                        "manifest_sha": manifest_sha,
                        "timeout_s": 300,
                    },
                    requires={"platform": "android"},
                )
            ],
            validation_id=None,
            now=datetime.now(UTC),
        )
        return int(s.execute(select(Job.id)).scalar_one())


def enrol(server: str, engine: Engine, name: str) -> str:
    with session_scope(engine) as s:
        token = create_enrolment_token(s, datetime.now(UTC), timedelta(hours=1))
    answer = AgentApi(server, None).register(token, name, {"platform": "android"})
    return str(answer["token"])


AGENT_SCRIPT = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from pg_agent.client import AgentApi
    from pg_agent.executor import Executor
    from pg_agent.loop import AgentLoop, Health

    class Phone:
        def installed_apk_sha256(self, package, serial):
            return sys.argv[3]

    def slow_runner(test_file, context, *, timeout_s):
        Path(sys.argv[4]).write_text("running")
        time.sleep(600)  # the test is still running when the process is killed

    api = AgentApi(sys.argv[1], sys.argv[2])
    executor = Executor(
        api=api, package="com.example", serial=None,
        context_args=lambda tag: {
            "locator_tag": tag, "package": "com.example", "activity": "a/.b", "adb_serial": None,
            "alttester_host": "127.0.0.1", "alttester_port": 13000, "allowlist": [],
        },
        root=Path(sys.argv[5]), work_dir=Path(sys.argv[6]), cache_dir=Path(sys.argv[6]),
        adb=Phone(), runner=slow_runner,
    )
    loop = AgentLoop(
        api=api, executor=executor, health=lambda full: Health(True, []), heartbeat_s=2
    )
    loop.healthy = True
    while not loop.run_once():
        time.sleep(0.5)
    """
)


def test_a_killed_agent_process_leaves_exactly_one_completion(
    server: str, engine: Engine, tmp_path: Path
) -> None:
    job_id = one_run_test_job(engine)
    first_token = enrol(server, engine, "agent-killed")
    script = tmp_path / "agent.py"
    script.write_text(AGENT_SCRIPT, encoding="utf-8")
    started = tmp_path / "started.flag"
    proc = subprocess.Popen(  # noqa: S603 - our own script
        [
            sys.executable,
            str(script),
            server,
            first_token,
            SHA,
            str(started),
            str(ROOT),
            str(tmp_path / "w1"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(200):
            if started.exists():
                break
            assert proc.poll() is None, proc.stderr.read().decode() if proc.stderr else ""
            time.sleep(0.1)
        assert started.exists(), "the agent never started the test"
        with session_scope(engine) as s:
            leased = s.get_one(Job, job_id)
            assert (leased.status, leased.lease_owner, leased.attempts) == (
                "leased",
                "agent-killed",
                1,
            )
            killed_token = leased.lease_token
        time.sleep(3)  # its heartbeat thread is keeping the lease alive
        with session_scope(engine) as s:
            assert s.get_one(Job, job_id).lease_expires_at > datetime.now(UTC)
    finally:
        psutil.Process(proc.pid).kill()  # no shutdown, no release: the process just dies
        proc.wait(timeout=10)
    time.sleep(LEASE_TTL_S + 2)  # the lease runs out
    second = AgentApi(server, enrol(server, engine, "agent-second"))

    def fast_runner(test_file: Path, context: Any, *, timeout_s: float) -> RunRecord:
        attempt = Attempt(
            attempt=1,
            outcome=Outcome.PASSED,
            rule="plugin_passed",
            duration_s=3.0,
            wall_s=8.0,
            exit_code=0,
            timed_out=False,
            artifact_dir=str(tmp_path / "none"),
        )
        return RunRecord(test_file=str(test_file), flags=(), attempts=(attempt,))

    class Phone:
        def installed_apk_sha256(self, package: str, serial: str | None) -> str:
            return SHA

    executor = Executor(
        api=second,
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
        work_dir=tmp_path / "w2",
        cache_dir=tmp_path / "w2",
        adb=Phone(),  # type: ignore[arg-type]
        runner=fast_runner,
    )
    loop = AgentLoop(api=second, executor=executor, health=lambda full: Health(True, []))
    loop.healthy = True
    assert loop.run_once() is True  # the claim reaps the expired lease, then takes the job
    with session_scope(engine) as s:
        job = s.get_one(Job, job_id)
        assert (job.status, job.lease_owner, job.attempts) == ("succeeded", "agent-second", 2)
        assert s.query(Execution).filter(Execution.job_id == job_id).count() == 1
    late = httpx.post(
        f"{server}/v1/jobs/{job_id}/heartbeat",
        json={"lease_token": killed_token},
        headers={"Authorization": f"Bearer {first_token}"},
    )
    assert late.status_code in (403, 409)  # the dead agent's lease is gone for good
    second.close()


def test_forged_and_stale_webhooks_are_refused_and_logged(
    server: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from pg_core.webhook import signature

    body = b'{"sha256": "' + b"b" * 64 + b'", "label": "x", "locator_tag": "87d396162a05"}'
    now = str(int(time.time()))
    forged = httpx.post(
        f"{server}/v1/webhooks/github",
        content=body,
        headers={
            "X-PG-Timestamp": now,
            "X-PG-Signature": signature("guess", now, body),
            "X-PG-Delivery": "forged-0001",
        },
    )
    old = str(int(time.time()) - 3600)
    stale = httpx.post(
        f"{server}/v1/webhooks/github",
        content=body,
        headers={
            "X-PG-Timestamp": old,
            "X-PG-Signature": signature("failure-mode-secret", old, body),
            "X-PG-Delivery": "stale-0001",
        },
    )
    assert (forged.status_code, stale.status_code) == (401, 401)
    logged = capsys.readouterr().out
    assert "webhook_rejected" in logged
    assert "bad_signature" in logged
    assert "stale_timestamp" in logged
