"""What every route needs, created once per app: settings, the database engine, storage, the
clock and paths into the repository (specs, SDK manifest, locator maps). Tests swap any of them."""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine

from pg_api.settings import ApiSettings
from pg_api.storage import LocalStorage, Storage, SupabaseStorage
from pg_db.sync import spec_features

MANIFEST = Path("pg_sdk/manifest.json")
LOCATORS = Path("pg_sdk/locators")


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class AppState:
    settings: ApiSettings
    engine: Engine
    storage: Storage
    root: Path = Path()
    now: Callable[[], datetime] = utc_now
    jitter: Callable[[], float] = field(default=lambda: random.uniform(0.0, 5.0))  # noqa: S311
    worker_alive: Callable[[], bool] | None = None  # set when the worker is embedded

    def manifest_sha(self) -> str:
        return hashlib.sha256((self.root / MANIFEST).read_bytes()).hexdigest()

    def features(self) -> list[str]:
        return spec_features(self.root)

    def has_locators(self, tag: str) -> bool:
        return (self.root / LOCATORS / f"{tag}.yaml").is_file()


def make_storage(settings: ApiSettings, clock: Callable[[], float] = time.time) -> Storage:
    if settings.pg_storage_backend == "supabase":
        if settings.supabase_url is None or settings.supabase_service_key is None:
            raise ValueError("Supabase storage needs SUPABASE_URL and SUPABASE_SERVICE_KEY")
        return SupabaseStorage(
            settings.supabase_url,
            settings.supabase_service_key.get_secret_value(),
            settings.pg_storage_bucket,
        )
    return LocalStorage(
        settings.pg_local_storage_dir,
        settings.pg_public_base_url,
        settings.pg_session_secret.get_secret_value(),
        clock,
    )


def app_state(request: Request) -> AppState:
    state: AppState = request.app.state.pg
    return state
