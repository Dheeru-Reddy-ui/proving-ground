"""The worker loop: claim a GENERATE or SCORE job, keep its lease alive while handling it, and
reap expired leases (ADR-0006). Runs standalone (`pg worker run`, `python -m pg_worker`) or as a
thread inside the API (`PG_EMBEDDED_WORKER=true`, ADR-0010)."""

from __future__ import annotations

import threading
from collections.abc import Callable

import structlog
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError

from pg_api import logs
from pg_core.jobs import JobType, LeaseCheck
from pg_db import jobs as queue
from pg_db import pipeline
from pg_db.models import Worker
from pg_db.session import session_scope
from pg_worker.alerts import Poster, raise_alerts, webhook_poster
from pg_worker.handlers import HANDLERS, Lease, LeaseLost, WorkerContext

log = logs.get("pg_worker")
REAP_EVERY_S = 10.0
MAX_IDLE_BACKOFF_S = 60.0


class LeaseKeeper:
    """Heartbeats a job's lease from its own thread every third of the TTL until stopped."""

    def __init__(self, ctx: WorkerContext, lease: Lease) -> None:
        self.ctx = ctx
        self.lease = lease
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"lease-{lease.job_id}", daemon=True)

    def _run(self) -> None:
        interval = self.ctx.settings.pg_lease_ttl_s / 3
        while not self.stop.wait(interval):
            try:
                with session_scope(self.ctx.engine) as s:
                    check = queue.heartbeat(
                        s,
                        self.lease.job_id,
                        self.lease.token,
                        self.ctx.now(),
                        self.ctx.settings.pg_lease_ttl_s,
                    )
            except OperationalError:
                log.warning("heartbeat_db_error", job_id=self.lease.job_id)
                continue
            if check is not LeaseCheck.OK:
                self.lost.set()
                log.warning("lease_lost", job_id=self.lease.job_id)
                return

    def __enter__(self) -> LeaseKeeper:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


class WorkerLoop:
    def __init__(self, ctx: WorkerContext, sleep: Callable[[float], bool] | None = None) -> None:
        self.ctx = ctx
        self.stop_event = threading.Event()
        self._sleep = sleep or self.stop_event.wait
        self._last_reap = 0.0
        self.handled = 0
        self._poster: Poster | None = None

    @property
    def name(self) -> str:
        return self.ctx.settings.pg_worker_name

    def types(self) -> list[JobType]:
        types = [JobType.SCORE]
        if self.ctx.settings.pg_generation_enabled:
            types.append(JobType.GENERATE)
        return types

    def beat(self) -> None:
        """Record this worker for the System page (last seen, kill switch)."""
        now = self.ctx.now()
        values = {
            "name": self.name,
            "started_at": now,
            "last_seen_at": now,
            "generation_enabled": self.ctx.settings.pg_generation_enabled,
            "info": {"handled": self.handled},
        }
        with session_scope(self.ctx.engine) as s:
            s.execute(
                insert(Worker)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["name"],
                    set_={k: v for k, v in values.items() if k not in ("name", "started_at")},
                )
            )

    def alert(self) -> list[str]:
        url = self.ctx.settings.pg_alert_webhook_url
        if self._poster is None and url is not None:
            self._poster = webhook_poster(url.get_secret_value())
        return raise_alerts(self.ctx.engine, self.ctx.now(), self._poster)

    def reap(self) -> list[int]:
        with session_scope(self.ctx.engine) as s:
            expired = pipeline.expire_leases(s, self.ctx.now())
        if expired:
            log.warning("leases_expired", job_ids=expired)
        return expired

    def claim(self) -> Lease | None:
        with session_scope(self.ctx.engine) as s:
            job = queue.claim(
                s,
                owner=self.name,
                types=self.types(),
                capabilities={},
                now=self.ctx.now(),
                ttl_s=self.ctx.settings.pg_lease_ttl_s,
            )
            if job is None or job.lease_token is None:
                return None
            return Lease(
                job_id=job.id,
                token=job.lease_token,
                type=JobType(job.type),
                key=job.idempotency_key,
                payload=dict(job.payload),
            )

    def run_once(self) -> bool:
        """Reap, then handle at most one job. True when a job was handled."""
        now_s = self.ctx.now().timestamp()
        if now_s - self._last_reap >= REAP_EVERY_S:
            self._last_reap = now_s
            self.reap()
            self.beat()
            self.alert()
        lease = self.claim()
        if lease is None:
            return False
        with structlog.contextvars.bound_contextvars(
            job_id=lease.job_id, job_type=lease.type.value
        ):
            log.info("job_started", key=lease.key)
            try:
                with LeaseKeeper(self.ctx, lease):
                    HANDLERS[lease.type](self.ctx, lease)
            except LeaseLost as exc:
                log.warning("job_abandoned", reason=str(exc))
            except Exception as exc:  # any handler bug: fail the job (retryable), keep looping
                log.error("job_crashed", exc_info=exc)
                self._fail(lease, f"{type(exc).__name__}: {exc}"[:1000])
        self.handled += 1
        return True

    def _fail(self, lease: Lease, error: str) -> None:
        with session_scope(self.ctx.engine) as s:
            pipeline.fail(
                s,
                lease.job_id,
                lease.token,
                error=error,
                retryable=True,
                now=self.ctx.now(),
                jitter=self.ctx.jitter(),
            )

    def run_forever(self) -> None:
        log.info(
            "worker_started", name=self.name, generation=self.ctx.settings.pg_generation_enabled
        )
        backoff = self.ctx.settings.pg_worker_poll_s
        while not self.stop_event.is_set():
            try:
                worked = self.run_once()
                backoff = self.ctx.settings.pg_worker_poll_s
            except OperationalError:
                log.warning("worker_db_unavailable", retry_in_s=round(backoff, 1))
                worked = False
                backoff = min(MAX_IDLE_BACKOFF_S, backoff * 2) + self.ctx.jitter()
            if not worked:
                self._sleep(backoff)
        log.info("worker_stopped", name=self.name)

    def stop(self) -> None:
        self.stop_event.set()
