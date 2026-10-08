"""Object storage for APK parts and run artifacts, behind signed URLs (ADR-0010).

The agent never holds a storage key: the API mints a short-lived URL per object and the agent
uploads or downloads with plain HTTP. Two backends with the same contract:

- `SupabaseStorage`: Supabase Storage's REST API (private bucket, service key on the API only).
  Signed uploads: `POST /object/upload/sign/{bucket}/{key}` returns a URL carrying a token; the
  client then `PUT`s the file there as multipart form field `file`. Signed downloads:
  `POST /object/sign/{bucket}/{key}` with `{"expiresIn": seconds}` returns `signedURL`.
  (Calls as made by Supabase's own Python client, `storage3/_sync/file_api.py`, read 2026-10-08.)
- `LocalStorage`: files under a directory, served by this API at `/v1/local-storage/...` with
  HMAC-signed, expiring URLs. For development and tests only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, ConfigDict

KEY_PATTERN = re.compile(r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._/-]{0,250}$")
UPLOAD_TTL_S = 2 * 3600  # Supabase signed upload URLs are valid for 2 hours
HTTP_TIMEOUT_S = 20.0


class StorageError(RuntimeError):
    pass


def check_key(key: str) -> str:
    if not KEY_PATTERN.match(key) or key.endswith("/") or "//" in key:
        raise StorageError(f"invalid storage key {key!r}")
    return key


class UploadTarget(BaseModel):
    """How the agent uploads one object: `method` the file to `url` as form field `form_field`."""

    model_config = ConfigDict(frozen=True)

    key: str
    url: str
    method: str = "PUT"
    form_field: str = "file"


class Storage(Protocol):
    def upload_target(self, key: str) -> UploadTarget: ...

    def download_url(self, key: str, expires_s: int) -> str: ...

    def exists(self, key: str) -> bool: ...


class SupabaseStorage:
    def __init__(
        self,
        supabase_url: str,
        service_key: str,
        bucket: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.base = supabase_url.rstrip("/") + "/storage/v1/"
        self.bucket = bucket
        self.client = client or httpx.Client(timeout=HTTP_TIMEOUT_S)
        self._headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    def _path(self, key: str) -> str:
        return f"{self.bucket}/{quote(check_key(key))}"

    def _post(self, path: str, json: dict[str, object] | None = None) -> dict[str, object]:
        response = self.client.post(self.base + path, headers=self._headers, json=json)
        if response.status_code >= 400:
            # never include headers (the service key) in the message
            raise StorageError(f"storage answered {response.status_code} for {path.split('?')[0]}")
        data = response.json()
        if not isinstance(data, dict):
            raise StorageError("unexpected storage response")
        return data

    def upload_target(self, key: str) -> UploadTarget:
        data = self._post(f"object/upload/sign/{self._path(key)}")
        url = str(data.get("url") or "")
        if "token=" not in url:
            raise StorageError("storage returned no upload token")
        return UploadTarget(key=key, url=self.base + url.lstrip("/"))

    def download_url(self, key: str, expires_s: int) -> str:
        data = self._post(f"object/sign/{self._path(key)}", json={"expiresIn": expires_s})
        signed = str(data.get("signedURL") or data.get("signedUrl") or "")
        if not signed:
            raise StorageError("storage returned no signed URL")
        return self.base + signed.lstrip("/")

    def exists(self, key: str) -> bool:
        response = self.client.head(self.base + f"object/{self._path(key)}", headers=self._headers)
        return response.status_code == 200


class LocalStorage:
    """Files on disk with HMAC-signed URLs served by `pg_api.routes.local_storage`."""

    def __init__(self, root: Path, base_url: str, secret: str, clock: Callable[[], float]) -> None:
        self.root = root
        self.base_url = base_url.rstrip("/")
        self._secret = secret.encode("utf-8")
        self.clock = clock

    def sign(self, op: str, key: str, expires: int) -> str:
        message = f"{op}\n{key}\n{expires}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def verify(self, op: str, key: str, expires: int, signature: str) -> bool:
        if expires < self.clock():
            return False
        return hmac.compare_digest(self.sign(op, key, expires), signature)

    def _url(self, op: str, key: str, ttl_s: int) -> str:
        expires = int(self.clock()) + ttl_s
        query = urlencode({"op": op, "expires": expires, "sig": self.sign(op, key, expires)})
        return f"{self.base_url}/v1/local-storage/{quote(check_key(key))}?{query}"

    def upload_target(self, key: str) -> UploadTarget:
        return UploadTarget(key=key, url=self._url("put", key, UPLOAD_TTL_S))

    def download_url(self, key: str, expires_s: int) -> str:
        return self._url("get", key, expires_s)

    def path(self, key: str) -> Path:
        target = (self.root / check_key(key)).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise StorageError("key escapes the storage directory")
        return target

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def write(self, key: str, data: bytes) -> None:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
