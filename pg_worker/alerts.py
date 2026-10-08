"""Operational alerts (M2.6): a job that went dead, or a device agent silent for over 10 minutes.

Each alert has a key naming its episode (the job and when it died; the agent and when it was last
seen), so it is recorded once and sent once, whatever restarts happen. The worker loop checks
every few seconds; when `PG_ALERT_WEBHOOK_URL` is set, new alerts are posted there as JSON with
the text under both `text` and `content` (the fields Slack and Discord incoming webhooks read).
The URL is a secret and is never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert

from pg_api import logs
from pg_db.models import Agent, Alert, Job
from pg_db.session import session_scope

log = logs.get("pg_worker.alerts")
STALE_AGENT = timedelta(minutes=10)
POST_TIMEOUT_S = 10.0
POST_ATTEMPTS = 3

Poster = Callable[[str], bool]


def due(engine: Engine, now: datetime) -> list[tuple[str, str, str]]:
    """(key, kind, message) for every alert condition that holds now."""
    found: list[tuple[str, str, str]] = []
    with session_scope(engine) as s:
        for job in s.execute(select(Job).where(Job.status == "dead")).scalars():
            ended = job.finished_at.isoformat() if job.finished_at else "unknown"
            error = (job.last_error or "no error recorded")[:300]
            found.append(
                (
                    f"dead-job:{job.id}:{ended}",
                    "dead_job",
                    f"Proving Ground: job {job.id} ({job.type}) gave up after {job.attempts} "
                    f"attempts: {error}",
                )
            )
        for agent in s.execute(select(Agent).where(Agent.revoked_at.is_(None))).scalars():
            if agent.last_seen_at is None or now - agent.last_seen_at <= STALE_AGENT:
                continue
            minutes = int((now - agent.last_seen_at).total_seconds() // 60)
            found.append(
                (
                    f"stale-agent:{agent.name}:{agent.last_seen_at.isoformat()}",
                    "stale_agent",
                    f"Proving Ground: device agent {agent.name} has not been seen for "
                    f"{minutes} min (last seen {agent.last_seen_at:%Y-%m-%d %H:%M} UTC).",
                )
            )
    return found


def raise_alerts(engine: Engine, now: datetime, post: Poster | None) -> list[str]:
    """Record new alerts, then send them. Returns the keys of the alerts raised this time."""
    new: list[tuple[int, str]] = []
    with session_scope(engine) as s:
        for key, kind, message in due(engine, now):
            alert_id = s.execute(
                insert(Alert)
                .values(key=key, kind=kind, message=message, sent=False, created_at=now)
                .on_conflict_do_nothing(index_elements=["key"])
                .returning(Alert.id)
            ).scalar_one_or_none()
            if alert_id is not None:
                new.append((alert_id, message))
                log.warning("alert", kind=kind, key=key)
    keys = []
    for alert_id, message in new:
        sent = post(message) if post is not None else False
        with session_scope(engine) as s:
            alert = s.get_one(Alert, alert_id)
            alert.sent = sent
            keys.append(alert.key)
    return keys


def webhook_poster(
    url: str,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Poster:
    http = client or httpx.Client(timeout=POST_TIMEOUT_S)

    def post(message: str) -> bool:
        for attempt in range(1, POST_ATTEMPTS + 1):
            try:
                response = http.post(url, json={"text": message, "content": message})
                if response.status_code < 300:
                    return True
                problem = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                problem = type(exc).__name__
            log.warning("alert_post_failed", attempt=attempt, problem=problem)
            if attempt < POST_ATTEMPTS:
                sleep(2.0**attempt)
        return False

    return post
