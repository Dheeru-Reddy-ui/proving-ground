"""Post a build registration to the control plane's webhook, signed (used by release.yml).

Reads the release's `build.json` (a `pg_core.builds.BuildRegistration`: sha256, label, locator
tag, patch notes and the APK parts manifest; never the APK itself, which stays in private
storage), signs `"<timestamp>.<body>"` with HMAC-SHA256 and posts it. The delivery id comes from
the workflow run, so a re-run of the same run is deduplicated by the API.

Environment: PG_API_URL, PG_WEBHOOK_SECRET, DELIVERY_ID. Never prints the secret.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

from pg_core.builds import BuildRegistration
from pg_core.webhook import signature

ATTEMPTS = 5


def main(path: str) -> int:
    body = Path(path).read_bytes()
    BuildRegistration.model_validate_json(body)  # refuse a malformed build.json before sending
    url = os.environ["PG_API_URL"].rstrip("/") + "/v1/webhooks/github"
    secret = os.environ["PG_WEBHOOK_SECRET"]
    delivery = os.environ["DELIVERY_ID"]
    for attempt in range(1, ATTEMPTS + 1):
        stamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-PG-Timestamp": stamp,
            "X-PG-Signature": signature(secret, stamp, body),
            "X-PG-Delivery": delivery,
        }
        try:
            response = httpx.post(url, content=body, headers=headers, timeout=120)
        except httpx.HTTPError as exc:  # a sleeping free instance can take a minute to wake
            print(f"attempt {attempt}: {type(exc).__name__}")
        else:
            print(f"attempt {attempt}: HTTP {response.status_code} {response.text[:500]}")
            if response.status_code < 300:
                return 0
            if response.status_code < 500 and response.status_code != 429:
                return 1
        time.sleep(min(60, 5 * 2**attempt))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
