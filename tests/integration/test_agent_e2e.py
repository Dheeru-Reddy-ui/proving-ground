"""A build validation across real processes boundaries, without a phone (M2.4).

A real API serves HTTP on a local port (uvicorn in a thread). The real worker generates (fake
LLM) and scores. The real agent loop and executor enrol over HTTP, download the APK's parts through
signed storage URLs, "install" them on a fake phone, run tests through a fake runner, upload
artifacts and complete their jobs. Only the device and the LLM are fakes.
"""

from __future__ import annotations

import hashlib
import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from sqlalchemy import Engine, delete, select

from pg_agent.client import AgentApi
from pg_agent.executor import Executor
from pg_agent.loop import AgentLoop, Health
from pg_api.app import create_app
from pg_api.auth import create_admin_token, create_enrolment_token
from pg_api.settings import ApiSettings
from pg_core.builds import part_key, split_sizes
from pg_core.gates.base import Outcome
from pg_db.models import (
    Agent,
    ApiToken,
    Build,
    Candidate,
    EnrolmentToken,
    Execution,
    GenerationRun,
    Job,
    Kill,
    LlmCall,
    Validation,
    WebhookDelivery,
    Worker,
)
from pg_db.session import session_scope
from pg_runner.adb import AdbResult
from pg_runner.runner import Attempt, RunRecord
from pg_worker.handlers import WorkerContext
from pg_worker.loop import WorkerLoop
from pg_worker.settings import WorkerSettings
from tests.integration.fakes import CODES, ROOT, FakeLLM, reply

APK = bytes(range(256)) * 400  # 100 KiB
APK_SHA = hashlib.sha256(APK).hexdigest()


@pytest.fixture(autouse=True)
def empty(engine: Engine) -> None:
    with session_scope(engine) as s:
        for table in (
            LlmCall,
            Kill,
            Execution,
            Job,
            Candidate,
            GenerationRun,
            Validation,
            WebhookDelivery,
            ApiToken,
            EnrolmentToken,
            Agent,
            Worker,
            Build,
        ):
            s.execute(delete(table))


@pytest.fixture
def server(database_url: str, tmp_path: Path) -> Iterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    settings = ApiSettings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=database_url,
        pg_session_secret="s" * 40,
        pg_secure_cookies=False,
        pg_local_storage_dir=tmp_path / "storage",
        pg_public_base_url=base,
        pg_lease_ttl_s=120,
    )
    app = create_app(settings, root=ROOT)
    config = uvicorn.Config(app, log_level="warning", access_log=False, lifespan="on")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield base
    srv.should_exit = True
    thread.join(timeout=10)


class FakePhone:
    """adb for a phone that installs whatever it is given."""

    def __init__(self) -> None:
        self.installed: str | None = None
        self.installs = 0

    def installed_apk_sha256(self, package: str, serial: str | None) -> str | None:
        return self.installed

    def install(self, apk: Path, serial: str | None) -> AdbResult:
        self.installs += 1
        self.installed = hashlib.sha256(apk.read_bytes()).hexdigest()
        return AdbResult(("adb",), 0, "Success\n", "")


