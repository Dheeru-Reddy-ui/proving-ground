"""The CLI's path to the control plane (Phase 2): upload an APK in parts, register its build and
start validations through the API with an admin token (`PG_API_URL`, `PG_API_TOKEN`)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from pg_agent.client import AgentApi
from pg_core.builds import ApkManifest, ApkPart, BuildRegistration, part_key, split_sizes

UPLOAD_TIMEOUT_S = 600.0


def apk_manifest(apk: Path, part_max: int | None = None) -> ApkManifest:
    """Hash the APK and each part it is cut into (ADR-0010: at most 45 MB per part)."""
    size = apk.stat().st_size
    sizes = split_sizes(size) if part_max is None else split_sizes(size, part_max)
    whole = hashlib.sha256()
    parts_sha = []
    with apk.open("rb") as handle:
        for part_size in sizes:
            chunk = handle.read(part_size)
            whole.update(chunk)
            parts_sha.append(hashlib.sha256(chunk).hexdigest())
    sha = whole.hexdigest()
    parts = tuple(
        ApkPart(index=i, key=part_key(sha, i), sha256=h, size=n)
        for i, (h, n) in enumerate(zip(parts_sha, sizes, strict=True))
    )
    return ApkManifest(sha256=sha, size=size, parts=parts)


class AdminApi(AgentApi):
    """The same retrying client as the agent's, with an admin token and admin endpoints."""

    def upload_urls(self, manifest: ApkManifest) -> list[dict[str, Any]]:
        answer = self._call("POST", "/v1/apk/upload-urls", manifest.model_dump(mode="json"))
        parts: list[dict[str, Any]] = answer.json()["parts"]
        return parts

    def register_build(self, registration: BuildRegistration) -> dict[str, Any]:
        data: dict[str, Any] = self._call(
            "POST", "/v1/builds", registration.model_dump(mode="json")
        ).json()
        return data

    def validate(
        self, build_id: int, features: list[str], n: int, idempotency_key: str | None
    ) -> dict[str, Any]:
        body = {"features": features, "n": n, "idempotency_key": idempotency_key}
        data: dict[str, Any] = self._call("POST", f"/v1/builds/{build_id}/validate", body).json()
        return data


def publish_build(
    api: AdminApi,
    apk: Path,
    *,
    label: str,
    locator_tag: str,
    patch_notes: str = "",
    echo: Callable[[str], Any] = print,
    part_max: int | None = None,
    storage_client: httpx.Client | None = None,
    register: bool = True,
) -> dict[str, Any]:
    """Upload the APK's missing parts, then register the build. Safe to repeat. Parts go to
    the signed URLs with a client that never carries the admin token."""
    manifest = apk_manifest(apk, part_max)
    targets = api.upload_urls(manifest)
    storage_client = storage_client or httpx.Client(timeout=UPLOAD_TIMEOUT_S)
    with apk.open("rb") as handle, storage_client as storage:
        for part, target in zip(manifest.parts, targets, strict=True):
            handle.seek(sum(p.size for p in manifest.parts[: part.index]))
            data = handle.read(part.size)
            if target["exists"]:
                echo(f"  part {part.index}: already uploaded")
                continue
            upload = target["upload"]
            response = storage.request(
                upload["method"],
                upload["url"],
                files={upload["form_field"]: (f"part-{part.index}", data)},
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"upload of part {part.index} failed: HTTP {response.status_code}"
                )
            echo(f"  part {part.index}: uploaded {part.size:,} bytes")
    registration = BuildRegistration(
        sha256=manifest.sha256,
        label=label,
        locator_tag=locator_tag,
        patch_notes=patch_notes,
        apk=manifest,
    )
    if not register:  # the webhook path: the release workflow registers it
        return registration.model_dump(mode="json")
    return api.register_build(registration)
