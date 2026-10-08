"""API configuration from the environment (and `.env` locally). Validated at start-up: a missing
or invalid variable stops the process with its name, never a half-configured service."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    database_url: SecretStr
    # Signs the admin session cookie; at least 32 random characters.
    pg_session_secret: SecretStr = Field(min_length=32)
    # argon2 hash of the admin password (`pg api hash-password`); unset = admin login disabled.
    pg_admin_password_hash: SecretStr | None = None
    # Shared HMAC secret of the build-registration webhook; unset = the webhook answers 503.
    pg_webhook_secret: SecretStr | None = None

    pg_public_demo: bool = False  # read-only pages without login, no artifacts, no actions
    pg_secure_cookies: bool = True  # False only for local http development
    # Proxies in front of the API that append to X-Forwarded-For. 0 = use the socket peer. The
    # client IP for rate limiting is the entry this many places from the right (never the
    # left-most, which the client controls).
    pg_trusted_proxy_hops: int = Field(default=0, ge=0, le=5)

    pg_storage_backend: Literal["local", "supabase"] = "local"
    pg_local_storage_dir: Path = Path("artifacts/storage")
    pg_public_base_url: str = "http://127.0.0.1:8000"  # how agents reach this API
    supabase_url: str | None = None
    supabase_service_key: SecretStr | None = None
    pg_storage_bucket: str = "proving-ground"

    pg_max_body_bytes: int = Field(default=2_000_000, ge=10_000)
    pg_rate_public_per_min: float = Field(default=120, gt=0)
    pg_rate_webhook_per_min: float = Field(default=30, gt=0)
    pg_rate_login_per_min: float = Field(default=10, gt=0)
    pg_rate_agent_per_min: float = Field(default=600, gt=0)

    pg_lease_ttl_s: float = Field(default=120, ge=10)
    pg_run_timeout_s: float = Field(default=300, gt=0)  # per RUN_TEST attempt
    pg_max_candidates: int = Field(default=12, ge=1)  # largest n a validation may request

    pg_embedded_worker: bool = False  # run the pg_worker loop inside this process (ADR-0010)

    @model_validator(mode="after")
    def _storage_complete(self) -> Self:
        if self.pg_storage_backend == "supabase" and (
            not self.supabase_url or self.supabase_service_key is None
        ):
            raise ValueError(
                "PG_STORAGE_BACKEND=supabase needs SUPABASE_URL and SUPABASE_SERVICE_KEY"
            )
        return self


class ConfigError(SystemExit):
    """Start-up configuration problem; the message names every variable at fault."""


def load_settings() -> ApiSettings:
    try:
        return ApiSettings()  # type: ignore[call-arg]
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            name = ".".join(str(p) for p in error["loc"]).upper() or "settings"
            problems.append(f"{name}: {error['msg']}")
        raise ConfigError("invalid API configuration:\n  " + "\n  ".join(problems)) from None
