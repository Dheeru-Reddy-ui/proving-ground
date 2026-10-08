"""Authentication (M2.2).

- **Agents** and the **CLI** send `Authorization: Bearer <token>`. Tokens are 256 random bits,
  shown once, and stored only as their sha256; `agent` tokens reach agent endpoints only,
  `admin` tokens reach admin endpoints. Revoking a token (or its agent) ends its access at once.
- **Enrolment tokens** are one-time and expire; `pg agent enroll` trades one for an agent token.
- **The admin dashboard** logs in with a password checked against an argon2 hash from the
  environment, gets a signed session cookie (HttpOnly, SameSite=Strict, Secure in production)
  and a CSRF token that every state-changing request must echo.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from pg_api.errors import ApiError
from pg_db.models import Agent, ApiToken, EnrolmentToken

TOKEN_BYTES = 32
LAST_USED_RESOLUTION = timedelta(minutes=1)  # avoid a write on every request
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_hasher = PasswordHasher()


def new_token(prefix: str) -> tuple[str, str]:
    """(plain token shown once, sha256 stored)."""
    plain = f"{prefix}_{secrets.token_urlsafe(TOKEN_BYTES)}"
    return plain, token_sha(plain)


def token_sha(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def password_ok(stored_hash: str | None, password: str) -> bool:
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["admin", "agent"]
    name: str
    via: Literal["token", "session"]
    agent_id: int | None = None


def bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def token_principal(
    session: Session, plain: str, kind: Literal["admin", "agent"], now: datetime
) -> Principal | None:
    row = session.execute(
        select(ApiToken).where(
            ApiToken.token_sha256 == token_sha(plain),
            ApiToken.kind == kind,
            ApiToken.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    name = row.name
    if row.agent_id is not None:
        agent = session.get(Agent, row.agent_id)
        if agent is None or agent.revoked_at is not None:
            return None
        name = agent.name
    if row.last_used_at is None or now - row.last_used_at > LAST_USED_RESOLUTION:
        row.last_used_at = now
    return Principal(kind=kind, name=name, via="token", agent_id=row.agent_id)


def require_token(
    session: Session, request: Request, kind: Literal["admin", "agent"], now: datetime
) -> Principal:
    plain = bearer(request)
    principal = token_principal(session, plain, kind, now) if plain else None
    if principal is None:
        raise ApiError(
            401,
            "unauthorized",
            f"a valid {kind} token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def session_admin(request: Request) -> Principal | None:
    if "session" not in request.scope or not request.session.get("admin"):
        return None
    return Principal(kind="admin", name="admin", via="session")


def check_csrf(request: Request, sent: str | None) -> None:
    expected = request.session.get("csrf") if "session" in request.scope else None
    if not expected or not sent or not hmac.compare_digest(str(expected), sent):
        raise ApiError(403, "csrf_failed", "missing or wrong CSRF token")


def require_admin(session: Session, request: Request, now: datetime) -> Principal:
    """An admin bearer token, or the admin session (with the CSRF header on unsafe methods)."""
    if bearer(request) is not None:
        return require_token(session, request, "admin", now)
    principal = session_admin(request)
    if principal is None:
        raise ApiError(401, "unauthorized", "admin login or an admin token is required")
    if request.method in UNSAFE_METHODS:
        check_csrf(request, request.headers.get(CSRF_HEADER))
    return principal


def login(request: Request) -> str:
    """Start an admin session; returns its CSRF token."""
    request.session.clear()
    csrf = secrets.token_urlsafe(TOKEN_BYTES)
    request.session.update({"admin": True, "csrf": csrf})
    return csrf


# --- enrolment ----------------------------------------------------------------------------


def create_enrolment_token(session: Session, now: datetime, ttl: timedelta) -> str:
    plain, sha = new_token("pge")
    session.add(EnrolmentToken(token_sha256=sha, created_at=now, expires_at=now + ttl))
    session.flush()
    return plain


def create_admin_token(session: Session, name: str) -> str:
    plain, sha = new_token("pga")
    session.add(ApiToken(kind="admin", name=name, token_sha256=sha))
    session.flush()
    return plain


def enrol_agent(
    session: Session,
    *,
    enrolment_token: str,
    name: str,
    capabilities: dict[str, object],
    now: datetime,
) -> tuple[Agent, str]:
    """Use a one-time enrolment token to create an agent and its bearer token."""
    row = session.execute(
        select(EnrolmentToken)
        .where(EnrolmentToken.token_sha256 == token_sha(enrolment_token))
        .with_for_update()
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at < now:
        raise ApiError(401, "unauthorized", "the enrolment token is invalid, used or expired")
    existing = session.execute(select(Agent).where(Agent.name == name)).scalar_one_or_none()
    if existing is not None:
        raise ApiError(409, "conflict", f"an agent named {name!r} already exists")
    agent = Agent(name=name, capabilities=capabilities, health={}, created_at=now)
    session.add(agent)
    session.flush()
    plain, sha = new_token("pgt")
    session.add(ApiToken(kind="agent", name=name, token_sha256=sha, agent_id=agent.id))
    row.used_at = now
    row.agent_id = agent.id
    session.flush()
    return agent, plain


def rotate_agent_token(session: Session, agent: Agent, now: datetime) -> str:
    """Revoke every token of the agent and issue a new one (RUNBOOK: token rotation)."""
    for token in session.execute(
        select(ApiToken).where(ApiToken.agent_id == agent.id, ApiToken.revoked_at.is_(None))
    ).scalars():
        token.revoked_at = now
    plain, sha = new_token("pgt")
    session.add(ApiToken(kind="agent", name=agent.name, token_sha256=sha, agent_id=agent.id))
    session.flush()
    return plain
