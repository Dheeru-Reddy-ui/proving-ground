"""The FastAPI application: `/v1` API, health checks, admin session and (M2.5) dashboard.

`create_app` takes every dependency explicitly, so tests build an app around a throwaway
database and local storage; `app_from_env` is the production factory (`pg api serve`).
"""

from __future__ import annotations

import math
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from pg_api import errors, logs
from pg_api.middleware import BodyLimitMiddleware, RateLimitMiddleware, RequestIdMiddleware
from pg_api.routes import agents, builds, health, local_storage, session
from pg_api.settings import ApiSettings, load_settings
from pg_api.state import AppState, make_storage, utc_now
from pg_api.storage import LocalStorage, Storage
from pg_core.builds import PART_MAX_BYTES
from pg_core.ratelimit import Limit
from pg_db.session import make_engine

SESSION_MAX_AGE_S = 8 * 3600
API_VERSION = "0.2.0"


def create_app(
    settings: ApiSettings,
    *,
    engine_url: str | None = None,
    storage: Storage | None = None,
    root: Path = Path(),
    now: Callable[[], datetime] = utc_now,
    clock: Callable[[], float] = time.monotonic,
    lifespan_hooks: Sequence[Callable[[AppState], Callable[[], None]]] = (),
) -> FastAPI:
    """`lifespan_hooks` start background work (the embedded worker) and return its stop."""
    state = AppState(
        settings=settings,
        engine=make_engine(engine_url or settings.database_url.get_secret_value()),
        storage=storage or make_storage(settings),
        root=root,
        now=now,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stops = [hook(state) for hook in lifespan_hooks]
        try:
            yield
        finally:
            for stop in reversed(stops):
                stop()
            state.engine.dispose()

    app = FastAPI(
        title="Proving Ground",
        version=API_VERSION,
        summary="A trust layer for AI-generated game tests: control plane API.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.pg = state
    errors.install(app)
    app.include_router(health.router)
    app.include_router(session.router)
    app.include_router(agents.router)
    app.include_router(builds.router)
    if isinstance(state.storage, LocalStorage):
        app.include_router(local_storage.router)

    limits = {
        kind: _limit(per_minute)
        for kind, per_minute in (
            ("public", settings.pg_rate_public_per_min),
            ("webhook", settings.pg_rate_webhook_per_min),
            ("login", settings.pg_rate_login_per_min),
            ("agent", settings.pg_rate_agent_per_min),
        )
    }
    # Starlette runs the last added middleware first: request id → rate limit → body limit → session
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.pg_session_secret.get_secret_value(),
        session_cookie="pg_session",
        max_age=SESSION_MAX_AGE_S,
        same_site="strict",
        https_only=settings.pg_secure_cookies,
    )
    app.add_middleware(BodyLimitMiddleware, max_body_size=_body_limit(settings, state.storage))
    app.add_middleware(
        RateLimitMiddleware,
        limits=limits,
        trusted_proxy_hops=settings.pg_trusted_proxy_hops,
        clock=clock,
    )
    app.add_middleware(RequestIdMiddleware)
    return app


def _limit(per_minute: float) -> Limit:
    """A bucket that holds ten seconds' worth of requests (at least one)."""
    return Limit(per_minute=per_minute, burst=max(1, math.ceil(per_minute / 6)))


def _body_limit(settings: ApiSettings, storage: Storage) -> int:
    """Local storage receives APK parts through the API; nothing else needs big bodies."""
    if isinstance(storage, LocalStorage):
        return max(settings.pg_max_body_bytes, PART_MAX_BYTES + 1_000_000)
    return settings.pg_max_body_bytes


def app_from_env() -> FastAPI:
    """Production factory: validated settings, JSON logs, and the embedded worker when
    PG_EMBEDDED_WORKER=true (ADR-0010)."""
    settings = load_settings()
    logs.configure()
    hooks: list[Callable[[AppState], Callable[[], None]]] = []
    if settings.pg_embedded_worker:
        from pg_worker.runtime import embedded_worker

        hooks.append(embedded_worker)
    return create_app(settings, lifespan_hooks=hooks)
