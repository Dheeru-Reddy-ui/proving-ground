"""Token-bucket rate limiting as a pure function (time passed in).

The API keeps one bucket per (route class, client) in memory. That is enough on one instance
(ADR-0010); several instances would each allow the full rate.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Limit(BaseModel):
    model_config = ConfigDict(frozen=True)

    per_minute: float = Field(gt=0)
    burst: int = Field(ge=1)


class Bucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    tokens: float
    updated: float  # monotonic seconds


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    bucket: Bucket
    retry_after_s: float = 0.0


def take(bucket: Bucket | None, limit: Limit, now: float) -> Decision:
    """Refill at `per_minute / 60` tokens a second up to `burst`, then spend one token."""
    rate = limit.per_minute / 60.0
    if bucket is None:
        tokens = float(limit.burst)
    else:
        tokens = min(float(limit.burst), bucket.tokens + max(0.0, now - bucket.updated) * rate)
    if tokens >= 1.0:
        return Decision(allowed=True, bucket=Bucket(tokens=tokens - 1.0, updated=now))
    return Decision(
        allowed=False,
        bucket=Bucket(tokens=tokens, updated=now),
        retry_after_s=(1.0 - tokens) / rate,
    )
