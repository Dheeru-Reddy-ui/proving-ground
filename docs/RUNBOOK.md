# Runbook

How to operate Proving Ground's device agent and control plane. Commands run from the repository root on the device host (Dheeru's Windows PC) unless stated otherwise.

## First deployment (once)

Free tiers only (ADR-0010). Check each provider's current limits yourself before relying on them.

### 1. Supabase (database and storage)

1. Create a project on the Free plan.
2. **Storage → New bucket:** name `proving-ground`, **private** (not public).
3. **Database connection string:** use the **session pooler** URI (IPv4, port 5432; the direct connection is IPv6-only on Free). This is the production `DATABASE_URL`.
4. **API settings:** note the project URL (`SUPABASE_URL`) and the **service_role** key (`SUPABASE_SERVICE_KEY`). The service key goes only to Render; never to the PC's `.env`, never to the agent.

### 2. Schema and the Phase 1 data

From the repository on the PC, with the Supabase URL in a shell variable (not in `.env`):

```bash
DATABASE_URL="<supabase session pooler URI>" uv run alembic upgrade head
```

Copy the Phase 1 results (runs 5 and 6 and everything they reference) so the demo shows them under the same IDs. This reads the local database and writes only to the empty Supabase one:

```bash
docker exec pg-proving-ground pg_dump -U pg -d proving_ground --data-only --no-owner --no-privileges -t builds -t specs -t bugs -t generation_runs -t candidates -t executions -t kills -t suite_tests > artifacts/phase1_data.sql
```

```bash
docker run --rm -i postgres:16 psql "<supabase session pooler URI>" -v ON_ERROR_STOP=1 < artifacts/phase1_data.sql
```

Run these two in Git Bash (PowerShell has no `<` redirection). `pg_dump --data-only` also restores the sequences, so new rows continue after the copied IDs.

### 3. Render (API, dashboard and worker in one free web service)

1. **New Web Service** from this GitHub repository, runtime **Docker** (it builds `Dockerfile`), instance type **Free**, health check path `/healthz`.
2. Turn **automatic deploys off**: the release workflow migrates first, then calls the deploy hook.
3. Copy the service's **Deploy Hook URL** (Settings).
4. Environment variables:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Supabase session pooler URI |
   | `PG_SESSION_SECRET` | 32+ random characters |
   | `PG_ADMIN_PASSWORD_HASH` | output of `uv run pg api hash-password` |
   | `PG_WEBHOOK_SECRET` | 32+ random characters (also a GitHub secret) |
   | `PG_PUBLIC_DEMO` | `true` |
   | `PG_SECURE_COOKIES` | `true` |
   | `PG_PUBLIC_BASE_URL` | `https://<service>.onrender.com` |
   | `PG_STORAGE_BACKEND` | `supabase` |
   | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | from step 1 |
   | `PG_STORAGE_BUCKET` | `proving-ground` |
   | `PG_EMBEDDED_WORKER` | `true` |
   | `PG_WORKER_NAME` | `render-worker` |
   | `PG_LLM_PROVIDER`, `PG_LLM_MODEL`, `PG_LLM_API_KEY` | as in the PC's `.env` (`gemini`, `gemini-3.5-flash`, the key) |
   | `PG_LLM_INPUT_USD_PER_MTOK`, `PG_LLM_OUTPUT_USD_PER_MTOK` | `0` on the free tier |
   | `PG_MAX_COST_PER_RUN_USD`, `PG_MAX_COST_PER_BUILD_USD`, `PG_MAX_COST_PER_DAY_USD` | e.g. `1.00`, `2.00`, `5.00` |
   | `PG_ALERT_WEBHOOK_URL` | optional: a Slack or Discord incoming-webhook URL |

   The service refuses to start and names the variable if one is missing or invalid.

### 4. GitHub

- **Actions secrets:** `DATABASE_URL` (Supabase), `PG_WEBHOOK_SECRET`, `RENDER_DEPLOY_HOOK_URL`.
- **Actions variable:** `PG_API_URL` = `https://<service>.onrender.com`.
- Deploy: push a tag `v0.2.0`; `release.yml` builds the image, migrates, calls the deploy hook and waits for `/readyz`.

### 5. The PC

1. Admin token for the CLI (writes to the Supabase database):

   ```bash
   DATABASE_URL="<supabase session pooler URI>" uv run pg api admin-token --name dheeru-pc-cli
   ```

   Put it in `.env` as `PG_API_TOKEN`, with `PG_API_URL=https://<service>.onrender.com`.
2. Enrol the agent: create an enrolment token on the dashboard's System page (log in first), then `uv run pg agent enroll --api https://<service>.onrender.com --name dheeru-pc` (below).

### 6. Register builds and validate

- **CLI path:** `uv run pg build register --apk <apk> --label "<label>" --locator-tag 87d396162a05`
- **Webhook path:** `uv run pg build upload --apk <apk> --label "<label>" --locator-tag 87d396162a05`, then create a GitHub release tagged `build-<n>` with the written `build.json` attached (any name matching `build*.json`, exactly one; never the APK); `release.yml` posts it, signed.
- **Validate:** `uv run pg build validate --build <id> --feature store --feature run_and_gameover --n 8`, then watch it on the dashboard.

