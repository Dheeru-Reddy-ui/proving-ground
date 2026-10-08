# ADR-0010: Hosting the control plane on free tiers

- **Status:** Accepted (approved by Dheeru on 2026-10-08 with the Phase 2 plan)
- **Date:** 2026-10-08
- **Deciders:** Dheeru (owner)

## Context

The Phase 2 plan names Supabase (Postgres and Storage) and Render (an API web service and a background worker). CLAUDE.md asks before adding any paid service, and Dheeru prefers free tiers. The providers' documentation, checked on 2026-10-08, says:

| Fact | Source |
|---|---|
| Render offers Free instances only for web services, Postgres and Key Value; background workers, cron jobs and private services have none | [render.com/docs/free](https://render.com/docs/free) |
| A Free web service spins down after 15 minutes without inbound traffic and takes about a minute to wake; each workspace gets 750 Free instance hours a month | same |
| Render's pre-deploy command is available only on paid services; a deploy hook URL triggers a deploy with a GET or POST | [render.com/docs/deploys](https://render.com/docs/deploys) |
| Supabase Storage on the Free plan accepts files up to 50 MB; the limit cannot be raised on Free | [storage file limits](https://supabase.com/docs/guides/storage/uploads/file-limits) |
| Supabase's direct database connection is IPv6-only unless the paid IPv4 add-on is bought; the shared pooler is IPv4 in session mode (port 5432, prepared statements work) and transaction mode (port 6543, no prepared statements) | [connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres) |
| Supabase pauses Free projects with low activity over 7 days | [project pausing](https://supabase.com/docs/guides/platform/free-project-pausing) |
| Supabase Storage can mint signed upload URLs, valid for 2 hours, that upload without further authentication | [createSignedUploadUrl](https://supabase.com/docs/reference/javascript/storage-from-createsigneduploadurl) |

Our hooked APK (build 2) is 64.1 MB (`docs/VERSIONS.md`).

Limits these pages do not settle (instance memory, pooler connection counts, total storage) are for Dheeru to check on the current pricing pages; we never state them from memory.

## Decision

1. **One Render Free web service runs the API, the dashboard and the worker.** With `PG_EMBEDDED_WORKER=true`, `pg_api` starts the `pg_worker` loop in a background thread at startup. The same loop runs standalone as `pg worker run`, so moving it to a paid background worker or to the PC needs no code change. Jobs are leased (ADR-0006), so a second worker anywhere is safe.
2. **Migrations run in the release workflow**, against `DATABASE_URL` from a GitHub Actions secret, before the workflow calls Render's deploy hook. Render's auto-deploy is off, so code never runs ahead of its schema. The app never migrates at import or start-up; `/readyz` reports not-ready when the database is behind.
3. **APKs are stored in parts.** `pg build register` hashes the APK, splits it into parts of at most 45 MB, uploads each part to a private bucket through signed upload URLs and records a manifest (part keys, part sha256s, whole-file sha256, size). The agent downloads the parts through signed URLs, joins them, and installs only if the whole-file sha256 matches the build. APKs are never attached to a GitHub release: the repository is public and the APK contains Asset Store content (CLAUDE.md rule 8, ADR-0003).
4. **The database is reached through the Supabase session pooler** (IPv4, prepared statements supported), with small connection pools in every process.
5. **The cloud image has no AltTester driver.** It moves to a `device` dependency group installed only on the PC: its licence forbids redistribution (ADR-0003), and the cloud never talks to a device. `pg_core`, `pg_generator` and `pg_db` do not import it (checked 2026-10-08).
6. **Observability stays inside the service:** an `llm_calls` table instead of Langfuse; `/metrics` computed on request from the jobs, agents and executions tables (nothing scrapes on a free tier); alerts posted to an optional webhook URL.
7. **Keep-alive:** a weekly scheduled workflow calls `/readyz`, which wakes the web service and queries the database, so the public demo's project is not paused for inactivity.
8. **Public demo data:** the local Phase 1 database is copied into Supabase once (schema by Alembic, data by `pg_dump --data-only`), so the demo shows generation runs 5 and 6 under the same IDs the README report cites. The local database is not modified.

## Alternatives considered

| Option | Why not chosen |
|---|---|
| A paid Render background worker | Paid; the free design meets the Phase 2 goals. It remains the first upgrade if the embedded worker becomes a problem. |
| The worker on the PC next to the agent | Keeps the LLM key off Render, but then nothing in the cloud generates or scores, and the PC becomes a single point for both. |
| Supabase Pro for 500 GB uploads | Paid. Splitting the file is a little code and keeps a whole-file hash check. |
| The agent installs only from a local build cache | No download path, so a build registered by the webhook could not be installed unless its APK already sat on the PC; weaker test of the platform. |
| Render pre-deploy command for migrations | Paid only. |
| Migrate at app start-up | The plan forbids it; two instances starting together could race. |
| Langfuse for LLM traces | Another service holding prompts; the `llm_calls` table answers the same questions. |

## Consequences

- **Cold starts:** the first request after 15 idle minutes waits about a minute. The README says so next to the demo link. The agent's polling keeps the service awake while it runs.
- **Shared fate:** a crash in the worker thread or the API takes down both. The worker thread is supervised (restarted with backoff, logged) and `/readyz` reports a dead worker.
- **One always-on Free service per workspace** fits in the monthly hours; a second would not.
- **Free-plan facts can change.** If a provider changes a limit we rely on, we stop and ask (BUILD_PLAN Phase 2, "Stop and ask me if").
- **Release secrets live in GitHub Actions** (`DATABASE_URL`, the webhook HMAC secret, the deploy hook URL) as well as in Render's environment; approved by Dheeru on 2026-10-08.

## References

- `docs/BUILD_PLAN.md`, Phase 2
- ADR-0002 (free plan), ADR-0003 (licences), ADR-0005 (agent pulls), ADR-0006 (Postgres queue)
