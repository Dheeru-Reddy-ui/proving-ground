"""Runtime settings, read from the environment and the local `.env` (documented in .env.example)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Game and device
    pg_game_project_dir: Path | None = None
    pg_apk_path: Path | None = None
    pg_android_package: str | None = None
    pg_alttester_host: str = "127.0.0.1"
    pg_alttester_port: int = 13000
    pg_adb_serial: str | None = None

    # Persistence
    database_url: SecretStr | None = None

    # LLM
    pg_llm_provider: str | None = None
    pg_llm_model: str | None = None
    pg_llm_api_key: SecretStr | None = None
    pg_max_cost_per_run_usd: Decimal | None = None
    # Prices per million tokens from the provider's pricing page; 0 on a free tier.
    pg_llm_input_usd_per_mtok: Decimal = Decimal(0)
    pg_llm_output_usd_per_mtok: Decimal = Decimal(0)
    pg_llm_temperature: float = 0.4
    pg_llm_max_output_tokens: int = 16000
    pg_llm_timeout_s: float = 180.0