## Before any device work

The agent checks these before every claim and reports what fails (`pg agent status` shows the same checks):

1. **Phone attached** over USB and authorised for adb (`adb devices -l` lists it; its serial matches `PG_ADB_SERIAL` in `.env`).
2. **The game is installed** (`PG_ANDROID_PACKAGE`).
3. **The reverse forward exists** (`adb reverse --list` shows `tcp:13000`). The test plugin recreates it before each launch if it vanished.
4. **AltTester Desktop is running with its server started** on port 13000, and its **inspector is closed** (the free plan has one driver slot, ADR-0002).
5. **The repository is up to date** (`git pull`). The agent refuses jobs planned with a different SDK manifest, and needs the locator map of each build.

## Enrol the agent (once per PC)

1. Where the control plane's `DATABASE_URL` is set, create a one-time enrolment token (valid 24 h by default):

   ```
   uv run pg api enrolment-token
   ```

2. On the device host, enrol and paste the token when asked (it is not echoed):

   ```
   uv run pg agent enroll --api https://<control-plane-url> --name dheeru-pc
   ```

   The agent's own token is stored in `%LOCALAPPDATA%\proving-ground\agent.json`. The file's ACL is reduced to your Windows user only (`icacls /inheritance:r /grant:r USER:F`), checked by `tests/unit/test_agent.py`. The server keeps only the token's sha256.

3. Check it: `uv run pg agent status` prints the agent's name, the API URL (never the token) and the health checks.

## Run the agent

```
uv run pg agent run
```

- It polls for work every 5 s when idle, runs one job at a time and prints one JSON log line per event.
- **Ctrl+C once:** it finishes the current job and stops. **Ctrl+C twice:** it stops now and reports the job as failed (retryable), so it runs again later.
- After an infra failure (phone gone, AltTester down) it runs the full `pg doctor`, claims nothing while unhealthy, and checks again every 60 s. The dashboard's System page shows its last report.

### Start it at login

The agent must run in your logged-in session, because AltTester Desktop is a GUI app.

**Startup folder (simplest).** Press Win+R, type `shell:startup`, Enter. In that folder create a shortcut to `ops\windows\start-agent.cmd` in the repository. The agent then starts in its own console window each time you log in; close it or press Ctrl+C to stop it.

**Task Scheduler (alternative).** In a terminal, with the repository at `C:\Users\dheer\proving_ground`:

```
schtasks /create /tn "Proving Ground agent" /tr "C:\Users\dheer\proving_ground\ops\windows\start-agent.cmd" /sc onlogon /it /rl limited
```

`/sc onlogon` starts it at every logon, `/it` only while you are logged in, `/rl limited` without admin rights ([schtasks create](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create)). Remove it with `schtasks /delete /tn "Proving Ground agent"`.

## Recover a stuck device

Symptoms: the System page shows the agent unhealthy or not seen for more than 10 minutes, RUN_TEST jobs stay queued, or jobs die with `infra on every attempt`.

1. Run `uv run pg agent status` and fix the first failing check:

   | Failing check | Fix |
   |---|---|
   | adb device | Reconnect the USB cable; accept the "Allow USB debugging" prompt; `adb kill-server` then `adb start-server`. |
   | reverse forward | `adb reverse tcp:13000 tcp:13000` |
   | AltTester Desktop | Start AltTester Desktop, or restart its server. It stops its server when a licence check loses the internet and does not restart it (`docs/VERSIONS.md`). |
   | app connects (full check) | Close the AltTester inspector. Unlock the phone. If Android shows the "16 KB-compatible" dialog, tap OK (ADR-0008). `adb shell am force-stop <package>`. |
   | game installed | Reinstall the build: retry its INSTALL_BUILD job, or `adb install -r <apk>`. |

2. If the previous agent process crashed, a test runner may still hold AltTester's only driver slot. The agent kills orphaned runners when it starts, so restarting it is enough. Otherwise end the leftover `python ... -p pg_sdk.pytest_plugin` process in Task Manager.
3. Jobs that died while the device was down: retry them from the System page, or `POST /v1/jobs/{id}/retry` with an admin token. A dead RUN_TEST repeat is also replaced automatically by an extra repeat (up to 2), as in `pg prove`.

## Rotate or revoke the agent's token

Where the control plane's `DATABASE_URL` is set:

- **Rotate** (routine): `uv run pg api rotate-agent-token --agent dheeru-pc` prints a new token and revokes the old ones at once. On the device host: `uv run pg agent set-token` and paste it.
- **Revoke** (suspected leak): `uv run pg api revoke-agent --agent dheeru-pc`. Every token of that agent stops working immediately. Enrol again with a new name and a new enrolment token.

## Where things are on the device host

| What | Where |
|---|---|
| Agent credentials | `%LOCALAPPDATA%\proving-ground\agent.json` (your user only) |
| Downloaded APKs | `%LOCALAPPDATA%\proving-ground\Cache\apks\<sha256>.apk` |
| Run artifacts (local copies) | `artifacts\agent\job-<id>-try-<n>\` in the repository (gitignored) |
| Uploaded artifacts | the private storage bucket, reached through the dashboard (admin only) |
