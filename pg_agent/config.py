"""The agent's credentials (`pg agent enroll`), kept in the user's config directory in a file
only this Windows user can read (ADR-0005). The token never goes into `.env` or the repo."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import platformdirs
from pydantic import BaseModel, ConfigDict, SecretStr

APP_NAME = "proving-ground"
CONFIG_FILE = "agent.json"


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_url: str
    name: str
    token: SecretStr


def config_path() -> Path:
    return platformdirs.user_config_path(APP_NAME, appauthor=False) / CONFIG_FILE


def windows_user() -> str:
    domain, user = os.environ.get("USERDOMAIN"), os.environ.get("USERNAME")
    if not user:
        raise RuntimeError("USERNAME is not set; cannot restrict the credentials file")
    return f"{domain}\\{user}" if domain else user


def restrict(path: Path) -> None:
    """Only the current user may read or write `path`: on Windows, drop inherited ACEs and grant
    this user full control (`icacls /inheritance:r /grant:r USER:F`); elsewhere, mode 600."""
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - fixed argv, never a shell
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{windows_user()}:F"],  # noqa: S607
            check=True,
            capture_output=True,
            timeout=30,
        )
    else:
        path.chmod(0o600)


def save(config: AgentConfig, path: Path | None = None) -> Path:
    """Write the credentials: the file is created empty and restricted before the token is
    written, so the token never sits in a readable file."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    restrict(target)
    data = {
        "api_url": config.api_url,
        "name": config.name,
        "token": config.token.get_secret_value(),
    }
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return target


def load(path: Path | None = None) -> AgentConfig:
    source = path or config_path()
    if not source.is_file():
        raise FileNotFoundError(f"no agent credentials at {source}; run `pg agent enroll` first")
    return AgentConfig.model_validate(json.loads(source.read_text(encoding="utf-8")))
