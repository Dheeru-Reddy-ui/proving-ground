"""The dashboard (M2.5): every page renders from real rows, writes need the admin and CSRF,
public demo mode is read-only and hides artifacts, and generated code is escaped."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select

from pg_api.app import create_app
from pg_api.auth import hash_password
from pg_api.settings import ApiSettings
from pg_core.gates.base import Outcome
from pg_core.jobs import DEVICE_JOB_TYPES, JobType
from pg_db import jobs as queue
from pg_db import pipeline, repo
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
from pg_db.sync import sync_specs_and_bugs
from pg_worker.handlers import WorkerContext
from pg_worker.loop import WorkerLoop
from pg_worker.settings import WorkerSettings
from tests.integration.fakes import CODES, ROOT, Clock, FakeLLM, reply, run_result

PASSWORD = "dashboard admin password"
SHA = "e" * 64


@pytest.fixture(scope="module")
def seeded(engine: Engine, database_url: str) -> dict[str, int]:
    """One build with one finished validation: 4 candidates, kills, a REVIEW item."""
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
        sync_specs_and_bugs(s, ROOT)
        build, _ = repo.register_build(s, SHA, "dashboard build", "87d396162a05")
        manifest_sha = hashlib.sha256((ROOT / "pg_sdk/manifest.json").read_bytes()).hexdigest()
        clock = Clock()
        validation, _ = pipeline.create_validation(
            s,
            build_id=build.id,
            features=["store"],
            n=4,
            seed=None,
            manifest_sha=manifest_sha,
            run_timeout_s=300,
            requested_by="admin:test",
            now=clock.now,
        )
        vid, bid = validation.id, build.id
    worker = WorkerLoop(
        WorkerContext(
            settings=WorkerSettings(_env_file=None, database_url=database_url, pg_worker_name="w1"),  # type: ignore[call-arg]
            engine=engine,
            now=lambda: clock.now,
            jitter=lambda: 0.0,
            root=ROOT,
            llm_factory=lambda: FakeLLM([reply(CODES)]),
        ),
        sleep=lambda _: False,
    )
    for _ in range(300):
        clock.tick(11)
        worked = worker.run_once()
        with session_scope(engine) as s:
            job = queue.claim(
                s,
                owner="pc",
                types=list(DEVICE_JOB_TYPES),
                capabilities={"platform": "android"},
                now=clock.tick(),
            )
            if job is not None:
                worked = True
                token = job.lease_token or ""
                if job.type == JobType.INSTALL_BUILD.value:
                    pipeline.complete(s, job.id, token, {"installed_sha256": SHA}, clock.now)
                else:
                    idx = s.get_one(Candidate, job.payload["candidate_id"]).idx
                    flags = job.payload["flags"]
                    outcome = Outcome.PASSED
                    if idx == 0 and flags:
                        outcome = Outcome.ASSERTION
                    if idx == 3 and not flags and job.payload["repeat"] == 2:
                        outcome = Outcome.ASSERTION
                    result = run_result(
                        outcome,
                        artifacts={"failure.png": f"artifacts/job-{job.id}/try-1/a1/failure.png"},
                    )
                    pipeline.complete_run_test(s, job.id, token, result, clock.now, 0.0)
        if not worked:
            break
    with session_scope(engine) as s:
        assert s.get_one(Validation, vid).status == "succeeded"
        run = s.execute(select(GenerationRun)).scalar_one()
        by_idx = {c.idx: c.id for c in repo.candidates_of_run(s, run.id)}
        s.add(
            Agent(
                name="pc-1",
                capabilities={},
                health={"healthy": False, "checks": [{"name": "adb device", "ok": False}]},
                last_seen_at=datetime.now(UTC),
            )
        )
        s.add(
            Job(
                type="RUN_TEST",
                payload={},
                requires={},
                status="dead",
                priority=0,
                attempts=3,
                max_attempts=3,
                run_after=clock.now,
                idempotency_key="dead-1",
                last_error="infra on every attempt (plugin_infra) device offline",
                created_at=clock.now,
                updated_at=clock.now,
                finished_at=clock.now,
            )
        )
    return {
        "build": bid,
        "validation": vid,
        "run": run.id,
        **{f"c{i}": cid for i, cid in by_idx.items()},
    }


@pytest.fixture
def make_client(database_url: str, tmp_path: Path) -> Iterator[Any]:
    clients: list[TestClient] = []

    def factory(**overrides: Any) -> TestClient:
        values: dict[str, Any] = {
            "database_url": database_url,
            "pg_session_secret": "d" * 40,
            "pg_admin_password_hash": hash_password(PASSWORD),
            "pg_secure_cookies": False,
            "pg_local_storage_dir": tmp_path / "storage",
            "pg_public_base_url": "http://testserver",
            **overrides,
        }
        client = TestClient(create_app(ApiSettings(_env_file=None, **values), root=ROOT))  # type: ignore[call-arg]
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def logged_in(client: TestClient) -> str:
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    return str(client.get("/session").json()["csrf_token"])


class Checker(HTMLParser):
    """Fails on unbalanced tags (a cheap well-formedness check for the templates)."""

    VOID: ClassVar[set[str]] = {"meta", "link", "input", "br", "img", "rect", "hr"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        assert self.stack, f"</{tag}> closes nothing"
        assert self.stack[-1] == tag, f"</{tag}> closes <{self.stack[-1]}>"
        self.stack.pop()


def well_formed(html: str) -> None:
    checker = Checker()
    checker.feed(html)
    assert checker.stack == [], checker.stack


def test_pages_need_a_login_unless_public_demo(make_client: Any, seeded: dict[str, int]) -> None:
    private = make_client()
    assert private.get("/", follow_redirects=False).headers["location"] == "/login"
    login = private.get("/login")
    assert login.status_code == 200
    well_formed(login.text)
    wrong = private.post("/login", data={"password": "nope"})
    assert wrong.status_code == 401
    assert "That password is not right." in wrong.text
    logged_in(private)
    assert private.get("/").status_code == 200


def test_every_page_renders_for_the_admin(make_client: Any, seeded: dict[str, int]) -> None:
    client = make_client()
    logged_in(client)
    pages = [
        "/",
        f"/builds/{seeded['build']}",
        f"/runs/{seeded['run']}",
        f"/validations/{seeded['validation']}",
        f"/validations/{seeded['validation']}/progress",
        f"/candidates/{seeded['c0']}",
        "/review",
        "/system",
    ]
    for path in pages:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        well_formed(response.text)
    build = client.get(f"/builds/{seeded['build']}").text
    assert "dashboard build" in build
    run = client.get(f"/runs/{seeded['run']}").text
    assert 'class="cell killed">2/2' in run
    assert "rejected by G1: never run" in run
    candidate = client.get(f"/candidates/{seeded['c0']}").text
    assert "/v1/executions/" in candidate  # artifacts for the admin
    assert "failure.png" in candidate
    assert "STORE-5" in candidate
    system = client.get("/system").text
    assert "unhealthy" in system
    assert "adb device" in system
    assert "device offline" in system
    assert client.get("/candidates/999999").status_code == 404


def test_public_demo_is_read_only(make_client: Any, seeded: dict[str, int]) -> None:
    demo = make_client(pg_public_demo=True)
    for path in (
        "/",
        f"/runs/{seeded['run']}",
        f"/candidates/{seeded['c0']}",
        "/review",
        "/system",
    ):
        response = demo.get(path)
        assert response.status_code == 200, path
        assert "Public demo · read-only" in response.text
        assert 'method="post" action="/review' not in response.text
        assert "/v1/executions/" not in response.text  # no artifact links
        assert "enrolment-token" not in response.text
    assert "adb device" not in demo.get("/system").text  # no health detail for the public
    blocked = demo.post(
        f"/review/{seeded['c1']}", data={"decision": "accept", "reason": "looks fine"}
    )
    assert blocked.status_code == 401


def test_review_from_the_queue(make_client: Any, seeded: dict[str, int], engine: Engine) -> None:
    client = make_client()
    csrf = logged_in(client)
    cid = seeded["c1"]
    queue_page = client.get("/review").text
    assert f'id="review-{cid}"' in queue_page
    no_csrf = client.post(
        f"/review/{cid}", data={"decision": "accept", "reason": "checks a real spec"}
    )
    assert no_csrf.status_code == 403
    short = client.post(
        f"/review/{cid}",
        data={"decision": "accept", "reason": "ok", "csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert "Give a reason of at least 3 characters." in short.text
    done = client.post(
        f"/review/{cid}",
        data={"decision": "reject", "reason": "the assertion cannot fail", "csrf_token": csrf},
        headers={"HX-Request": "true"},
    )
    assert done.status_code == 200
    assert "Rejected by a person" in done.text
    assert "<html" not in done.text  # a fragment for HTMX to swap in
    with session_scope(engine) as s:
        cand = s.get_one(Candidate, cid)
        assert (cand.human_decision, cand.human_reason) == ("reject", "the assertion cannot fail")
    plain = client.post(
        f"/review/{cid}",
        data={"decision": "accept", "reason": "changed my mind", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert plain.headers["location"] == "/review"


def test_system_actions(make_client: Any, seeded: dict[str, int], engine: Engine) -> None:
    client = make_client()
    csrf = logged_in(client)
    page = client.post("/system/enrolment-token", data={"csrf_token": csrf})
    token = re.search(r'class="secret">(pge_[^<]+)<', page.text)
    assert token is not None
    with session_scope(engine) as s:
        dead = s.execute(select(Job).where(Job.idempotency_key == "dead-1")).scalar_one()
        dead_id = dead.id
    retried = client.post(
        f"/system/jobs/{dead_id}/retry",
        data={"csrf_token": csrf, "next": "//evil.example/"},
        follow_redirects=False,
    )
    assert retried.headers["location"] == "/system"  # never an off-site redirect
    with session_scope(engine) as s:
        assert s.get_one(Job, dead_id).status == "queued"
    assert client.post(f"/system/jobs/{dead_id}/retry", data={}).status_code == 403


def test_generated_code_is_escaped(
    make_client: Any, seeded: dict[str, int], engine: Engine
) -> None:
    with session_scope(engine) as s:
        run = s.execute(select(GenerationRun)).scalar_one()
        hostile, _ = repo.add_candidate(
            s,
            run.id,
            "test_x",
            "</p><script>alert('intent')</script>",
            "</code></pre><script>alert(1)</script>",
            ["STORE-1"],
        )
        hostile_id = hostile.id
    client = make_client()
    logged_in(client)
    html = client.get(f"/candidates/{hostile_id}").text
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    well_formed(html)
