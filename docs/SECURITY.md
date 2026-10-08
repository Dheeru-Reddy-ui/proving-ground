# Security and threat model

Proving Ground runs code written by a language model on a PC that holds a phone, and exposes a small control plane on the internet. This page lists what can go wrong, what stops it, and what risk remains. It covers Phase 2 (control plane, worker, device agent, dashboard).

## What is worth protecting

| Asset | Where it lives |
|---|---|
| The device host (Dheeru's Windows PC) and its files | The agent and the test runner run there as Dheeru's user |
| Gate verdicts and their evidence (executions, kills, human reviews) | Postgres (Supabase) |
| Secrets: database URL, Supabase service key, LLM key, webhook secret, admin password hash, session secret, agent and admin tokens | Environment variables on Render and in the PC's `.env`; GitHub Actions secrets for release jobs; the agent's token in `%LOCALAPPDATA%\proving-ground\agent.json` |
| Artifacts (screenshots, game logs, logcat, pytest output) and APK parts | Private Supabase Storage bucket |
| The holdout split | `benchmark/bugs.yaml`; never in a prompt, never run before Phase 4 |

## Trust boundaries

```
internet ──HTTPS──► pg_api (Render) ──► Postgres, Storage (Supabase)
                         ▲
   device agent (PC) ────┘ outbound HTTPS only (ADR-0005)
        │ pytest subprocess: LLM-written test code
        └── adb, AltTester Desktop (unencrypted WebSocket on the PC, ADR-0002) ── phone
```

Everything the language model writes is untrusted. Everything a device agent reports is trusted only as far as that agent is.

## Threats and mitigations

### 1. LLM-written code runs on the device host

A generated test could try to read files, open sockets, start processes or exfiltrate secrets.

- **Static gate G1** (`pg_core/gates/static.py`, ADR-0004) parses the code and never imports or runs it. One test function; imports limited to `pytest` and public `pg_sdk` names; no `exec`, `eval`, `compile`, `open`, `__import__`, `getattr`/`setattr`, `globals`, `vars`, no `_`-prefixed name or attribute, no `try`, nested functions, classes or `async`; every call on `game` must resolve to the SDK manifest. 46 adversarial cases in `tests/unit/test_gate_static.py`.
- **The SDK is the only surface.** `pg_sdk` exposes player intents (open the store, buy, read a balance); none touches the PC's file system or network beyond AltTester.
- **G1 runs twice.** The worker runs it before any job is planned; the agent runs it again on the code it downloads (`pg_agent/executor.py`), refuses code whose sha256 differs from the job's, and refuses jobs planned with a different SDK manifest.
- **Sandboxed subprocess** (`pg_runner/runner.py`): a fresh temporary working directory, an environment reduced to an allow-list (no API keys, no `DATABASE_URL`, no tokens), a wall-clock timeout that kills the whole process tree, and never `exec`/`eval` in the agent's own process (CLAUDE.md rule 5).

**Residual risk.** The subprocess runs as Dheeru's Windows user, so a test that got past G1 could read what that user can read, including the agent's token file. G1 is the real barrier; the subprocess limits accidents more than attacks. A separate low-privilege Windows account for the runner would close this; it is not done in Phase 2.

### 2. A device agent's token leaks

- **Scope:** an agent token reaches agent endpoints only (`/v1/agents/*`, `/v1/jobs/*`, `/v1/code/*`, `/v1/apk/*`, `/v1/artifacts/*`). It cannot read the dashboard's admin data, start validations, review candidates or touch the database directly.
- **Leases are fenced:** a job can be completed only with the lease token issued to the agent that claimed it, and only by that agent (`lease_owner`); artifact keys in a result must belong to that job.
- **Storage:** tokens are 256 random bits, stored only as sha256; the PC's copy sits in a file whose ACL grants only Dheeru's user (`icacls /inheritance:r /grant:r USER:F`, tested).
- **Response:** `pg api revoke-agent --agent NAME` ends every token of that agent at once; `pg api rotate-agent-token` for routine rotation (RUNBOOK.md).

**Residual risk.** Whoever holds a valid agent token can report false results and so sway gate decisions for the jobs it claims. Every job keeps its `lease_owner`, so results can be traced to an agent and discarded after a revocation.

### 3. Forged or replayed build-registration webhooks

- HMAC-SHA256 over `"<timestamp>.<raw body>"` with a shared secret, compared in constant time (`pg_core/webhook.py`).
- Timestamps more than five minutes away from the server clock are refused, so a captured request cannot be replayed later; inside the window, a repeated delivery id changes nothing and registration is idempotent on the APK hash.
- The body is validated strictly (unknown fields refused); the locator map must exist; an APK manifest is accepted only if every part is already in private storage, which only the admin can upload to.
- Forged and stale requests answer 401 and are logged with the reason; the webhook route is rate-limited per client.
- If `PG_WEBHOOK_SECRET` is unset the route answers 503 rather than accepting unsigned requests.

### 4. Prompt injection through specs or game text

- Prompts carry only the feature specs (committed, reviewed by Dheeru), the SDK manifest, a fixed game summary and the names of accepted tests. The bug catalog, flag names and holdout data never reach a prompt (`tests/unit/test_prompt_leak.py`, and the worker's own prompts are checked in `tests/integration/test_worker.py`).
- Whatever the model returns is parsed as strict JSON and treated as untrusted code (threat 1). An instruction smuggled into a spec could at worst make the model write bad tests, which the gates reject or send to a human.
- Phase 3's crawler will read text from the game: it is data, and any prompt that includes it must quote and label it as untrusted.

### 5. Artifact and data privacy

- The storage bucket is private. Every object is reached through a signed URL minted by the API: 2 hours to upload, 1 hour for an agent to download APK parts, 5 minutes for the admin to view an artifact.
- **Public demo mode** shows builds, decisions, gate reasons and code, but no artifacts, no human review reasons and no agent health detail; failure details are cut to one line.
- Home-directory paths (which carry the PC's user name) are removed from every page and API view.
- Game screenshots are imagery of the Unity sample; they stay out of the repository and the public demo until Dheeru decides otherwise (Phase 0 decision).

### 6. The admin dashboard

- One admin. The password is checked against an argon2 hash from the environment; there are no stored user accounts.
- The session cookie is signed, HttpOnly, SameSite=Strict and Secure in production, and lasts 8 hours.
- Every state-changing request needs the session's CSRF token (form field or `X-CSRF-Token` header); bearer-token API calls need no CSRF token because no cookie is involved.
- Login is rate-limited per client. Pages are served with a strict Content-Security-Policy (`default-src 'self'`, no inline scripts or styles, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`. Generated code is HTML-escaped (tested with a hostile candidate).

### 7. Secrets handling

- Secrets come only from the environment or `.env` (gitignored; `.env.example` lists every variable and is checked by a test).
- The cloud image is built from an allow-list (`.dockerignore`): no `.env`, no artifacts, no device code, no AltTester driver. Checked in CI (`integration.yml`, job "cloud image").
- Secrets are never logged: settings use `SecretStr`, error messages from storage never include headers, and the alert webhook URL is never printed.
- The test runner's scrubbed environment keeps secrets away from generated code.

### 8. Abuse and cost

- Per-client token-bucket rate limits on public pages, login and enrolment, the webhook and agent routes; request bodies over 2 MB are refused with 413.
- LLM spend is capped per generation run, per build and per UTC day; `PG_GENERATION_ENABLED=false` stops all generation at once.

### 9. Supply chain

- Python dependencies are pinned in `uv.lock` and installed with `--locked`.
- Base images are pinned by digest; uv itself by version and digest.
- htmx is vendored (2.0.11, 0BSD), its sha256 checked against jsDelivr's published hash before it was committed.
- A dependency vulnerability audit is part of Phase 4 (M4.5).

## Known gaps

- The rate limiter keeps its state in memory: one Render instance is fine; several would each allow the full rate (ADR-0010).
- How many proxies Render puts in `X-Forwarded-For` is not yet measured (`PG_TRUSTED_PROXY_HOPS`); until it is, rate limits key on the proxy's address.
- The runner is not isolated from Dheeru's user account (threat 1).
- AltTester Desktop's server is unencrypted and listens on all interfaces; keep the PC on a trusted network and allow it through the firewall for private networks only (ADR-0002).
