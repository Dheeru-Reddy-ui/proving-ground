"""Admin login and logout (form posts from the dashboard). The password is checked against the
argon2 hash in `PG_ADMIN_PASSWORD_HASH`; the session cookie is signed, HttpOnly and SameSite."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from pg_api import auth, logs
from pg_api.errors import ApiError
from pg_api.state import AppState, app_state

router = APIRouter(tags=["session"])
log = logs.get("pg_api.session")
State = Annotated[AppState, Depends(app_state)]


@router.post("/login")
def login(
    password: Annotated[str, Form(max_length=200)], request: Request, state: State
) -> RedirectResponse:
    stored = state.settings.pg_admin_password_hash
    if stored is None:
        raise ApiError(503, "login_disabled", "PG_ADMIN_PASSWORD_HASH is not configured")
    if not auth.password_ok(stored.get_secret_value(), password):
        log.warning("admin_login_failed")
        raise ApiError(401, "unauthorized", "wrong password")
    auth.login(request)
    log.info("admin_login")
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(
    request: Request, csrf_token: Annotated[str, Form(max_length=200)] = ""
) -> RedirectResponse:
    if auth.session_admin(request) is not None:
        auth.check_csrf(request, csrf_token or request.headers.get(auth.CSRF_HEADER))
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/session")
def current_session(request: Request) -> dict[str, object]:
    """Whether this browser is logged in, and its CSRF token for same-origin scripts."""
    if auth.session_admin(request) is None:
        return {"admin": False}
    return {"admin": True, "csrf_token": request.session.get("csrf")}
