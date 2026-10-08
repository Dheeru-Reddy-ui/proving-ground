"""Worker configuration from the environment (and `.env` locally), validated at start-up."""

from __future__ import annotations

import socket
from decimal import Decimal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from pg_core.budget import Budgets


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    database_url: SecretStr

    pg_llm_provider: str | None = None
    pg_llm_model: str | None = None
    pg_llm_api_key: SecretStr | None = None
    pg_llm_input_usd_per_mtok: Decimal = Decimal(0)
    pg_llm_output_usd_per_mtok: Decimal = Decimal(0)
    pg_llm_temperature: float = 0.4
    pg_llm_max_output_tokens: int = 16000
    pg_llm_timeout_s: float = 180.0

    pg_max_cost_per_run_usd: Decimal | None = None  # None: 1.00, as `pg generate`
    pg_max_cost_per_build_usd: Decimal = Decimal("2.00")
    pg_max_cost_per_day_usd: Decimal = Decimal("5.00")
    # Kill switch: false leaves GENERATE jobs queued (the dashboard says why); scoring goes on.
    pg_generation_enabled: bool = True

    pg_worker_name: str = Field(default_factory=lambda: f"worker-{socket.gethostname()}"[:64])
    pg_worker_poll_s: float = Field(default=2.0, gt=0)
    pg_lease_ttl_s: float = Field(default=120, ge=10)

    def budgets(self) -> Budgets:
        return Budgets(
            per_run_usd=self.pg_max_cost_per_run_usd or Decimal("1.00"),
            per_build_usd=self.pg_max_cost_per_build_usd,
            per_day_usd=self.pg_max_cost_per_day_usd,
        )

    def llm_configured(self) -> bool:
        return bool(self.pg_llm_provider and self.pg_llm_model and self.pg_llm_api_key)


class WorkerConfigError(SystemExit):
    pass


def load_worker_settings() -> WorkerSettings:
    try:
        return WorkerSettings()  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in e['loc']).upper()}: {e['msg']}" for e in exc.errors()
        ]
        raise WorkerConfigError(
            "invalid worker configuration:\n  " + "\n  ".join(problems)
        ) from None
