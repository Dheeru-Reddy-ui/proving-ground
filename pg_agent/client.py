"""The agent's HTTP client for the control plane (outbound HTTPS only, ADR-0005).

Every request has a timeout. Connection errors, 429 and 5xx are retried with exponential backoff
and jitter for up to `max_wait_s` (long enough for a free Render service to wake up). Signed
storage URLs are fetched with a separate client that never sends the agent's token.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

REQUEST_TIMEOUT_S = 30.0
TRANSFER_TIMEOUT_S = 300.0
RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0
DEFAULT_MAX_WAIT_S = 180.0


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class LeaseLost(ApiError):
    """409 from a lease operation: the job is no longer this agent's."""


class Unreachable(RuntimeError):
    """The API did not answer within `max_wait_s` of retries."""


class ClaimedJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: int
    type: str
    payload: dict[str, Any]
    lease_token: str
    attempts: int
    max_attempts: int


class AgentApi:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        max_wait_s: float = DEFAULT_MAX_WAIT_S,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=REQUEST_TIMEOUT_S,
            transport=transport,
        )
        self.storage = httpx.Client(timeout=TRANSFER_TIMEOUT_S, transport=transport)
        self.sleep = sleep
        self.rng = rng or random.Random()  # noqa: S311 - jitter, not security
        self.max_wait_s = max_wait_s

    def close(self) -> None:
        self.http.close()
        self.storage.close()

    def _request(self, method: str, path: str, json: Any = None) -> httpx.Response:
        waited = 0.0
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.http.request(method, path, json=json)
            except httpx.TransportError as exc:
                problem = f"{type(exc).__name__}"
            else:
                if response.status_code not in RETRY_STATUS:
                    return response
                problem = f"HTTP {response.status_code}"
            delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempt - 1))
            delay += self.rng.uniform(0, BACKOFF_BASE_S)
            if waited + delay > self.max_wait_s:
                raise Unreachable(f"{method} {path}: {problem} after {attempt} attempt(s)")
            self.sleep(delay)
            waited += delay

    def _call(
        self, method: str, path: str, json: Any = None, *, lease: bool = False
    ) -> httpx.Response:
        response = self._request(method, path, json)
        if response.status_code < 400:
            return response
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        code = str(error.get("code", "error"))
        message = str(error.get("message", response.text[:200]))
        if lease and response.status_code == 409:
            raise LeaseLost(409, code, message)
        raise ApiError(response.status_code, code, message)

    # --- endpoints ------------------------------------------------------------------------

    def register(
        self, enrolment_token: str, name: str, capabilities: dict[str, Any]
    ) -> dict[str, Any]:
        body = {"enrolment_token": enrolment_token, "name": name, "capabilities": capabilities}
        data: dict[str, Any] = self._call("POST", "/v1/agents/register", body).json()
        return data

    def status(
        self,
        *,
        healthy: bool,
        checks: list[dict[str, Any]],
        capabilities: dict[str, Any],
        current_job_id: int | None = None,
    ) -> None:
        body = {
            "healthy": healthy,
            "checks": checks,
            "capabilities": capabilities,
            "current_job_id": current_job_id,
        }
        self._call("POST", "/v1/agents/status", body)

    def claim(
        self, types: list[str], capabilities: dict[str, Any], installed_build_sha: str | None
    ) -> ClaimedJob | None:
        body = {
            "types": types,
            "capabilities": capabilities,
            "installed_build_sha": installed_build_sha,
        }
        response = self._call("POST", "/v1/jobs/claim", body)
        if response.status_code == 204:
            return None
        return ClaimedJob.model_validate(response.json())

    def heartbeat(self, job_id: int, token: str) -> None:
        self._call("POST", f"/v1/jobs/{job_id}/heartbeat", {"lease_token": token}, lease=True)

    def complete(self, job_id: int, token: str, result: dict[str, Any]) -> dict[str, Any]:
        body = {"lease_token": token, "result": result}
        data: dict[str, Any] = self._call(
            "POST", f"/v1/jobs/{job_id}/complete", body, lease=True
        ).json()
        return data

    def fail(self, job_id: int, token: str, error: str, *, retryable: bool) -> dict[str, Any]:
        body = {"lease_token": token, "error": error[:4000], "retryable": retryable}
        data: dict[str, Any] = self._call(
            "POST", f"/v1/jobs/{job_id}/fail", body, lease=True
        ).json()
        return data

    def release(self, job_id: int, token: str) -> None:
        self._call("POST", f"/v1/jobs/{job_id}/release", {"lease_token": token}, lease=True)

    def code(self, code_sha: str) -> str:
        return str(self._call("GET", f"/v1/code/{code_sha}").json()["code"])

    def apk(self, sha256: str) -> dict[str, Any]:
        data: dict[str, Any] = self._call("GET", f"/v1/apk/{sha256}").json()
        return data

    def upload_url(self, job_id: int, token: str, attempt: int, name: str) -> dict[str, Any]:
        body = {"job_id": job_id, "lease_token": token, "attempt": attempt, "name": name}
        data: dict[str, Any] = self._call(
            "POST", "/v1/artifacts/upload-url", body, lease=True
        ).json()
        return data

    # --- signed storage URLs (no token) ---------------------------------------------------

    def upload(self, target: dict[str, Any], path: Path) -> None:
        with path.open("rb") as handle:
            response = self.storage.request(
                str(target.get("method", "PUT")),
                str(target["url"]),
                files={str(target.get("form_field", "file")): (path.name, handle)},
            )
        if response.status_code >= 400:
            raise ApiError(response.status_code, "upload_failed", f"upload of {path.name} failed")

    def download(self, url: str, dest: Path) -> None:
        with self.storage.stream("GET", url) as response:
            if response.status_code >= 400:
                raise ApiError(response.status_code, "download_failed", "download failed")
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
