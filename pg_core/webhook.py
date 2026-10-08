"""Build-registration webhook signatures (HMAC-SHA256 with a timestamp), as pure functions.

The sender (our release workflow) signs `"<timestamp>.<raw body>"` with the shared secret and
sends `X-PG-Timestamp: <unix seconds>` and `X-PG-Signature: sha256=<hex>`. The receiver
recomputes the signature over the **raw** body, compares in constant time, and refuses
timestamps more than five minutes from its clock, so a captured request cannot be replayed
later. Within the window, replays are stopped by the delivery-id table and by build
registration being idempotent on the APK hash.
"""

from __future__ import annotations

import hashlib
import hmac
from enum import StrEnum

TOLERANCE_S = 300
PREFIX = "sha256="


class WebhookCheck(StrEnum):
    OK = "ok"
    MISSING = "missing_header"
    BAD_TIMESTAMP = "bad_timestamp"
    STALE = "stale_timestamp"  # outside the tolerance window: possibly a replay
    BAD_SIGNATURE = "bad_signature"


def signature(secret: str, timestamp: str, body: bytes) -> str:
    message = timestamp.encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return PREFIX + digest


def verify(
    *,
    secret: str,
    timestamp: str | None,
    signature_header: str | None,
    body: bytes,
    now_unix: float,
    tolerance_s: int = TOLERANCE_S,
) -> WebhookCheck:
    if not secret:
        raise ValueError("the webhook secret is empty")
    if not timestamp or not signature_header:
        return WebhookCheck.MISSING
    if not timestamp.isdigit() or len(timestamp) > 12:
        return WebhookCheck.BAD_TIMESTAMP
    if abs(now_unix - int(timestamp)) > tolerance_s:
        return WebhookCheck.STALE
    expected = signature(secret, timestamp, body)
    if not hmac.compare_digest(expected.encode("ascii"), signature_header.encode("utf-8")):
        return WebhookCheck.BAD_SIGNATURE
    return WebhookCheck.OK
