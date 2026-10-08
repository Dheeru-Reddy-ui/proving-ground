"""Pure pieces of the API: webhook signatures, rate limiting and the APK parts manifest."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pg_core.builds import (
    PART_MAX_BYTES,
    ApkManifest,
    BuildRegistration,
    part_key,
    split_sizes,
)
from pg_core.ratelimit import Limit, take
from pg_core.webhook import WebhookCheck, signature, verify

SECRET = "s3cret-for-tests"  # noqa: S105 (a test value, not a credential)
BODY = b'{"sha256": "abc"}'
NOW = 1_791_460_800.0  # 2026-10-08 12:00 UTC
SHA = "a" * 64


# --- webhook signatures -------------------------------------------------------------------


def check(ts: str | None, sig: str | None, body: bytes = BODY, now: float = NOW) -> WebhookCheck:
    return verify(secret=SECRET, timestamp=ts, signature_header=sig, body=body, now_unix=now)


def test_a_correct_signature_passes() -> None:
    ts = str(int(NOW))
    assert check(ts, signature(SECRET, ts, BODY)) is WebhookCheck.OK
    assert signature(SECRET, ts, BODY).startswith("sha256=")


@pytest.mark.parametrize(
    ("ts_offset", "tamper", "expected"),
    [
        (0, "body", WebhookCheck.BAD_SIGNATURE),  # body changed after signing
        (0, "secret", WebhookCheck.BAD_SIGNATURE),  # forged with another secret
        (0, "timestamp", WebhookCheck.BAD_SIGNATURE),  # timestamp swapped, signature kept
        (301, None, WebhookCheck.STALE),  # replayed more than five minutes later
        (-301, None, WebhookCheck.STALE),  # from the future
        (299, None, WebhookCheck.OK),
    ],
)
def test_tampering_and_replays_are_refused(
    ts_offset: int, tamper: str | None, expected: WebhookCheck
) -> None:
    ts = str(int(NOW))
    sig = signature("other-secret" if tamper == "secret" else SECRET, ts, BODY)
    body = BODY + b" " if tamper == "body" else BODY
    sent_ts = str(int(NOW) - 1) if tamper == "timestamp" else ts
    assert check(sent_ts, sig, body, now=NOW + ts_offset) is expected


@pytest.mark.parametrize(
    ("ts", "sig", "expected"),
    [
        (None, "sha256=00", WebhookCheck.MISSING),
        ("1791460800", None, WebhookCheck.MISSING),
        ("", "sha256=00", WebhookCheck.MISSING),
        ("-1", "sha256=00", WebhookCheck.BAD_TIMESTAMP),
        ("1e9", "sha256=00", WebhookCheck.BAD_TIMESTAMP),
        ("9" * 13, "sha256=00", WebhookCheck.BAD_TIMESTAMP),
        ("1791460800", "sha256=" + "0" * 64, WebhookCheck.BAD_SIGNATURE),
        ("1791460800", "naïve", WebhookCheck.BAD_SIGNATURE),  # non-ASCII header
    ],
)
def test_malformed_headers(ts: str | None, sig: str | None, expected: WebhookCheck) -> None:
    assert check(ts, sig) is expected


def test_an_empty_secret_is_a_configuration_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        verify(secret="", timestamp="1", signature_header="x", body=b"", now_unix=NOW)


# --- rate limiting ------------------------------------------------------------------------


def test_burst_then_refill() -> None:
    limit = Limit(per_minute=60, burst=3)
    bucket = None
    for _ in range(3):
        decision = take(bucket, limit, 100.0)
        assert decision.allowed
        bucket = decision.bucket
    denied = take(bucket, limit, 100.0)
    assert not denied.allowed
    assert denied.retry_after_s == pytest.approx(1.0)
    assert take(denied.bucket, limit, 100.5).allowed is False
    assert take(denied.bucket, limit, 101.0).allowed is True


def test_refill_never_exceeds_the_burst() -> None:
    limit = Limit(per_minute=60, burst=2)
    decision = take(None, limit, 0.0)
    later = take(decision.bucket, limit, 10_000.0)
    assert later.bucket.tokens == pytest.approx(1.0)  # refilled to 2, spent 1


def test_a_clock_going_backwards_adds_nothing() -> None:
    limit = Limit(per_minute=60, burst=1)
    first = take(None, limit, 50.0)
    assert not take(first.bucket, limit, 10.0).allowed


# --- APK parts ----------------------------------------------------------------------------


def test_split_sizes() -> None:
    assert split_sizes(1) == [1]
    assert split_sizes(PART_MAX_BYTES) == [PART_MAX_BYTES]
    assert split_sizes(PART_MAX_BYTES + 1) == [PART_MAX_BYTES, 1]
    size = 64_100_000
    assert sum(split_sizes(size)) == size
    assert all(s <= PART_MAX_BYTES for s in split_sizes(size))
    with pytest.raises(ValueError, match="empty"):
        split_sizes(0)


def manifest(sizes: list[int], **overrides: object) -> dict[str, object]:
    parts = [
        {"index": i, "key": part_key(SHA, i), "sha256": "b" * 64, "size": s}
        for i, s in enumerate(sizes)
    ]
    return {"sha256": SHA, "size": sum(sizes), "parts": parts, **overrides}


def test_a_consistent_manifest_is_accepted() -> None:
    m = ApkManifest.model_validate(manifest([PART_MAX_BYTES, 100]))
    assert m.parts[1].key == f"apks/{SHA}/part-001"


@pytest.mark.parametrize(
    "bad",
    [
        manifest([10, 10], size=21),  # sizes do not add up
        manifest([PART_MAX_BYTES + 1]),  # a part over the storage limit
        {**manifest([10, 10]), "parts": list(reversed(manifest([10, 10])["parts"]))},  # type: ignore[call-overload]
        manifest([10], sha256="c" * 64),  # keys belong to another APK
        manifest([]),  # no parts
        manifest([10], sha256="A" * 64),  # not lower-case hex
    ],
)
def test_inconsistent_manifests_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ApkManifest.model_validate(bad)


def test_build_registration() -> None:
    reg = BuildRegistration.model_validate(
        {"sha256": SHA, "label": "build 3", "locator_tag": "87d396162a05", "apk": manifest([5])}
    )
    assert reg.patch_notes == ""
    with pytest.raises(ValidationError):  # the manifest is for another APK
        BuildRegistration.model_validate(
            {"sha256": "d" * 64, "label": "x", "locator_tag": "87d396162a05", "apk": manifest([5])}
        )
    with pytest.raises(ValidationError):  # unknown field
        BuildRegistration.model_validate(
            {"sha256": SHA, "label": "x", "locator_tag": "87d396162a05", "url": "http://x"}
        )
    with pytest.raises(ValidationError):  # not a locator tag
        BuildRegistration.model_validate({"sha256": SHA, "label": "x", "locator_tag": "../../etc"})
