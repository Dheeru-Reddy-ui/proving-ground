"""Settings must match .env.example exactly, so the documented config never drifts."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from pg_api.settings import ApiSettings
from pg_cli.settings import Settings
from pg_worker.settings import WorkerSettings


def example_keys() -> set[str]:
    keys = set()
    for line in Path(".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            keys.add(stripped.split("=", 1)[0])
    return keys


def test_env_example_documents_every_setting_and_nothing_else() -> None:
    fields = {*Settings.model_fields, *ApiSettings.model_fields, *WorkerSettings.model_fields}
    assert example_keys() == {name.upper() for name in fields}


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_ALTTESTER_PORT", "13001")
    monkeypatch.setenv("PG_ADB_SERIAL", "SER")
    monkeypatch.setenv("PG_MAX_COST_PER_RUN_USD", "1.50")
    monkeypatch.setenv("PG_LLM_API_KEY", "not-a-real-key")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.pg_alttester_port == 13001
    assert settings.pg_adb_serial == "SER"
    assert settings.pg_max_cost_per_run_usd == Decimal("1.50")
    assert "not-a-real-key" not in repr(settings)  # secrets are masked


def test_empty_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_ADB_SERIAL", "")
    monkeypatch.setenv("PG_APK_PATH", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.pg_adb_serial is None
    assert settings.pg_apk_path is None
    assert settings.pg_alttester_host == "127.0.0.1"
