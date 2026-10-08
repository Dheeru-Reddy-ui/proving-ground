"""The control-plane API against real Postgres and local storage (M2.2).

Covers auth (agent, admin token, admin session + CSRF), the webhook's signature and replay
rules, APK parts, the agent's lease cycle through HTTP, artifacts, public demo mode, rate and
size limits, and 503s when the database is down.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select

from pg_api.app import create_app
from pg_api.auth import create_admin_token, create_enrolment_token, hash_password
from pg_api.settings import ApiSettings
from pg_api.storage import UploadTarget
from pg_core.builds import part_key
from pg_core.gates.base import Outcome
from pg_core.jobs import JobType
from pg_core.webhook import signature
from pg_db import jobs as queue
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
    Validation,
    WebhookDelivery,
)
from pg_db.session import session_scope
from tests.integration.fakes import Clock, run_result, worker_step

ROOT = Path(__file__).resolve().parents[2]
ADMIN_PASSWORD = "correct horse battery staple"  # noqa: S105 (a test value)
WEBHOOK_SECRET = "webhook-secret-for-tests"  # noqa: S105 (a test value)
SESSION_SECRET = "s" * 40
TAG = "87d396162a05"  # a locator map that exists in pg_sdk/locators/
APK = b"fake apk bytes " * 100
SHA = hashlib.sha256(APK).hexdigest()
PASSWORD_HASH = hash_password(ADMIN_PASSWORD)


@pytest.fixture(autouse=True)
def empty_tables(engine: Engine) -> None:
    with session_scope(engine) as s:
        for table in (
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
            Build,
        ):
            s.execute(delete(table))


ClientFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(database_url: str, tmp_path: Path) -> Iterator[ClientFactory]:
    clients: list[TestClient] = []

    def factory(*, clock: Clock | None = None, **overrides: Any) -> TestClient:
        values: dict[str, Any] = {
            "database_url": database_url,
            "pg_session_secret": SESSION_SECRET,
            "pg_admin_password_hash": PASSWORD_HASH,
            "pg_webhook_secret": WEBHOOK_SECRET,
            "pg_secure_cookies": False,
            "pg_local_storage_dir": tmp_path / "storage",
            "pg_public_base_url": "http://testserver",
        }
        values.update(overrides)
        storage = values.pop("storage", None)
        engine_url = values.pop("engine_url", None)
        settings = ApiSettings(_env_file=None, **values)  # type: ignore[call-arg]
        now = clock or Clock()
        app = create_app(
            settings, engine_url=engine_url, storage=storage, root=ROOT, now=lambda: now.now
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def admin_headers(engine: Engine) -> dict[str, str]:
    with session_scope(engine) as s:
        return {"Authorization": f"Bearer {create_admin_token(s, 'cli')}"}


def enrolment(engine: Engine, now: datetime, ttl: timedelta = timedelta(hours=1)) -> str:
    with session_scope(engine) as s:
        return create_enrolment_token(s, now, ttl)


def enrol_agent(client: TestClient, engine: Engine, name: str = "pc-1") -> dict[str, str]:
    token = enrolment(engine, datetime.now(UTC))
    response = client.post(
        "/v1/agents/register",
        json={"enrolment_token": token, "name": name, "capabilities": {"platform": "android"}},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def registration(sha: str = SHA, **extra: Any) -> dict[str, Any]:
    return {"sha256": sha, "label": "test build", "locator_tag": TAG, **extra}


def apk_manifest() -> dict[str, Any]:
    return {
        "sha256": SHA,
        "size": len(APK),
        "parts": [{"index": 0, "key": part_key(SHA, 0), "sha256": SHA, "size": len(APK)}],
    }


def upload(client: TestClient, target: dict[str, Any] | UploadTarget, data: bytes) -> None:
    t = target if isinstance(target, UploadTarget) else UploadTarget.model_validate(target)
    response = client.request(t.method, t.url, files={t.form_field: ("blob", data)})
    assert response.status_code == 200, response.text


# --- health, request ids, errors ----------------------------------------------------------


def test_health_and_readiness(make_client: ClientFactory) -> None:
    client = make_client()
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "migrations": "ok"}


def test_request_ids_are_returned_and_generated(make_client: ClientFactory) -> None:
    client = make_client()
    mine = client.get("/healthz", headers={"X-Request-ID": "abc12345-req"})
    assert mine.headers["X-Request-ID"] == "abc12345-req"
    generated = client.get("/healthz", headers={"X-Request-ID": "bad id with spaces"})
    assert len(generated.headers["X-Request-ID"]) == 32
    error = client.get("/v1/builds")
    body = error.json()["error"]
    assert error.status_code == 401
    assert body["code"] == "unauthorized"
    assert body["request_id"] == error.headers["X-Request-ID"]


def test_validation_errors_do_not_echo_input(make_client: ClientFactory) -> None:
    client = make_client()
    response = client.post(
        "/v1/agents/register",
        json={"enrolment_token": "pge_secret-value-123", "name": "Bad Name!", "capabilities": {}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["problems"][0]["loc"] == ["body", "name"]
    assert "secret-value-123" not in response.text


def test_reads_need_the_admin_unless_public_demo(
    make_client: ClientFactory, engine: Engine
) -> None:
    private = make_client()
    assert private.get("/v1/builds").status_code == 401
    assert private.get("/v1/builds", headers=admin_headers(engine)).status_code == 200
    public = make_client(pg_public_demo=True)
    assert public.get("/v1/builds").status_code == 200
    assert public.post("/v1/builds", json=registration()).status_code == 401  # still read-only


# --- builds, APK parts, webhook -----------------------------------------------------------


def test_build_registration_is_idempotent_and_checks_locators(
    make_client: ClientFactory, engine: Engine
) -> None:
    client = make_client()
    headers = admin_headers(engine)
    first = client.post("/v1/builds", json=registration(), headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True
    assert first.json()["build"]["source"] == "cli"
    again = client.post("/v1/builds", json=registration(label="renamed"), headers=headers)
    assert again.json()["created"] is False
    assert again.json()["build"]["label"] == "test build"
    unknown = client.post(
        "/v1/builds", json=registration("e" * 64, locator_tag="0" * 12), headers=headers
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "unknown_locator_tag"


def test_apk_parts_are_uploaded_registered_and_downloaded(
    make_client: ClientFactory, engine: Engine
) -> None:
    client = make_client()
    headers = admin_headers(engine)
    early = client.post("/v1/builds", json=registration(apk=apk_manifest()), headers=headers)
    assert early.status_code == 422  # parts not uploaded yet
    urls = client.post("/v1/apk/upload-urls", json=apk_manifest(), headers=headers).json()
    (part,) = urls["parts"]
    assert part["exists"] is False
    upload(client, part["upload"], APK)
    again = client.post("/v1/apk/upload-urls", json=apk_manifest(), headers=headers).json()
    assert again["parts"][0] == {
        "index": 0,
        "key": part_key(SHA, 0),
        "exists": True,
        "upload": None,
    }
    registered = client.post("/v1/builds", json=registration(apk=apk_manifest()), headers=headers)
    assert registered.json()["build"]["has_apk"] is True
    agent = enrol_agent(client, engine)
    apk = client.get(f"/v1/apk/{SHA}", headers=agent).json()
    assert apk["sha256"] == SHA
    assert client.get(apk["parts"][0]["url"]).content == APK
    assert client.get(f"/v1/apk/{SHA}", headers=headers).status_code == 401  # agents only
    tampered = apk["parts"][0]["url"].replace("op=get", "op=put")
    assert client.get(tampered).status_code == 403


def signed(
    body: bytes, *, secret: str = WEBHOOK_SECRET, ts: int | None = None, delivery: str | None = None
) -> dict[str, str]:
    stamp = str(int(time.time()) if ts is None else ts)
    return {
        "X-PG-Timestamp": stamp,
        "X-PG-Signature": signature(secret, stamp, body),
        "X-PG-Delivery": delivery or uuid.uuid4().hex,
        "Content-Type": "application/json",
    }


def test_the_webhook_registers_one_build_and_rejects_forgeries(
    make_client: ClientFactory, engine: Engine
) -> None:
    client = make_client(clock=_real_clock(), pg_rate_webhook_per_min=600)
    body = (
        f'{{"sha256": "{"c" * 64}", "label": "release 3", "locator_tag": "{TAG}", '
        '"patch_notes": "n"}'
    ).encode()
    headers = signed(body, delivery="delivery-0001")
    first = client.post("/v1/webhooks/github", content=body, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True
    assert first.json()["build"]["source"] == "webhook"
    replay = client.post("/v1/webhooks/github", content=body, headers=headers)
    assert replay.json() == {**first.json(), "created": False, "duplicate_delivery": True}
    new_delivery = client.post("/v1/webhooks/github", content=body, headers=signed(body))
    assert new_delivery.json()["created"] is False  # idempotent on the APK hash
    forged = client.post(
        "/v1/webhooks/github",
        content=body,
        headers=signed(body, secret="wrong-secret"),  # noqa: S106
    )
    assert forged.status_code == 401
    assert forged.json()["error"]["message"] == "webhook rejected: bad_signature"
    old = client.post(
        "/v1/webhooks/github", content=body, headers=signed(body, ts=int(time.time()) - 600)
    )
    assert old.status_code == 401
    assert old.json()["error"]["message"] == "webhook rejected: stale_timestamp"
    tampered = client.post(
        "/v1/webhooks/github",
        content=body.replace(b"release 3", b"release 4"),
        headers=signed(body),
    )
    assert tampered.status_code == 401
    with session_scope(engine) as s:
        assert s.query(Build).count() == 1
        assert s.query(WebhookDelivery).count() == 2


def test_the_webhook_is_off_without_a_secret(make_client: ClientFactory) -> None:
    client = make_client(pg_webhook_secret=None)
    response = client.post("/v1/webhooks/github", content=b"{}", headers=signed(b"{}"))
    assert response.status_code == 503


def _real_clock() -> Clock:
    clock = Clock()
    clock.now = datetime.now(UTC)
    return clock


# --- agents and tokens --------------------------------------------------------------------


def test_enrolment_tokens_are_one_time_and_expire(
    make_client: ClientFactory, engine: Engine
) -> None:
    clock = _real_clock()
    client = make_client(clock=clock, pg_rate_login_per_min=600)
    token = enrolment(engine, clock.now)
    body = {"enrolment_token": token, "name": "pc-1", "capabilities": {}}
    assert client.post("/v1/agents/register", json=body).status_code == 201
    reused = client.post("/v1/agents/register", json={**body, "name": "pc-2"})
    assert reused.status_code == 401
    expired = enrolment(engine, clock.now - timedelta(hours=2))
    assert (
        client.post(
            "/v1/agents/register", json={**body, "enrolment_token": expired, "name": "pc-3"}
        ).status_code
        == 401
    )
    with session_scope(engine) as s:
        assert [a.name for a in s.execute(select(Agent)).scalars()] == ["pc-1"]
        stored = s.execute(select(ApiToken)).scalar_one()
        assert stored.kind == "agent"
        assert len(stored.token_sha256) == 64  # only the hash is stored


def test_tokens_are_scoped_and_revocable(make_client: ClientFactory, engine: Engine) -> None:
    client = make_client(clock=_real_clock())
    agent = enrol_agent(client, engine)
    admin = admin_headers(engine)
    claim = {"types": ["RUN_TEST"], "capabilities": {"platform": "android"}}
    assert client.post("/v1/jobs/claim", json=claim, headers=agent).status_code == 204
    assert client.post("/v1/jobs/claim", json=claim, headers=admin).status_code == 401
    assert client.get("/v1/builds", headers=agent).status_code == 401
    worker_types = {"types": ["GENERATE"], "capabilities": {}}
    assert client.post("/v1/jobs/claim", json=worker_types, headers=agent).status_code == 422
    with session_scope(engine) as s:
        s.execute(select(Agent)).scalar_one().revoked_at = datetime.now(UTC)
    assert client.post("/v1/jobs/claim", json=claim, headers=agent).status_code == 401


# --- a validation driven through the API --------------------------------------------------


def test_an_agent_runs_a_validation_through_the_api(
    make_client: ClientFactory, engine: Engine
) -> None:
    clock = _real_clock()
    client = make_client(clock=clock)
    public = make_client(clock=clock, pg_public_demo=True)
    admin = admin_headers(engine)
    agent = enrol_agent(client, engine)
    build_id = client.post("/v1/builds", json=registration(), headers=admin).json()["build"]["id"]
    created = client.post(
        f"/v1/builds/{build_id}/validate",
        json={"features": ["store"], "n": 1, "idempotency_key": "v-1"},
        headers=admin,
    )
    assert created.status_code == 201, created.text
    validation_id = created.json()["id"]
    assert created.json()["jobs"] == {"queued": 2}
    claim = {
        "types": ["INSTALL_BUILD", "RUN_TEST"],
        "capabilities": {"platform": "android", "max_concurrency": 1},
        "installed_build_sha": SHA,
    }
    install = client.post("/v1/jobs/claim", json=claim, headers=agent).json()
    assert install["type"] == "INSTALL_BUILD"
    assert (
        client.post("/v1/jobs/claim", json=claim, headers=agent).status_code == 204
    )  # one at a time
    wrong = client.post(
        f"/v1/jobs/{install['job_id']}/complete",
        json={"lease_token": install["lease_token"], "result": {"installed_sha256": "f" * 64}},
        headers=agent,
    )
    assert wrong.status_code == 422
    done = client.post(
        f"/v1/jobs/{install['job_id']}/complete",
        json={"lease_token": install["lease_token"], "result": {"installed_sha256": SHA}},
        headers=agent,
    )
    assert done.json() == {"status": "stored", "job_status": "succeeded"}
    # the worker generates and scores (fake LLM, fake G1)
    fake = Clock()
    fake.now = clock.now
    for _ in range(2):
        with session_scope(engine) as s:
            job = queue.claim(
                s,
                owner="worker-1",
                types=[JobType.GENERATE, JobType.SCORE],
                capabilities={},
                now=fake.tick(),
            )
            assert job is not None
            worker_step(s, job, fake, ())
    clock.now = fake.tick()
    run = client.post("/v1/jobs/claim", json=claim, headers=agent).json()
    assert run["type"] == "RUN_TEST"
    assert run["payload"]["flags"] == []
    code = client.get(f"/v1/code/{run['payload']['code_sha']}", headers=agent).json()
    assert hashlib.sha256(code["code"].encode()).hexdigest() == run["payload"]["code_sha"]
    beat = client.post(
        f"/v1/jobs/{run['job_id']}/heartbeat",
        json={"lease_token": run["lease_token"]},
        headers=agent,
    )
    assert beat.status_code == 200
    lost = client.post(
        f"/v1/jobs/{run['job_id']}/heartbeat", json={"lease_token": "x" * 32}, headers=agent
    )
    assert lost.status_code == 409
    target = client.post(
        "/v1/artifacts/upload-url",
        json={
            "job_id": run["job_id"],
            "lease_token": run["lease_token"],
            "attempt": 1,
            "name": "screen.png",
        },
        headers=agent,
    ).json()
    upload(client, target, b"png bytes")
    result = run_result(Outcome.ASSERTION, artifacts={"screen.png": target["key"]})
    payload = {"lease_token": run["lease_token"], "result": result.model_dump(mode="json")}
    foreign = run_result(Outcome.ASSERTION, artifacts={"x.png": "artifacts/job-999/x.png"})
    bad = client.post(
        f"/v1/jobs/{run['job_id']}/complete",
        json={**payload, "result": foreign.model_dump(mode="json")},
        headers=agent,
    )
    assert bad.status_code == 422
    stored = client.post(f"/v1/jobs/{run['job_id']}/complete", json=payload, headers=agent)
    assert stored.json() == {"status": "stored", "job_status": "succeeded"}
    duplicate = client.post(f"/v1/jobs/{run['job_id']}/complete", json=payload, headers=agent)
    assert duplicate.json() == {"status": "duplicate", "job_status": "succeeded"}
    other = run_result(Outcome.PASSED).model_dump(mode="json")
    conflict = client.post(
        f"/v1/jobs/{run['job_id']}/complete",
        json={"lease_token": run["lease_token"], "result": other},
        headers=agent,
    )
    assert conflict.status_code == 409
    # the clean run failed: the planner moves on to final scoring
    detail = client.get(f"/v1/validations/{validation_id}", headers=admin).json()
    assert detail["jobs"] == {"succeeded": 4, "queued": 1}
    assert detail["device_seconds"] == 30.0
    cid = detail["candidates"][0]["id"]
    candidate = client.get(f"/v1/candidates/{cid}", headers=admin).json()
    (execution,) = candidate["executions"]
    assert execution["outcome"] == "assertion"
    assert execution["artifacts"] == ["screen.png"]
    link = client.get(
        f"/v1/executions/{execution['id']}/artifacts/screen.png",
        headers=admin,
        follow_redirects=False,
    )
    assert link.status_code == 307
    assert client.get(link.headers["location"]).content == b"png bytes"
    shown = public.get(f"/v1/candidates/{cid}").json()
    assert shown["executions"][0]["artifacts"] == []
    assert public.get(f"/v1/executions/{execution['id']}/artifacts/screen.png").status_code == 401
    assert public.get(f"/v1/builds/{build_id}").json()["validations"][0]["id"] == validation_id
    again = client.post(
        f"/v1/builds/{build_id}/validate",
        json={"features": ["store"], "n": 1, "idempotency_key": "v-1"},
        headers=admin,
    )
    assert again.json()["id"] == validation_id
    assert (
        client.post(
            f"/v1/builds/{build_id}/validate", json={"features": ["nope"]}, headers=admin
        ).json()["error"]["code"]
        == "unknown_feature"
    )


# --- admin session ------------------------------------------------------------------------


def test_admin_session_login_csrf_and_review(make_client: ClientFactory, engine: Engine) -> None:
    client = make_client(clock=_real_clock())
    assert client.post("/login", data={"password": "wrong"}).status_code == 401
    login = client.post("/login", data={"password": ADMIN_PASSWORD}, follow_redirects=False)
    assert login.status_code == 303
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    session = client.get("/session").json()
    assert session["admin"] is True
    csrf = session["csrf_token"]
    assert client.get("/v1/builds").status_code == 200
    no_csrf = client.post("/v1/builds", json=registration())
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "csrf_failed"
    ok = client.post("/v1/builds", json=registration(), headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200
    with session_scope(engine) as s:
        build = s.execute(select(Build)).scalar_one()
        run = GenerationRun(
            build_id=build.id,
            feature="store",
            provider="fake",
            model="fake",
            prompt_version="v2",
            prompt_hash="h",
            n_requested=1,
        )
        s.add(run)
        s.flush()
        cand = Candidate(
            generation_run_id=run.id, name="t", code="c", code_sha="0" * 64, decision="review"
        )
        s.add(cand)
        s.flush()
        cid = cand.id
    review = {"decision": "reject", "reason": "the assertion cannot fail"}
    assert client.post(f"/v1/candidates/{cid}/review", json=review).status_code == 403
    reviewed = client.post(
        f"/v1/candidates/{cid}/review", json=review, headers={"X-CSRF-Token": csrf}
    )
    assert reviewed.json()["human_decision"] == "reject"
    twice = client.post(f"/v1/candidates/{cid}/review", json=review, headers={"X-CSRF-Token": csrf})
    assert twice.status_code == 200  # still REVIEW; the latest human decision is kept
    client.post("/logout", data={"csrf_token": csrf})
    assert client.get("/session").json() == {"admin": False}


# --- limits and failures ------------------------------------------------------------------


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: set[str] = set()

    def upload_target(self, key: str) -> UploadTarget:
        return UploadTarget(key=key, url=f"memory://{key}")

    def download_url(self, key: str, expires_s: int) -> str:
        return f"memory://{key}?ttl={expires_s}"

    def exists(self, key: str) -> bool:
        return key in self.objects


def test_rate_and_body_limits(make_client: ClientFactory) -> None:
    client = make_client(
        pg_rate_login_per_min=30, storage=MemoryStorage(), pg_max_body_bytes=10_000
    )
    codes = [client.post("/login", data={"password": "x"}).status_code for _ in range(6)]
    assert codes == [401, 401, 401, 401, 401, 429]
    limited = client.post("/login", data={"password": "x"})
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    assert client.get("/healthz").status_code == 200  # other classes are unaffected
    big = client.post(
        "/v1/builds", content=b"x" * 20_000, headers={"Content-Type": "application/json"}
    )
    assert big.status_code == 413
    assert big.json()["error"]["code"] == "content_too_large"


def test_a_database_outage_answers_503(make_client: ClientFactory) -> None:
    client = make_client(engine_url="postgresql://nobody:nothing@127.0.0.1:1/none")
    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["checks"]["database"].startswith("unavailable")
    response = client.get("/v1/builds", headers={"Authorization": "Bearer pga_x"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
