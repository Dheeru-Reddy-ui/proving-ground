"""Structured error responses: every error is `{"error": {"code", "message", "request_id"}}`.

Validation errors never echo the submitted values (they may hold tokens). Database connection
failures answer 503 so clients retry and `/readyz` can recover without a restart.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from pg_api import logs
from pg_db.diagnostics import db_reason

log = logs.get("pg_api.errors")

STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "content_too_large",
    415: "unsupported_media_type",
    422: "invalid_request",
    429: "rate_limited",
    503: "unavailable",
}


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def body(code: str, message: str, rid: str | None, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": rid, **extra}}


def respond(request: Request, status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(body(code, message, request_id(request), **extra), status_code=status)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        response = respond(request, exc.status, exc.code, exc.message)
        response.headers.update(exc.headers)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = STATUS_CODES.get(exc.status_code, "error")
        response = respond(request, exc.status_code, code, str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": str(e.get("msg", ""))}
            for e in exc.errors()
        ]
        return respond(request, 422, "invalid_request", "the request is invalid", problems=problems)

    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    async def database_down(request: Request, exc: Exception) -> JSONResponse:
        log.warning("database_unavailable", error=type(exc).__name__, reason=db_reason(exc))
        return respond(request, 503, "database_unavailable", "the database is unavailable")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", path=request.url.path, exc_info=exc)
        return respond(request, 500, "internal_error", "internal error")
