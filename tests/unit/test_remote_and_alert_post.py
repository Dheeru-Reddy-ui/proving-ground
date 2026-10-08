"""The CLI's upload of an APK in parts through the API, and the alert webhook poster."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from pg_cli.remote import AdminApi, apk_manifest, publish_build
from pg_worker.alerts import webhook_poster


def test_the_manifest_hashes_every_part_and_the_whole_file(tmp_path: Path) -> None:
    apk = tmp_path / "game.apk"
    data = bytes(range(256)) * 100  # 25,600 bytes
    apk.write_bytes(data)
    manifest = apk_manifest(apk, part_max=10_000)
    assert manifest.sha256 == hashlib.sha256(data).hexdigest()
    assert [p.size for p in manifest.parts] == [10_000, 10_000, 5_600]
    assert manifest.parts[2].sha256 == hashlib.sha256(data[20_000:]).hexdigest()


def test_publish_uploads_only_missing_parts_then_registers(tmp_path: Path) -> None:
    apk = tmp_path / "game.apk"
    data = b"x" * 25_000
    apk.write_bytes(data)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/v1/apk/upload-urls":
            parts = json.loads(request.content)["parts"]
            return httpx.Response(
                200,
                json={
                    "parts": [
                        {
                            "index": p["index"],
                            "key": p["key"],
                            "exists": p["index"] == 0,
                            "upload": None
                            if p["index"] == 0
                            else {
                                "url": f"https://storage.test/{p['index']}",
                                "method": "PUT",
                                "form_field": "file",
                            },
                        }
                        for p in parts
                    ]
                },
            )
        if path == "/v1/builds":
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"build": {"id": 3, "sha256": body["sha256"]}, "created": True}
            )
        return httpx.Response(200, json={"Key": path})

    transport = httpx.MockTransport(handler)
    api = AdminApi("https://api.test", "pga_token", transport=transport)
    lines: list[str] = []
    answer: dict[str, Any] = publish_build(
        api,
        apk,
        label="build 3",
        locator_tag="87d396162a05",
        echo=lines.append,
        part_max=10_000,
        storage_client=httpx.Client(transport=transport),
    )
    assert answer["created"] is True
    uploads = [r for r in seen if r.url.host == "storage.test"]
    assert [r.url.path for r in uploads] == ["/1", "/2"]  # part 0 was already stored
    assert all("authorization" not in r.headers for r in uploads)  # the admin token stays home
    registration = json.loads(next(r for r in seen if r.url.path == "/v1/builds").content)
    assert registration["apk"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert lines[0] == "  part 0: already uploaded"


def test_the_alert_poster_retries_then_gives_up() -> None:
    calls: list[dict[str, str]] = []
    answers = iter([httpx.Response(500), httpx.Response(204)])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return next(answers)

    slept: list[float] = []
    post = webhook_poster(
        "https://hooks.test/x", httpx.Client(transport=httpx.MockTransport(handler)), slept.append
    )
    assert post("job 4 died") is True
    assert calls == [{"text": "job 4 died", "content": "job 4 died"}] * 2
    assert slept == [2.0]
    down = webhook_poster(
        "https://hooks.test/x",
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))),
        lambda s: None,
    )
    assert down("x") is False
