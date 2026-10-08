"""Starting the worker: standalone (`python -m pg_worker`, `pg worker run`) or embedded in the
API process as a supervised thread (ADR-0010)."""

from __future__ import annotations

import random
import signal
import threading
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from sqlalchemy import Engine

from pg_api import logs
from pg_api.state import AppState, utc_now
from pg_db.session import make_engine
from pg_worker.handlers import WorkerContext
from pg_worker.loop import WorkerLoop
from pg_worker.settings import WorkerSettings, load_worker_settings

log = logs.get("pg_worker")
RESTART_DELAY_S = 10.0


def make_context(settings: WorkerSettings, engine: Engine, root: Path = Path()) -> WorkerContext:
    rng = random.Random()  # noqa: S311 - jitter, not security
    return WorkerContext(
        settings=settings, engine=engine, now=utc_now, jitter=lambda: rng.uniform(0, 2), root=root
    )


def run_standalone() -> None:
    settings = load_worker_settings()
    logs.configure()
    loop = WorkerLoop(make_context(settings, make_engine(settings.database_url.get_secret_value())))

    def stop(signum: int, _frame: FrameType | None) -> None:
        log.info("worker_signal", signal=signum)
        loop.stop()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    loop.run_forever()


def embedded_worker(state: AppState) -> Callable[[], None]:
    """Lifespan hook: run the worker loop in a supervised daemon thread of the API process,
    restarting it after a crash. Returns the function that stops it."""
    loop = WorkerLoop(make_context(load_worker_settings(), state.engine, state.root))
    stopping = threading.Event()

    def supervise() -> None:
        while not stopping.is_set():
            try:
                loop.run_forever()
            except Exception as exc:  # restart instead of leaving the API without a worker
                log.error("embedded_worker_crashed", exc_info=exc)
                stopping.wait(RESTART_DELAY_S)

    thread = threading.Thread(target=supervise, name="pg-worker", daemon=True)
    thread.start()
    state.worker_alive = thread.is_alive

    def stop() -> None:
        stopping.set()
        loop.stop()
        thread.join(timeout=30)

    return stop
