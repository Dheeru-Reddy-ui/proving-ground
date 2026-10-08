"""Server-rendered dashboard (Jinja2 + HTMX, no SPA): builds, runs, candidates, review, system."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).parent / "static"


def mount_dashboard(app: FastAPI) -> None:
    from pg_dashboard.routes import router

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
