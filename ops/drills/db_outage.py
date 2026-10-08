"""M2.7 drill: the database goes away and comes back.

Starts a throwaway Postgres 16 container, migrates it, runs the API against it in this process,
then stops and restarts the container. Expected: `/readyz` and every database-backed route answer
503 (`database_unavailable`) while it is down, and both recover without restarting the API.
Prints a timeline; the container is removed at the end.

    uv run python ops/drills/db_outage.py
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config

from pg_api.app import create_app
from pg_api.auth import create_admin_token
from pg_api.settings import ApiSettings
from pg_db.session import make_engine, session_scope

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "postgres:16"
NAME = "pg-drill-db-outage"


def docker(*args: str) -> str:
    out = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return out.stdout.strip()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(check: Callable[[], bool], timeout_s: float) -> float:
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        if check():
            return time.monotonic() - start
        time.sleep(0.5)
    raise TimeoutError("condition not met")


def main() -> None:
    password = secrets.token_urlsafe(16)
    db_port = free_port()
    docker("rm", "-f", NAME) if NAME in docker("ps", "-a", "--format", "{{.Names}}") else None
    docker(
        "run",
        "-d",
        "--name",
        NAME,
        "-e",
        f"POSTGRES_PASSWORD={password}",
        "-e",
        "POSTGRES_USER=pg",
        "-e",
        "POSTGRES_DB=drill",
        "-p",
        f"127.0.0.1:{db_port}:5432",
        IMAGE,
    )
    url = f"postgresql://pg:{password}@127.0.0.1:{db_port}/drill"
    t0 = time.monotonic()

    def log(message: str) -> None:
        print(f"[{time.monotonic() - t0:6.1f}s] {message}", flush=True)

    try:
        engine = make_engine(url)

        def db_up() -> bool:
            try:
                with engine.connect():
                    return True
            except Exception:
                return False

        wait_for(db_up, 60)
        os.environ["DATABASE_URL"] = url
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        with session_scope(engine) as s:
            token = create_admin_token(s, "drill")
        log("database container up and migrated")
        api_port = free_port()
        settings = ApiSettings(
            _env_file=None,  # type: ignore[call-arg]
            database_url=url,
            pg_session_secret=secrets.token_urlsafe(32),
            pg_local_storage_dir=ROOT / "artifacts" / "drill-storage",
        )
        server = uvicorn.Server(
            uvicorn.Config(create_app(settings, root=ROOT), port=api_port, log_level="warning")
        )
        threading.Thread(target=server.run, daemon=True).start()
        wait_for(lambda: server.started, 30)
        api = httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=30)
        auth = {"Authorization": f"Bearer {token}"}

        def probe(label: str) -> tuple[int, int]:
            ready = api.get("/readyz")
            builds = api.get("/v1/builds", headers=auth)
            code = (
                builds.json().get("error", {}).get("code", "") if builds.status_code >= 400 else ""
            )
            log(
                f"{label}: /readyz {ready.status_code} {ready.json()['checks'].get('database')}; "
                f"/v1/builds {builds.status_code} {code}".rstrip()
            )
            return ready.status_code, builds.status_code

        assert probe("before") == (200, 200)
        docker("stop", NAME)
        log("database container stopped")
        assert probe("database down") == (503, 503)
        assert probe("database still down") == (503, 503)
        docker("start", NAME)
        log("database container started again")
        waited = wait_for(lambda: api.get("/readyz").status_code == 200, 90)
        log(f"/readyz ready again after {waited:.1f}s, without restarting the API")
        assert probe("after") == (200, 200)
        server.should_exit = True
        log("RESULT: 503 while the database was down; recovered on its own")
    finally:
        docker("rm", "-f", NAME)
        print("drill container removed", flush=True)


if __name__ == "__main__":
    main()
