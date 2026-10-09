"""Liveness and readiness. `/readyz` fails (503) while the database is unreachable, behind the
migrations this code expects, or the embedded worker has stopped; it recovers on its own."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Annotated, Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from pg_api import logs
from pg_api.state import AppState, app_state
from pg_db.diagnostics import db_reason

router = APIRouter(tags=["health"])
log = logs.get("pg_api.health")
State = Annotated[AppState, Depends(app_state)]


@cache
def migration_head(root: Path) -> str | None:
    config = Config()
    config.set_main_option("script_location", str(root / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
def readyz(state: State) -> JSONResponse:
    checks: dict[str, Any] = {}
    try:
        with state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        checks["database"] = "ok"
        head = migration_head(state.root.resolve())
        checks["migrations"] = "ok" if version == head else f"at {version}, code expects {head}"
    except SQLAlchemyError as exc:
        checks["database"] = f"unavailable ({type(exc).__name__})"
        log.warning("readyz_database_unavailable", reason=db_reason(exc))
    if state.worker_alive is not None:
        checks["worker"] = "ok" if state.worker_alive() else "stopped"
    ready = all(v == "ok" for v in checks.values()) and "migrations" in checks
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status_code=200 if ready else 503,
    )
