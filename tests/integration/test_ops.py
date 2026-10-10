"""Operations (M2.6): alerts are raised once per episode, and /metrics reports the tables."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select

from pg_api.app import create_app
from pg_api.auth import create_admin_token
from pg_api.settings import ApiSettings
from pg_db.models import Agent, Alert, ApiToken, Job
from pg_db.session import session_scope
from pg_worker.alerts import raise_alerts

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)


def reset(engine: Engine) -> None:
    with session_scope(engine) as s:
        for table in (Alert, Job, ApiToken, Agent):
            s.execute(delete(table))


def dead_job(engine: Engine, key: str, finished: datetime) -> int:
    with session_scope(engine) as s:
        job = Job(
            type="RUN_TEST",
            payload={},
            requires={},
            status="dead",
            priority=0,
            attempts=3,
            max_attempts=3,
            run_after=NOW,
            idempotency_key=key,
            last_error="infra on every attempt (plugin_infra) device offline",
            created_at=NOW,
            updated_at=NOW,
            finished_at=finished,
        )
        s.add(job)
        s.flush()
        return job.id


def test_alerts_are_raised_once_per_episode(engine: Engine) -> None:
    reset(engine)
    sent: list[str] = []
    job_id = dead_job(engine, "a", NOW)
    with session_scope(engine) as s:
        s.add(
            Agent(name="pc-1", capabilities={}, health={}, last_seen_at=NOW - timedelta(minutes=11))
        )
        s.add(
            Agent(name="pc-2", capabilities={}, health={}, last_seen_at=NOW - timedelta(minutes=2))
        )
    first = raise_alerts(engine, NOW, lambda m: sent.append(m) or True)
    assert sorted(k.split(":")[0] for k in first) == ["dead-job", "stale-agent"]
    assert any(f"job {job_id} (RUN_TEST) gave up after 3 attempts: infra" in m for m in sent)
    assert any("device agent pc-1 has not been seen for 11 min" in m for m in sent)
    assert raise_alerts(engine, NOW + timedelta(minutes=1), lambda m: sent.append(m) or True) == []
    assert len(sent) == 2  # nothing is sent twice
    with session_scope(engine) as s:
        s.execute(select(Agent).where(Agent.name == "pc-1")).scalar_one().last_seen_at = NOW
    later = NOW + timedelta(minutes=30)  # pc-1 came back, then went silent again
    again = raise_alerts(engine, later, lambda m: False)
    assert [k.split(":")[0] for k in again] == ["stale-agent", "stale-agent"]  # pc-1 and pc-2
    with session_scope(engine) as s:
        unsent = s.execute(select(Alert).where(Alert.sent.is_(False))).scalars().all()
        assert len(unsent) == 2  # the webhook refused them; they are recorded anyway


def queued_device_job(engine: Engine, key: str) -> None:
    with session_scope(engine) as s:
        s.add(
            Job(
                type="RUN_TEST",
                payload={},
                requires={},
                status="queued",
                priority=0,
                attempts=1,
                max_attempts=3,
                run_after=NOW,
                idempotency_key=key,
                last_error="InstallError: adb install failed: device 'X' not found",
                created_at=NOW,
                updated_at=NOW,
            )
        )


def unhealthy(since: datetime) -> dict[str, object]:
    return {
        "healthy": False,
        "checks": [
            {"name": "adb device", "ok": False, "status": "FAIL", "detail": "no device"},
            {"name": "python", "ok": True, "status": "PASS", "detail": "3.12"},
        ],
        "unhealthy_since": since.isoformat(),
    }


def test_an_agent_unhealthy_while_device_jobs_wait_raises_one_alert(engine: Engine) -> None:
    reset(engine)
    sent: list[str] = []
    with session_scope(engine) as s:
        for name, minutes in (("pc-1", 11), ("pc-2", 5)):
            health = unhealthy(NOW - timedelta(minutes=minutes))
            s.add(Agent(name=name, capabilities={}, health=health, last_seen_at=NOW))
    assert raise_alerts(engine, NOW, lambda m: sent.append(m) or True) == []  # nothing waits
    queued_device_job(engine, "q")
    keys = raise_alerts(engine, NOW, lambda m: sent.append(m) or True)
    assert [k.split(":")[0] for k in keys] == ["unhealthy-agent"]  # pc-2: only 5 min so far
    assert sent == [
        "Proving Ground: device agent pc-1 has been unhealthy for 11 min (failing: adb device) "
        "while 1 device job(s) wait in the queue; it claims nothing until its checks pass."
    ]
    assert raise_alerts(engine, NOW + timedelta(minutes=3), lambda m: True) == []  # same spell
    with session_scope(engine) as s:
        for agent in s.execute(select(Agent)).scalars():
            agent.last_seen_at = NOW + timedelta(minutes=20)  # both still report
        pc1 = s.execute(select(Agent).where(Agent.name == "pc-1")).scalar_one()
        pc1.health = {"healthy": True, "checks": []}
    later = raise_alerts(engine, NOW + timedelta(minutes=20), lambda m: True)
    assert later == [f"unhealthy-agent:pc-2:{(NOW - timedelta(minutes=5)).isoformat()}"]


def test_metrics_need_the_admin_and_report_the_tables(
    engine: Engine, database_url: str, tmp_path: Path
) -> None:
    reset(engine)
    dead_job(engine, "m", NOW)
    with session_scope(engine) as s:
        token = create_admin_token(s, "metrics")
        s.add(
            Agent(
                name='pc "1"',
                capabilities={},
                health={"healthy": True},
                last_seen_at=datetime.now(UTC),
            )
        )
    settings = ApiSettings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=database_url,
        pg_session_secret="m" * 40,
        pg_local_storage_dir=tmp_path,
        pg_public_demo=True,
    )
    with TestClient(create_app(settings, root=ROOT)) as client:
        assert client.get("/metrics").status_code == 401  # not public, even in demo mode
        body = client.get("/metrics", headers={"Authorization": f"Bearer {token}"}).text
    assert "# TYPE pg_jobs gauge" in body
    assert 'pg_jobs{status="dead",type="RUN_TEST"} 1' in body
    assert 'pg_agent_healthy{agent="pc \\"1\\""} 1' in body
    assert "pg_llm_spend_today_usd 0" in body
