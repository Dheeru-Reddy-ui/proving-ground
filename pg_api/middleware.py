"""ASGI middleware: request IDs with an access log line, and per-client rate limits."""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pg_api import logs
from pg_core.ratelimit import Bucket, Limit, take

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
MAX_BUCKETS = 10_000

access_log = logs.get("pg_api.access")


class RequestIdMiddleware:
    """Gives every request an ID (the caller's, when well-formed), binds it to the log context,
    returns it in `X-Request-ID` and logs one access line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        rid = incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, rid)
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=rid):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                access_log.info(
                    "request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status,
                    ms=round((time.perf_counter() - started) * 1000, 1),
                )


def client_ip(scope: Scope, trusted_proxy_hops: int) -> str:
    """The client address for rate limiting. Behind N trusted proxies that append to
    X-Forwarded-For, it is the N-th entry from the right; entries further left are whatever the
    client sent and are never trusted."""
    peer = scope.get("client")
    fallback = str(peer[0]) if peer else "unknown"
    if trusted_proxy_hops == 0:
        return fallback
    forwarded = Headers(scope=scope).get("x-forwarded-for", "")
    entries = [e.strip() for e in forwarded.split(",") if e.strip()]
    if len(entries) < trusted_proxy_hops:
        return fallback
    return entries[-trusted_proxy_hops]


def route_class(method: str, path: str) -> str | None:
    """Which limit applies to a request; None for health checks."""
    if path in ("/healthz", "/readyz"):
        return None
    if path.startswith("/v1/webhooks/"):
        return "webhook"
    if (method, path) in (("POST", "/login"), ("POST", "/v1/agents/register")):
        return "login"
    if path.startswith(
        ("/v1/jobs", "/v1/agents", "/v1/artifacts", "/v1/code", "/v1/local-storage")
    ):
        return "agent"
    return "public"


class RateLimitMiddleware:
    """Token buckets per (route class, client IP), in memory (one instance, ADR-0010)."""

    def __init__(
        self,
        app: ASGIApp,
        limits: Mapping[str, Limit],
        trusted_proxy_hops: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.limits = dict(limits)
        self.hops = trusted_proxy_hops
        self.clock = clock
        self.buckets: dict[tuple[str, str], Bucket] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        kind = route_class(scope["method"], scope["path"])
        limit = self.limits.get(kind) if kind else None
        if limit is None or kind is None:
            await self.app(scope, receive, send)
            return
        key = (kind, client_ip(scope, self.hops))
        if len(self.buckets) > MAX_BUCKETS:
            self.buckets.clear()  # bounded memory; at worst a client gets a fresh burst
        decision = take(self.buckets.get(key), limit, self.clock())
        self.buckets[key] = decision.bucket
        if decision.allowed:
            await self.app(scope, receive, send)
            return
        retry = max(1, round(decision.retry_after_s))
        await send_error(
            scope,
            send,
            429,
            "rate_limited",
            f"too many requests; retry after {retry}s",
            [(b"retry-after", str(retry).encode("ascii"))],
        )


class BodyLimitMiddleware:
    """Refuses request bodies over `max_body_size` with a JSON 413: at once when Content-Length
    says so, otherwise as soon as the streamed body passes the limit."""

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_body_size:
            await send_error(scope, send, 413, "content_too_large", "the request body is too large")
            return
        total = 0

        async def limited() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_body_size:
                    raise HTTPException(413, "the request body is too large")
            return message

        await self.app(scope, limited, send)


async def send_error(
    scope: Scope,
    send: Send,
    status: int,
    code: str,
    message: str,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    """A structured error response sent straight from middleware."""
    rid = scope.get("state", {}).get("request_id")
    payload: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": rid}}
    data = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(data)).encode("ascii")),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": data})