def test_a_validation_runs_through_the_api_worker_and_agent(
    server: str, engine: Engine, database_url: str, tmp_path: Path
) -> None:
    with session_scope(engine) as s:
        admin = {"Authorization": f"Bearer {create_admin_token(s, 'cli')}"}
        enrolment = create_enrolment_token(s, datetime.now(UTC), timedelta(hours=1))
    http = httpx.Client(base_url=server, timeout=30)
    # the CLI uploads the APK in parts and registers the build
    sizes = split_sizes(len(APK), part_max=40_000)
    parts, offset = [], 0
    for i, size in enumerate(sizes):
        chunk = APK[offset : offset + size]
        offset += size
        parts.append(
            {
                "index": i,
                "key": part_key(APK_SHA, i),
                "sha256": hashlib.sha256(chunk).hexdigest(),
                "size": size,
            }
        )
    manifest = {"sha256": APK_SHA, "size": len(APK), "parts": parts}
    targets = http.post("/v1/apk/upload-urls", json=manifest, headers=admin).json()["parts"]
    offset = 0
    for target, size in zip(targets, sizes, strict=True):
        up = target["upload"]
        response = http.request(
            up["method"], up["url"], files={up["form_field"]: ("part", APK[offset : offset + size])}
        )
        assert response.status_code == 200
        offset += size
    build = http.post(
        "/v1/builds",
        json={"sha256": APK_SHA, "label": "e2e", "locator_tag": "87d396162a05", "apk": manifest},
        headers=admin,
    ).json()["build"]
    validation = http.post(
        f"/v1/builds/{build['id']}/validate", json={"features": ["store"], "n": 4}, headers=admin
    ).json()
    # the agent enrols over HTTP
    registered = AgentApi(server, None).register(enrolment, "e2e-pc", {"platform": "android"})
    api = AgentApi(server, registered["token"], max_wait_s=10)
    phone = FakePhone()
    run_count: dict[str, int] = {}

    def runner(test_file: Path, context: Any, *, timeout_s: float) -> RunRecord:
        cid = int(test_file.stem.removeprefix("test_c"))
        with session_scope(engine) as s:
            idx = s.get_one(Candidate, cid).idx
        key = context.flags[0] if context.flags else "clean"
        run_count[key] = run_count.get(key, 0) + 1
        outcome = Outcome.PASSED
        if idx == 3 and key == "clean" and run_count[key] >= 2:
            outcome = Outcome.ASSERTION  # the flaky candidate
        elif idx == 0 and key != "clean":
            outcome = Outcome.ASSERTION  # the good one kills every relevant dev bug
        directory = Path(context.artifact_dir) / "attempt_1"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "pytest_output.txt").write_text(f"{key} {outcome.value}")
        attempt = Attempt(
            attempt=1,
            outcome=outcome,
            rule=f"plugin_{outcome.value}",
            duration_s=4.0,
            wall_s=12.0,
            exit_code=0,
            timed_out=False,
            artifact_dir=str(directory),
        )
        return RunRecord(test_file=str(test_file), flags=tuple(context.flags), attempts=(attempt,))

    executor = Executor(
        api=api,
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
        work_dir=tmp_path / "agent",
        cache_dir=tmp_path / "apks",
        adb=phone,  # type: ignore[arg-type]
        runner=runner,
    )
    agent = AgentLoop(api=api, executor=executor, health=lambda full: Health(True, []))
    worker = WorkerLoop(
        WorkerContext(
            settings=WorkerSettings(
                _env_file=None, database_url=database_url, pg_worker_name="e2e-worker"
            ),  # type: ignore[call-arg]
            engine=engine,
            now=lambda: datetime.now(UTC),
            jitter=lambda: 0.0,
            root=ROOT,
            llm_factory=lambda: FakeLLM([reply(CODES)]),
        ),
        sleep=lambda _: False,
    )
    agent.start()
    for _ in range(300):
        worked = worker.run_once()
        worked = agent.run_once() or worked
        status = http.get(f"/v1/validations/{validation['id']}", headers=admin).json()
        if status["status"] != "running":
            break
        assert worked, f"stuck: {status['jobs']}"
    assert status["status"] == "succeeded", status
    assert phone.installs == 1
    decisions = {c["name"]: c["decision"] for c in status["candidates"]}
    assert decisions == {
        "test_buy_character_unlock_and_select": "accept",
        "test_buy_raccoon_other_spec": "review",
        "test_buy_everything": "reject",
        "test_buy_flaky": "reject",
    }
    accepted = next(c for c in status["candidates"] if c["decision"] == "accept")
    assert accepted["kills"]
    detail = http.get(f"/v1/candidates/{accepted['id']}", headers=admin).json()
    assert all(e["artifacts"] == ["pytest_output.txt"] for e in detail["executions"])
    with session_scope(engine) as s:
        agent_row = s.execute(select(Agent)).scalar_one()
        assert agent_row.name == "e2e-pc"
        assert agent_row.health["healthy"] is True
        assert s.execute(select(Job).where(Job.status != "succeeded")).first() is None
    api.close()
    http.close()
