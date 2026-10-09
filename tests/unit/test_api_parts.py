"""API pieces that need no database: client IPs, route classes, storage backends, scrubbing,
settings validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from pg_api.middleware import client_ip, forwarded_count, route_class
from pg_api.settings import ApiSettings, ConfigError, load_settings
from pg_api.storage import LocalStorage, StorageError, SupabaseStorage, check_key
from pg_api.views import scrub

SERVICE_KEY = "service-key-for-tests"


def scope(peer: str = "10.0.0.9", xff: str | None = None) -> dict[str, Any]:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return {"type": "http", "client": (peer, 1234), "headers": headers}


@pytest.mark.parametrize(
    ("hops", "xff", "expected"),
    [
        (0, "1.1.1.1", "10.0.0.9"),  # no trusted proxy: the socket peer
        (1, "6.6.6.6, 2.2.2.2", "2.2.2.2"),  # the client cannot spoof the right-most entry
        (2, "6.6.6.6, 2.2.2.2, 10.1.1.1", "2.2.2.2"),
        (2, "2.2.2.2", "10.0.0.9"),  # fewer entries than hops: fall back to the peer
        (1, None, "10.0.0.9"),
        (1, " , ", "10.0.0.9"),
    ],
)
def test_client_ip(hops: int, xff: str | None, expected: str) -> None:
    assert client_ip(scope(xff=xff), hops) == expected


@pytest.mark.parametrize(
    ("xff", "expected"),
    [(None, 0), ("", 0), ("203.0.113.9", 1), ("1.1.1.1, 203.0.113.9", 2), ("a, , b,", 2)],
)
def test_forwarded_count(xff: str | None, expected: int) -> None:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    assert forwarded_count({"type": "http", "headers": headers}) == expected


def test_client_ip_without_a_peer() -> None:
    assert client_ip({"type": "http", "headers": []}, 0) == "unknown"


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/healthz", None),
        ("GET", "/readyz", None),
        ("POST", "/v1/webhooks/github", "webhook"),
        ("POST", "/login", "login"),
        ("POST", "/v1/agents/register", "login"),
        ("POST", "/v1/jobs/claim", "agent"),
        ("GET", "/v1/apk/abc", "public"),
        ("GET", "/v1/code/abc", "agent"),
        ("GET", "/v1/builds", "public"),
        ("GET", "/", "public"),
    ],
)
def test_route_class(method: str, path: str, expected: str | None) -> None:
    assert route_class(method, path) == expected


@pytest.mark.parametrize(
    "key",
    ["../etc/passwd", "a/../b", "/abs", "a//b", "a/", "", "a b", "a\\b", "x" * 300],
)
def test_bad_storage_keys(key: str) -> None:
    with pytest.raises(StorageError):
        check_key(key)


def test_good_storage_keys() -> None:
    assert check_key("apks/" + "a" * 64 + "/part-000").startswith("apks/")
    assert check_key("artifacts/job-1/try-1/a1/screen.png")


def supabase(handler: Any) -> SupabaseStorage:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SupabaseStorage("https://abc.supabase.co/", SERVICE_KEY, "pg", client=client)


def test_supabase_signed_upload_and_download() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "/object/upload/sign/" in request.url.path:
            return httpx.Response(200, json={"url": "/object/upload/sign/pg/a/b.png?token=T1"})
        if "/object/sign/" in request.url.path:
            return httpx.Response(200, json={"signedURL": "/object/sign/pg/a/b.png?token=T2"})
        return httpx.Response(200 if request.url.path.endswith("/a/b.png") else 404)

    storage = supabase(handler)
    target = storage.upload_target("a/b.png")
    assert target.url == "https://abc.supabase.co/storage/v1/object/upload/sign/pg/a/b.png?token=T1"
    assert (target.method, target.form_field) == ("PUT", "file")
    url = storage.download_url("a/b.png", 300)
    assert url == "https://abc.supabase.co/storage/v1/object/sign/pg/a/b.png?token=T2"
    assert storage.exists("a/b.png")
    assert not storage.exists("a/c.png")
    upload, download = seen[0], seen[1]
    assert upload.method == "POST"
    assert upload.url.path == "/storage/v1/object/upload/sign/pg/a/b.png"
    assert upload.headers["authorization"] == f"Bearer {SERVICE_KEY}"
    assert upload.headers["apikey"] == SERVICE_KEY
    assert json.loads(download.content) == {"expiresIn": 300}
    assert seen[2].method == "HEAD"


def test_supabase_errors_never_carry_the_key() -> None:
    storage = supabase(lambda request: httpx.Response(403, json={"error": "nope"}))
    with pytest.raises(StorageError) as caught:
        storage.upload_target("a/b.png")
    assert SERVICE_KEY not in str(caught.value)
    assert "403" in str(caught.value)
    no_token = supabase(lambda request: httpx.Response(200, json={"url": "/object/x"}))
    with pytest.raises(StorageError, match="no upload token"):
        no_token.upload_target("a/b.png")
    no_url = supabase(lambda request: httpx.Response(200, json={}))
    with pytest.raises(StorageError, match="no signed URL"):
        no_url.download_url("a/b.png", 60)
    not_a_dict = supabase(lambda request: httpx.Response(200, json=[1]))
    with pytest.raises(StorageError, match="unexpected"):
        not_a_dict.download_url("a/b.png", 60)


def test_local_storage_signatures_expire_and_bind_the_operation(tmp_path: Path) -> None:
    now = [1000.0]
    storage = LocalStorage(tmp_path, "http://api/", "secret-secret-secret", lambda: now[0])
    sig = storage.sign("get", "a/b", 1100)
    assert storage.verify("get", "a/b", 1100, sig)
    assert not storage.verify("put", "a/b", 1100, sig)
    assert not storage.verify("get", "a/c", 1100, sig)
    now[0] = 1101
    assert not storage.verify("get", "a/b", 1100, sig)
    assert storage.upload_target("a/b").url.startswith("http://api/v1/local-storage/a/b?op=put&")
    storage.write("a/b", b"x")
    assert storage.exists("a/b")
    assert storage.path("a/b").read_bytes() == b"x"


def test_scrub_removes_home_paths() -> None:
    text = r"C:\Users\dheer\proving_ground\artifacts\x.png and /home/bob/x and /Users/al/y"
    assert scrub(text) == r"~\proving_ground\artifacts\x.png and ~/x and ~/y"
    assert scrub(None) == ""


def test_settings_fail_fast_naming_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(Path(__file__).parent)  # no .env here
    for name in ("DATABASE_URL", "PG_SESSION_SECRET", "PG_STORAGE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError) as caught:
        load_settings()
    message = str(caught.value)
    assert "DATABASE_URL" in message
    assert "PG_SESSION_SECRET" in message
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@y/z")
    monkeypatch.setenv("PG_SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("PG_STORAGE_BACKEND", "supabase")
    with pytest.raises(ConfigError, match="SUPABASE_URL"):
        load_settings()
    monkeypatch.setenv("PG_STORAGE_BACKEND", "local")
    assert isinstance(load_settings(), ApiSettings)
